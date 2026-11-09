"""Milestone A.6: opt-in plates shared by several jobs, across users."""
import time

import pytest

from app import allocator, bedstate, jobstate, outcomes, plates, slicer, stats, store
from conftest import make_user
from test_pipeline import CLEAR, draft, pooled_job, wait_phase


def only(fleet, key="p1s"):
    """Leave one printer enabled and confirmed clear, so every pooled job competes for it."""
    for k, pid in fleet.items():
        store.printers.update(pid, enabled=(k == key))
    bedstate.set_state(fleet[key], "clear", "manual")
    return fleet[key]


def submit(c, job, **kw):
    r = c.post(f"/api/jobs/{job['id']}/submit", json=kw)
    assert r.status_code == 200, r.text
    return r.json()


def two_users(client):
    return make_user(client, "ann@imperial.ac.uk"), make_user(client, "bob@imperial.ac.uk")


# ----------------------------------------------------------------------------- the rules

def test_footprints_are_packed_with_gaps_and_may_turn(client):
    bed = (180, 180, 180)
    assert plates.footprints_fit([(20, 20, 20)] * 6, bed)
    assert not plates.footprints_fit([(100, 100, 10)] * 2, bed)         # two 100 mm squares do not fit on 180 mm
    assert not plates.footprints_fit([(170, 100, 10)] * 2, bed)
    assert plates.footprints_fit([(60, 170, 10)] * 2, bed)              # a long part may be turned to fit the row
    assert not plates.footprints_fit([(200, 20, 10)], bed)


def test_only_opted_in_jobs_with_the_same_settings_and_colour_are_partners(client, fleet):
    me = client.get("/api/me").json()
    printer = store.printers.get(fleet["p1s"])
    lead = pooled_job(me, "lead", 100)
    mate = pooled_job(me, "mate", 110)
    stranger = pooled_job(me, "stranger", 120)
    other_preset = pooled_job(me, "fine", 130)
    red, blue = pooled_job(me, "red", 140), pooled_job(me, "blue", 150)
    store.jobs.update(lead, allow_batching=True)
    store.jobs.update(mate, allow_batching=True)
    store.jobs.update(other_preset, allow_batching=True, spec={"preset": "0.12mm Fine", "filament": "PLA", "settings": {}})
    store.jobs.update(red, allow_batching=True, colour="red")
    store.jobs.update(blue, allow_batching=True, colour="blue")
    pool = [j for j in store.jobs.all() if j["phase"] == "pooled"]
    got = {j["id"] for j in plates.find_partners(store.jobs.get(lead), printer, pool, time.time())}
    assert mate in got and red in got                                    # same settings, "any" colour matches red
    assert stranger not in got and other_preset not in got               # not opted in / different settings
    assert blue not in got                                               # red and blue cannot share a plate


def test_plate_limits_and_pickup_times_are_respected(client, fleet):
    me = client.get("/api/me").json()
    printer = store.printers.get(fleet["p1s"])
    ids = [pooled_job(me, f"p{i}", 100 + i) for i in range(5)]
    for i in ids:
        store.jobs.update(i, allow_batching=True, preview={"time_s": 600})
    pool = [j for j in store.jobs.all() if j["phase"] == "pooled"]
    lead = store.jobs.get(ids[0])
    from app import pipelinecfg
    pipelinecfg.put({"plate_max_parts": 3})
    try:
        assert len(plates.find_partners(lead, printer, pool, time.time())) == 2             # lead + 2 = 3 parts
    finally:
        pipelinecfg.put({"plate_max_parts": 6})
    store.jobs.update(ids[1], pickup_by=time.time() + 600)                                  # cannot wait for a longer print
    pool = [j for j in store.jobs.all() if j["phase"] == "pooled"]
    got = {j["id"] for j in plates.find_partners(lead, printer, pool, time.time())}
    assert ids[1] not in got


def test_a_job_waits_for_a_partner_until_it_runs_out_of_slack_or_time(client, fleet):
    me = client.get("/api/me").json()
    jid = pooled_job(me, "waiter", time.time())
    store.jobs.update(jid, allow_batching=True, preview={"time_s": 600})
    now = time.time()
    assert "another part" in plates.hold_reason(store.jobs.get(jid), now)                   # whenever: wait
    assert plates.hold_reason(store.jobs.get(jid), now + 25 * 3600) is None                 # past the 24 h maximum hold
    store.jobs.update(jid, pickup_by=now + 4 * 3600)
    assert plates.hold_reason(store.jobs.get(jid), now)                                     # plenty of time to spare
    store.jobs.update(jid, pickup_by=now + 1800)
    assert plates.hold_reason(store.jobs.get(jid), now) is None                             # no slack left: go alone
    store.jobs.update(jid, allow_batching=False)
    assert plates.hold_reason(store.jobs.get(jid), now) is None


# ----------------------------------------------------------------------------- the whole path

def test_two_users_share_a_plate_from_pool_to_result(client, fleet):
    ann, bob = two_users(client)
    printer = only(fleet)
    a, b = draft(ann), draft(bob)
    submit(ann, a, allow_batching=True)
    assert allocator.tick()["assigned"] == 0                         # alone and opted in: waits for a partner
    assert "share the plate" in ann.get(f"/api/jobs/{a['id']}").json()["waiting_for"]
    submit(bob, b, allow_batching=True)
    ja, jb = ann.get(f"/api/jobs/{a['id']}").json(), bob.get(f"/api/jobs/{b['id']}").json()
    lead, member = (ja, jb) if ja.get("members") else (jb, ja)
    assert lead["members"] == [member["id"]] and member["batch_lead"] == lead["id"]
    assert lead["printer_id"] == printer and member["printer_id"] == printer
    assert abs(lead["plate_share"] + member["plate_share"] - 1) < 0.01

    done = wait_phase(client, lead["id"], {"ready", "failed"})
    assert done["phase"] == "ready"
    mirrored = wait_phase(client, member["id"], {"ready"})
    assert mirrored["gcode"] == done["gcode"] and mirrored["state"] == "done"
    owner_of_member = ann if member["id"] == a["id"] else bob
    assert owner_of_member.get(f"/api/jobs/{member['id']}/gcode").status_code == 200       # files come from the lead

    # either owner may start the plate; both jobs print
    assert owner_of_member.post(f"/api/jobs/{member['id']}/print").status_code in (200, 409, 502)   # no live printer in tests
    jobstate.transition(lead["id"], "printing", "t", force=True, queued=True, printed=True,
                        remote_name="plate-abc123.gcode.3mf", started_at=time.time(), result=None)
    assert store.jobs.get(member["id"])["phase"] == "printing"
    assert store.jobs.get(member["id"])["remote_name"] == "plate-abc123.gcode.3mf"

    # the printer's report ends both, with one printer-side result for the plate
    outcomes.on_status(printer, {"state": "FINISH", "subtask_name": "plate-abc123"})
    for jid in (lead["id"], member["id"]):
        j = store.jobs.get(jid)
        assert j["phase"] == "printed" and j["result"]["outcome"] == "finished"
    summary = stats.summary()
    assert summary["by_printer"][printer]["jobs"] == 1                                       # one plate, one print
    assert abs(sum(u["filament_g"] for u in summary["by_user"].values() if u["finished"]) -
               store.jobs.get(lead["id"])["stats"]["filament_g"]) < 0.5                      # filament split between owners

    # any one owner clears the whole plate; the other is told
    bedstate.set_state(printer, "occupied", "printer", finished=True, finished_at=time.time())
    from app import mailer
    before = len(mailer.outbox)
    assert owner_of_member.post(f"/api/printers/{printer}/bed/clear", json=CLEAR).status_code == 200
    time.sleep(0.2)
    assert len(mailer.outbox) > before


def test_a_job_that_is_not_opted_in_never_shares(client, fleet):
    ann, bob = two_users(client)
    only(fleet)
    a, b = draft(ann), draft(bob)
    submit(ann, a, allow_batching=True)
    submit(bob, b)                                                   # not opted in
    assert not any(j.get("members") for j in store.jobs.all())
    assert store.jobs.get(b["id"])["phase"] in ("assigned", "slicing", "ready")
    assert store.jobs.get(a["id"])["phase"] == "pooled"              # the other job took the only printer


def test_cancelling_one_job_returns_the_others_to_the_pool(client, fleet):
    ann, bob = two_users(client)
    only(fleet)
    a, b = draft(ann), draft(bob)
    submit(ann, a, allow_batching=True)
    submit(bob, b, allow_batching=True)
    lead = next(j for j in store.jobs.all() if j.get("members"))
    wait_phase(client, lead["id"], {"ready"})
    member_id = lead["members"][0]
    owner = ann if store.jobs.get(member_id)["owner_email"].startswith("ann") else bob
    assert owner.post(f"/api/jobs/{member_id}/cancel").json()["phase"] == "cancelled"
    other = store.jobs.get(lead["id"])
    assert other["phase"] in ("pooled", "assigned", "slicing", "ready") and not other.get("members")
    assert store.jobs.get(member_id)["phase"] == "cancelled" and not store.jobs.get(member_id).get("batch_lead")


def test_a_plate_the_slicer_cannot_fit_drops_its_last_job_and_slices_again(client, fleet, monkeypatch):
    ann, bob = two_users(client)
    only(fleet)
    real = slicer.run
    calls = []

    def fussy(workdir, models, *args):
        calls.append(len(models) if isinstance(models, list) else 1)
        if isinstance(models, list) and len(models) > 1:
            raise slicer.SliceError("One of the plate is empty or has no object fully inside it.")
        return real(workdir, models, *args)

    monkeypatch.setattr(slicer, "run", fussy)
    a, b = draft(ann), draft(bob)
    calls.clear()                                                    # the previews also went through the slicer
    submit(ann, a, allow_batching=True)
    submit(bob, b, allow_batching=True)
    lead = next(j for j in store.jobs.all() if j.get("members"))
    last = lead["members"][-1]
    done = wait_phase(client, lead["id"], {"ready", "failed"})
    assert done["phase"] == "ready" and not done.get("members") and calls[:2] == [2, 1]
    dropped = store.jobs.get(last)
    assert dropped["phase"] in ("pooled", "assigned", "slicing") and not dropped.get("batch_lead")
    assert lead["id"] in dropped["no_batch_with"]                                            # never paired with them again


def test_expired_hold_dissolves_a_plate(client, fleet):
    ann, bob = two_users(client)
    only(fleet)
    a, b = draft(ann), draft(bob)
    submit(ann, a, allow_batching=True)
    submit(bob, b, allow_batching=True)
    lead = next(j for j in store.jobs.all() if j.get("members"))
    wait_phase(client, lead["id"], {"ready"})
    wait_phase(client, lead["members"][0], {"ready"})
    store.jobs.update(lead["id"], phase_at=time.time() - 3 * 3600)
    assert allocator.expire_holds(time.time()) == 1
    for jid in (lead["id"], lead["members"][0]):
        assert store.jobs.get(jid)["phase"] == "pooled" and not store.jobs.get(jid).get("batch_lead")
    assert not store.jobs.get(lead["id"]).get("members")


def test_a_plate_starts_by_itself_only_if_everyone_asked(client, fleet):
    me = client.get("/api/me").json()
    a, b = pooled_job(me, "a", 1), pooled_job(me, "b", 2)
    store.jobs.update(a, auto_start=True, members=[b])
    store.jobs.update(b, auto_start=False, batch_lead=a)
    assert plates.auto_start(store.jobs.get(a)) is False
    store.jobs.update(b, auto_start=True)
    assert plates.auto_start(store.jobs.get(a)) is True
