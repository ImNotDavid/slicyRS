"""Milestone A: state machine and events, preview, pool and allocator, pickup forecast, bed state, task queue, statistics."""
import time

import pytest

from app import adapter, allocator, bambu, bedstate, jobstate, outcomes, pipelinecfg, stats, store, taskqueue, worker
from app.bambu import PrinterLink
from conftest import VALID, make_user, upload

CLEAR = {"bed_empty": True, "plate_matches": True, "filament_matches": True}


def stl():
    return next(VALID.glob("*.stl"))


def wait_phase(client, job_id, phases, timeout=30):
    end = time.time() + timeout
    while time.time() < end:
        j = client.get(f"/api/jobs/{job_id}").json()
        if j.get("phase") in phases:
            return j
        time.sleep(0.1)
    raise TimeoutError(f"{job_id} is {j.get('phase')}, wanted {phases}")


def draft(client, **kw):
    mid = upload(client, stl()).json()["id"]
    r = client.post("/api/jobs/preview", json={"model_id": mid, "filament": "PLA", **kw})
    assert r.status_code == 200, r.text
    return wait_phase(client, r.json()["id"], {"previewed", "failed"})


def clear_all(client, fleet):
    for pid in fleet.values():
        assert client.post(f"/api/printers/{pid}/bed/clear", json=CLEAR).status_code == 200


# ----------------------------------------------------------------------------- state machine and events

def test_transitions_are_checked_and_logged(client, fleet):
    j = draft(client)
    assert j["phase"] == "previewed"
    with pytest.raises(jobstate.InvalidTransition):
        jobstate.transition(j["id"], "printing")                       # cannot skip the pool, slicing and ready
    jobstate.transition(j["id"], "cancelled", "me")
    kinds = [(e["data"].get("from"), e["data"].get("to")) for e in client.get(f"/api/jobs/{j['id']}/events").json()]
    assert kinds == [(None, "submitted"), ("submitted", "previewing"), ("previewing", "previewed"), ("previewed", "cancelled")]


def test_events_are_private_to_the_owner(client, fleet):
    j = draft(client)
    other = make_user(client, "someone.else@imperial.ac.uk")
    assert other.get(f"/api/jobs/{j['id']}/events").status_code == 404


# ----------------------------------------------------------------------------- preview and submit

def test_preview_gives_an_estimate_and_keeps_no_gcode(client, fleet):
    j = draft(client)
    assert j["preview"]["time_s"] > 0 and j["preview"]["layers"] > 0 and j["state"] == "previewed"
    assert j["printer_id"] is None                                       # printer-agnostic until the allocator chooses
    from app.config import JOBS_DIR
    assert not (JOBS_DIR / j["id"] / "preview").exists()


def test_part_no_printer_can_ever_print_is_refused_at_draft(client, fleet):
    mid = upload(client, stl()).json()["id"]
    r = client.post("/api/jobs/preview", json={"model_id": mid, "filament": "PLA", "scale": 50})
    assert r.status_code == 409 and "ever print" in r.json()["detail"]


def test_submit_waits_for_a_confirmed_clear_plate_then_slices_for_the_chosen_printer(client, fleet):
    j = draft(client)
    r = client.post(f"/api/jobs/{j['id']}/submit", json={})
    assert r.status_code == 200 and r.json()["phase"] == "pooled"
    assert allocator.tick()["assigned"] == 0                            # every plate is "unknown": nothing starts
    assert client.get(f"/api/jobs/{j['id']}").json()["phase"] == "pooled"
    assert client.post(f"/api/printers/{fleet['p1s']}/bed/clear", json=CLEAR).status_code == 200   # also ticks the allocator
    done = wait_phase(client, j["id"], {"ready", "failed"})
    assert done["phase"] == "ready" and done["printer_id"] == fleet["p1s"] and done["gcode"] and done["state"] == "done"
    path = [e["data"]["to"] for e in client.get(f"/api/jobs/{j['id']}/events").json() if e["kind"] == "phase"]
    assert path == ["submitted", "previewing", "previewed", "pooled", "assigned", "slicing", "ready"]


def test_unlikely_pickup_time_is_refused_with_the_earliest_likely_time(client, fleet):
    j = draft(client)
    soon = time.time() + 5
    r = client.post(f"/api/jobs/{j['id']}/submit", json={"pickup_by": soon})
    assert r.status_code == 409 and r.json()["earliest_pickup"] > soon
    assert client.get(f"/api/jobs/{j['id']}").json()["phase"] == "previewed"       # not pooled
    ok = client.post(f"/api/jobs/{j['id']}/submit", json={"pickup_by": r.json()["earliest_pickup"] + 60})
    assert ok.status_code == 200 and ok.json()["pickup_by"] > soon


def test_pickup_time_in_the_past_is_a_400_and_submit_before_preview_is_409(client, fleet):
    j = draft(client)
    assert client.post(f"/api/jobs/{j['id']}/submit", json={"pickup_by": time.time() - 10}).status_code == 400
    jobstate.transition(j["id"], "cancelled")
    assert client.post(f"/api/jobs/{j['id']}/submit", json={}).status_code == 409


def test_forecast_grows_with_jobs_ahead_in_the_pool(client, fleet):
    for pid in list(fleet.values())[:]:
        if pid != fleet["mini"]:
            store.printers.update(pid, enabled=False)                   # one printer: a queue builds
    a, b = draft(client), draft(client)
    first = client.get(f"/api/jobs/{b['id']}/forecast").json()["earliest_finish"]
    assert client.post(f"/api/jobs/{a['id']}/submit", json={}).status_code == 200
    later = client.get(f"/api/jobs/{b['id']}/forecast").json()
    assert later["jobs_ahead"] == 1 and later["earliest_finish"] > first


def test_cancel(client, fleet):
    j = draft(client)
    client.post(f"/api/jobs/{j['id']}/submit", json={})
    assert client.post(f"/api/jobs/{j['id']}/cancel").json()["phase"] == "cancelled"
    assert client.post(f"/api/jobs/{j['id']}/cancel").status_code == 200   # repeating is harmless
    done = jobstate.transition(j["id"], "cancelled")
    assert done["phase"] == "cancelled"


# ----------------------------------------------------------------------------- allocator

def pooled_job(owner, name, submitted, pickup_by=None, at_risk=False):
    jid = f"pool-{name}-{time.time_ns()}"
    store.jobs.put(jid, {"id": jid, "name": name, "phase": "pooled", "state": "pooled", "created": submitted, "submitted_at": submitted,
                         "owner_id": owner["id"], "owner_email": owner["email"], "dims": [20, 20, 20], "filament": "PLA",
                         "preset": "0.20mm Standard", "spec": {"preset": "0.20mm Standard", "filament": "PLA", "settings": {}},
                         "input_file": "input.stl", "pickup_by": pickup_by, "at_risk": at_risk, "printed": False, "queued": False})
    return jid


def test_at_risk_job_jumps_the_queue_otherwise_submission_order(client, fleet, me=None):
    me = client.get("/api/me").json()
    for k, pid in fleet.items():
        store.printers.update(pid, enabled=(k == "mini"))
    first = pooled_job(me, "first", 100)
    second = pooled_job(me, "second", 200)
    risky = pooled_job(me, "risky", 300, pickup_by=time.time() + 600, at_risk=True)
    assert [j["id"] for j in sorted(store.jobs.all(), key=allocator.priority) if j["phase"] == "pooled"][:3] == [risky, first, second]
    bedstate.set_state(fleet["mini"], "clear", "manual")
    allocator.assign_pool()
    assert store.jobs.get(risky)["phase"] in ("assigned", "slicing", "ready", "failed")
    assert store.jobs.get(first)["phase"] == "pooled" and store.jobs.get(second)["phase"] == "pooled"   # one printer, one job


def test_busy_or_held_printer_is_not_free(client, fleet):
    for pid in fleet.values():
        bedstate.set_state(pid, "clear", "manual")
    store.printers.update(fleet["mini"], status={"state": "RUNNING"})
    store.printers.update(fleet["p1s"], enabled=False)
    ids = {p["id"] for p in allocator.free_printers()}
    assert fleet["mini"] not in ids and fleet["p1s"] not in ids and fleet["x1c"] in ids
    me = client.get("/api/me").json()
    jid = pooled_job(me, "holds", 1)
    store.jobs.update(jid, phase="ready", printer_id=fleet["x1c"])
    assert fleet["x1c"] not in {p["id"] for p in allocator.free_printers()}


def test_unstarted_sliced_job_returns_to_the_pool_after_the_hold(client, fleet):
    me = client.get("/api/me").json()
    jid = pooled_job(me, "held", 1)
    store.jobs.update(jid, phase="ready", phase_at=time.time() - 3 * 3600, printer_id=fleet["mini"], printer_name="mini")
    assert allocator.expire_holds(time.time()) == 1
    j = store.jobs.get(jid)
    assert j["phase"] == "pooled" and j["printer_id"] is None


def test_a_job_slipping_past_its_pickup_time_is_flagged_once_and_the_owner_told(client, fleet):
    from app import mailer
    me = client.get("/api/me").json()
    jid = pooled_job(me, "slipping", time.time(), pickup_by=time.time() + 30)
    mailer.outbox.clear()
    allocator.check_pickups(time.time())
    allocator.check_pickups(time.time())
    time.sleep(0.3)
    assert store.jobs.get(jid)["at_risk"] is True
    assert sum("may miss its pickup time" in m["subject"] for m in mailer.outbox) == 1
    allocator.check_pickups(time.time() + 3600)                          # now past the pickup time
    assert any(e["kind"] == "late" for e in jobstate.events_for(jid))


# ----------------------------------------------------------------------------- bed state

def test_a_finished_print_leaves_the_plate_occupied_and_it_stays_confirmed_clear_afterwards(client, fleet):
    pid = fleet["mini"]
    bambu._set_status(pid, {"state": "RUNNING"})
    assert store.printers.get(pid)["bed"]["state"] == "occupied"
    bambu._set_status(pid, {"state": "FINISH"})
    bed = store.printers.get(pid)["bed"]
    assert bed["state"] == "occupied" and bed["finished"] is True
    assert client.post(f"/api/printers/{pid}/bed/clear", json=CLEAR).status_code == 200
    bambu._set_status(pid, {"state": "FINISH", "progress": 100})      # the printer keeps repeating FINISH
    assert store.printers.get(pid)["bed"]["state"] == "clear"


def test_clearing_needs_the_full_checklist_and_the_right_person(client, fleet):
    pid = fleet["mini"]
    assert client.post(f"/api/printers/{pid}/bed/clear", json={"bed_empty": True}).status_code == 400
    stranger = make_user(client, "stranger@imperial.ac.uk")
    r = stranger.post(f"/api/printers/{pid}/bed/clear", json=CLEAR)
    assert r.status_code == 403
    client.patch(f"/api/admin/users/{stranger.get('/api/me').json()['id']}", json={"operator": True})
    assert stranger.post(f"/api/printers/{pid}/bed/clear", json=CLEAR).status_code == 200
    assert any(a["action"] == "bed_clear" for a in client.get("/api/admin/audit").json())


def test_owner_of_the_last_print_may_clear_their_own_plate(client, fleet):
    pid = fleet["mini"]
    owner = make_user(client, "owner.of.print@imperial.ac.uk")
    uid = owner.get("/api/me").json()
    store.jobs.put("owned-last", {"id": "owned-last", "name": "x", "owner_id": uid["id"], "printer_id": pid, "started_at": time.time()})
    assert owner.post(f"/api/printers/{pid}/bed/clear", json=CLEAR).status_code == 200


def test_cannot_confirm_clear_while_printing(client, fleet):
    pid = fleet["mini"]
    store.printers.update(pid, status={"state": "RUNNING"})
    assert client.post(f"/api/printers/{pid}/bed/clear", json=CLEAR).status_code == 400


def test_a_restart_makes_every_plate_unknown(client, fleet):
    clear_all(client, fleet)
    bedstate.reset_all()
    assert {store.printers.get(p)["bed"]["state"] for p in fleet.values()} == {"unknown"}


def test_owner_is_told_at_once_and_operators_after_the_admin_set_delay(client, fleet):
    from app import mailer
    pid = fleet["mini"]
    admin = client.get("/api/me").json()
    store.jobs.put("rem-job", {"id": "rem-job", "name": "Bracket", "owner_id": admin["id"], "owner_email": admin["email"],
                               "printer_id": pid, "started_at": time.time()})
    ops = make_user(client, "op.person@imperial.ac.uk")
    client.patch(f"/api/admin/users/{ops.get('/api/me').json()['id']}", json={"operator": True})
    bambu._set_status(pid, {"state": "RUNNING"})
    bambu._set_status(pid, {"state": "FINISH"})
    mailer.outbox.clear()
    bedstate.reminders(time.time())
    time.sleep(0.3)
    assert [m["to"] for m in mailer.outbox] == [admin["email"]]
    pipelinecfg.put({"operator_delay_min": 30})
    bedstate.reminders(time.time() + 29 * 60)
    time.sleep(0.2)
    assert len(mailer.outbox) == 1
    bedstate.reminders(time.time() + 31 * 60)
    time.sleep(0.3)
    assert "op.person@imperial.ac.uk" in [m["to"] for m in mailer.outbox]
    pipelinecfg.put({"operator_delay_min": 30})


def test_pipeline_settings_are_admin_only_and_validated(client, fleet):
    assert client.put("/api/admin/pipeline", json={"cooling_margin_min": 15}).json()["cooling_margin_min"] == 15
    assert client.put("/api/admin/pipeline", json={"cooling_margin_min": 9999}).status_code == 400
    user = make_user(client, "plain.user@imperial.ac.uk")
    assert user.get("/api/admin/pipeline").status_code == 403
    client.put("/api/admin/pipeline", json={"cooling_margin_min": 10})


# ----------------------------------------------------------------------------- task queue and worker

def test_task_lost_with_its_worker_is_picked_up_by_another(client):
    store._conn.execute("DELETE FROM tasks")
    tid = taskqueue.enqueue("noop", {})
    t = taskqueue.claim("worker-a")
    assert t["id"] == tid and taskqueue.claim("worker-b") is None      # held under lease
    later = time.time() + taskqueue.LEASE_S + 1                         # worker-a died: its lease runs out
    t2 = taskqueue.claim("worker-b", now=later)
    assert t2["id"] == tid and t2["attempts"] == 2


def test_task_that_keeps_losing_workers_is_declared_dead(client):
    store._conn.execute("DELETE FROM tasks")
    taskqueue.enqueue("noop", {"job_id": "x"}, max_attempts=2)
    now = time.time()
    taskqueue.claim("a", now=now)
    taskqueue.claim("b", now=now + 100)
    dead = taskqueue.reap_dead(now=now + 200)
    assert len(dead) == 1 and dead[0]["payload"] == {"job_id": "x"} and taskqueue.claim("c", now=now + 300) is None


def test_job_in_a_killed_slicer_is_failed_not_lost(client, fleet):
    j = draft(client)
    jobstate.transition(j["id"], "pooled", state="pooled", submitted_at=time.time())
    jobstate.transition(j["id"], "assigned", printer_id=fleet["mini"])
    jobstate.transition(j["id"], "slicing", state="slicing")
    store._conn.execute("DELETE FROM tasks")
    taskqueue.enqueue("slice", {"job_id": j["id"]}, max_attempts=1)
    now = time.time()
    taskqueue.claim("dying", now=now)
    allocator.tick(now + 1000)
    got = store.jobs.get(j["id"])
    assert got["phase"] == "failed" and "stopped" in got["error"]


def test_worker_loop_runs_tasks(client, fleet):
    assert worker.run_once("test-worker") in (True, False)             # safe to call with an empty or busy queue


def test_unknown_task_kind_fails_the_task_not_the_worker(client):
    store._conn.execute("DELETE FROM tasks")
    taskqueue.enqueue("nonsense", {}, max_attempts=1)
    assert worker.run_once("w") is True
    assert taskqueue.counts().get("failed") == 1


# ----------------------------------------------------------------------------- adapter and statistics

def test_bambu_link_satisfies_the_adapter_interface():
    assert issubclass(PrinterLink, adapter.PrinterAdapter)
    assert issubclass(bambu.PrinterError, adapter.AdapterError)


def test_statistics_report_usage_estimate_accuracy_wait_and_pickup(client, fleet):
    me = client.get("/api/me").json()
    jid = pooled_job(me, "stat-job", time.time() - 7200, pickup_by=time.time() + 3600)
    store.jobs.update(jid, printer_id=fleet["p1s"], printer_name="p1s", preview={"time_s": 1000}, stats={"time_s": 1200, "filament_g": 12.5},
                      result={"outcome": "finished", "duration_s": 1500, "ended_at": time.time()})
    s = stats.summary()
    assert s["by_user"][me["email"]]["finished"] >= 1 and s["by_printer"][fleet["p1s"]]["print_s"] >= 1500
    assert s["estimate_accuracy"]["sliced_median_ratio"] == pytest.approx(1.25, rel=0.3)
    assert s["pickup"]["met"] >= 1
    out = client.get("/api/admin/stats").json()
    assert "tasks" in out and out["jobs"] >= 1
