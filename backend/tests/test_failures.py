"""Failure learning (2.13): causes from owners and operators, patterns, staff-only flags."""
import time
import uuid

from app import failures, store
from conftest import make_user


def _ended(pid, outcome, owner=None, filament="PETG", ago=3600, **kw):
    jid = uuid.uuid4().hex[:8]
    end = time.time() - ago
    store.jobs.put(jid, {"id": jid, "name": "f", "printer_id": pid, "printer_name": "p", "phase": "printed" if outcome == "finished" else "failed",
                         "filament": filament, "owner_id": (owner or {}).get("id"), "owner_email": (owner or {}).get("email"), "bed_type": "Textured PEI Plate",
                         "started_at": end - 600, "created": end - 700, "preset": "0.20mm Standard",
                         "result": {"outcome": outcome, "ended_at": end, "summary": "", **kw}})
    return jid


def test_owner_and_operators_say_why_and_others_cannot(client, fleet):
    oc = make_user(client, "fl.owner@imperial.ac.uk")
    owner = oc.get("/api/me").json()
    jid = _ended(fleet["mini"], "failed", owner)
    assert make_user(client, "fl.other@imperial.ac.uk").put(f"/api/jobs/{jid}/cause", json={"cause": "warping"}).status_code == 404
    r = oc.put(f"/api/jobs/{jid}/cause", json={"cause": "didnt_stick", "note": "glue stick next time"})
    assert r.status_code == 200 and store.jobs.get(jid)["cause"]["label"] == "Did not stick to the plate"
    assert client.put(f"/api/jobs/{jid}/cause", json={"cause": "warping"}).json()["cause"] == "warping"     # an admin can change it
    assert oc.put(f"/api/jobs/{jid}/cause", json={"cause": "gremlins"}).status_code == 400
    ok = _ended(fleet["mini"], "finished", owner)
    assert oc.put(f"/api/jobs/{ok}/cause", json={"cause": "warping"}).status_code == 400
    page = oc.get(f"/api/jobs/{jid}/page").json()
    assert page["can_explain"] and page["cause"]["cause"] == "warping" and "spaghetti" in page["cause_labels"]


def test_clearing_the_plate_can_record_the_cause(client, fleet):
    pid = fleet["p1s"]
    jid = _ended(pid, "failed", client.get("/api/me").json())
    store.printers.update(pid, bed={"state": "occupied", "finished": True}, status={"state": "FAILED"})
    assert next(p for p in client.get("/api/printers").json() if p["id"] == pid)["needs_cause"] == jid
    r = client.post(f"/api/printers/{pid}/bed/clear", json={"bed_empty": True, "plate_matches": True, "filament_matches": True, "cause": "spaghetti"})
    assert r.status_code == 200 and store.jobs.get(jid)["cause"]["cause"] == "spaghetti"


def test_patterns_flag_combinations_and_runs_to_staff_only(client, fleet):
    pid = fleet["x1c"]
    for outcome in ("failed", "failed", "failed", "finished"):
        _ended(pid, outcome, filament="PETG")
    p = client.get("/api/failures").json()
    texts = [f["text"] for f in p["flags"]]
    assert any(t.startswith("x1c · PETG: 3 of 4") for t in texts) and any(t.startswith("x1c: 3 of its last 4") for t in texts)
    assert p["unexplained"] >= 3
    assert make_user(client, "fl.member@imperial.ac.uk").get("/api/failures").status_code == 403
    assert "flags" not in make_user(client, "fl.member2@imperial.ac.uk").get("/api/home").json().get("fleet", {})
    assert client.get("/api/home").json()["fleet"]["flags"]


def test_deliberate_stops_do_not_count(client, fleet):
    pid = fleet["h2d"]
    for _ in range(4):
        _ended(pid, "failed", filament="ABS", stopped_by="someone")
    assert not [f for f in failures.patterns()["flags"] if "h2d" in f["text"]]
