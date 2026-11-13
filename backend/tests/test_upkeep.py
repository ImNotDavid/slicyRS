"""Maintenance, "needs attention" (2.6) and the per-printer wait estimate (2.5)."""
import time
import uuid

from app import alerts, allocator, outcomes, store, upkeep
from conftest import VALID, make_user, upload


def _printer(**kw):
    pid = uuid.uuid4().hex[:8]
    store.printers.put(pid, {"id": pid, "name": "U", "model": "P1S", "enabled": True, "status": {"state": "IDLE"}, "bed": {"state": "clear"}, **kw})
    return pid


def _op(client, email):
    c = make_user(client, email)
    u = next(x for x in store.users.all() if x["email"] == email)
    store.users.update(u["id"], operator=True)
    return c


def test_maintenance_takes_a_printer_out_of_the_pool_with_a_note_everyone_sees(client):
    pid = _printer()
    assert pid in {p["id"] for p in allocator.free_printers()}
    op = _op(client, "up.op@imperial.ac.uk")
    r = op.post(f"/api/printers/{pid}/maintenance", json={"on": True, "note": "new nozzle, back Friday"})
    assert r.status_code == 200 and r.json()["unavailable"] == "In maintenance: new nozzle, back Friday"
    assert pid not in {p["id"] for p in allocator.free_printers()}
    member = make_user(client, "up.member@imperial.ac.uk")
    entry = next(p for p in member.get("/api/printers").json() if p["id"] == pid)
    assert entry["maintenance"]["note"] == "new nozzle, back Friday"
    assert member.post(f"/api/printers/{pid}/maintenance", json={"on": False}).status_code == 403
    assert op.post(f"/api/printers/{pid}/maintenance", json={"on": False}).json()["unavailable"] is None
    assert pid in {p["id"] for p in allocator.free_printers()}
    store.printers.delete(pid)


def test_maintenance_is_temporary_so_jobs_can_still_be_submitted(client, fleet):
    m = upload(client, VALID / "cube_binary.stl").json()
    for pid in fleet.values():
        upkeep.set_maintenance(pid, True, "all down", {"email": "t@x"})
    try:
        r = client.post("/api/assign", json={"model_id": m["id"], "filament": "PLA", "nozzle_mm": 0.4})
        cands = r.json()["candidates"]
        assert r.json()["chosen"] is None and any("In maintenance: all down" in c["blockers"] for c in cands)
        from app import forecast
        assert forecast.eligible_printers((20, 20, 20), "PLA")        # still printable once they are back
    finally:
        for pid in fleet.values():
            upkeep.set_maintenance(pid, False, "", {"email": "t@x"})


def test_a_failed_print_flags_the_printer_until_someone_marks_it_ready(client):
    pid = _printer(status={"state": "RUNNING", "subtask_name": "flag-me"})
    jid = uuid.uuid4().hex[:8]
    store.jobs.put(jid, {"id": jid, "name": "Flagged", "owner_email": "admin@imperial.ac.uk", "printer_id": pid, "printer_name": "U", "phase": "printing",
                         "printed": True, "remote_name": "flag-me.gcode.3mf", "started_at": time.time() - 100, "created": time.time() - 200, "state": "done"})
    outcomes.on_status(pid, {"state": "FAILED", "subtask_name": "flag-me", "layer_num": 4, "total_layer_num": 50})
    p = store.printers.get(pid)
    assert "failed" in upkeep.unavailable(p) and pid not in {x["id"] for x in allocator.free_printers()}
    member = make_user(client, "up.member2@imperial.ac.uk")
    assert member.post(f"/api/printers/{pid}/ready").status_code == 403
    assert client.post(f"/api/printers/{pid}/ready").json()["attention"] is None
    store.printers.delete(pid)


def test_an_error_mid_print_flags_the_printer(client):
    pid = _printer(status={"state": "RUNNING"})
    jid = uuid.uuid4().hex[:8]
    store.jobs.put(jid, {"id": jid, "name": "E", "owner_email": "admin@imperial.ac.uk", "printer_id": pid, "phase": "printing", "printed": True,
                         "started_at": time.time() - 100, "created": time.time() - 100})
    alerts.on_status(pid, "RUNNING", {"state": "RUNNING", "hms": [{"attr": 0x05000500, "code": 0x00010007, "timestamp": int(time.time())}]})
    assert upkeep.unavailable(store.printers.get(pid)).startswith("Needs attention: error 0500-0500-0001-0007")
    store.printers.delete(pid)
    store.jobs.update(jid, phase="printed")


def test_wait_estimate_counts_the_running_print_and_jobs_sent_to_the_printer(client):
    pid = _printer(status={"state": "RUNNING", "remaining_min": 30})
    jid = uuid.uuid4().hex[:8]
    store.jobs.put(jid, {"id": jid, "name": "Next", "printer_id": pid, "phase": "ready", "stats": {"time_s": 1200}, "created": time.time(), "dims": [1, 1, 1], "filament": "PLA"})
    w = upkeep.wait_s(store.printers.get(pid))
    assert 30 * 60 + 1200 <= w <= 30 * 60 + 1200 * 1.5            # the estimate may add the admin-set buffer
    upkeep.set_maintenance(pid, True, "", {"email": "t@x"})
    assert upkeep.wait_s(store.printers.get(pid)) is None
    store.jobs.update(jid, phase="cancelled")
    store.printers.delete(pid)
