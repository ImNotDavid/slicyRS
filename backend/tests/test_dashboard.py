"""Fleet dashboard (2.11)."""
import time
import uuid

from app import dashboard, jobstate, store
from conftest import make_user

DAY = 86400


def _job(pid, outcome, filament="PLA", grams=100.0, ago=DAY, dur=3600, owner="d.owner@imperial.ac.uk", **res):
    jid = uuid.uuid4().hex[:8]
    end = time.time() - ago
    store.jobs.put(jid, {"id": jid, "name": "d", "printer_id": pid, "printer_name": "x", "phase": "printed" if outcome == "finished" else "failed",
                         "filament": filament, "owner_email": owner, "stats": {"filament_g": grams}, "started_at": end - dur, "created": end - dur - 60,
                         "result": {"outcome": outcome, "ended_at": end, "duration_s": dur, "percent": res.pop("percent", 100), "summary": "", **res}})
    return jid


def test_dashboard_numbers(client, fleet):
    mini = fleet["mini"]
    _job(mini, "finished")
    _job(mini, "failed", percent=50, layer=2, hms=[])
    _job(fleet["p1s"], "failed", filament="PETG", hms=[{"code": "0300-0100-0001-0001", "text": "x"}])
    _job(mini, "finished", ago=40 * DAY)                                   # outside both windows
    d = client.get("/api/admin/dashboard?days=7").json()
    assert d["prints"] == {"finished": 1, "failed": 2}
    u = {x["name"]: x for x in d["utilisation"]}
    assert u["mini"]["busy_s"] == 7200 and 0 < u["mini"]["percent"] < 100
    fp = {x["name"]: x for x in d["failures_by_printer"]}
    assert fp["mini"]["rate"] == 50.0 and fp["p1s"]["rate"] == 100.0
    assert {r["reason"] for r in d["failure_reasons"]} == {"Stopped in the first layers", "Printer error 0300-0100-0001-0001"}
    assert {m["material"]: m["grams"] for m in d["filament"]["by_material"]} == {"PLA": 150.0, "PETG": 100.0}
    assert len(d["daily"]) == 7 and sum(x["finished"] for x in d["daily"]) == 1
    assert client.get("/api/admin/dashboard?days=30").json()["prints"]["finished"] == 1
    assert client.get("/api/admin/dashboard?days=3").status_code == 400


def test_queue_waits_come_from_events(client, fleet):
    jid = uuid.uuid4().hex[:8]
    store.jobs.put(jid, {"id": jid, "name": "q", "phase": "assigned", "created": time.time()})
    t = time.time() - 3600
    for to, at in (("pooled", t), ("assigned", t + 600)):
        ev = {"id": uuid.uuid4().hex[:12], "job_id": jid, "kind": "phase", "at": at, "actor": None, "data": {"to": to}}
        store.events.put(ev["id"], ev)
    q = client.get("/api/admin/dashboard").json()["queue"]
    assert q["samples"] >= 1 and q["max_s"] >= 600


def test_only_admins(client):
    assert make_user(client, "d.member@imperial.ac.uk").get("/api/admin/dashboard").status_code == 403


def test_reasons_are_grouped():
    assert dashboard.reason({"stopped_by": "x"}) == "Stopped by a person"
    assert dashboard.reason({"layer": 50}) == "Stopped without an error code"
