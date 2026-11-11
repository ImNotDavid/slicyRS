"""Milestone 1.10: nozzle sizes. A process preset names its nozzle; only printers with that nozzle can print the job."""
import time

import pytest

from app import allocator, assign, bedstate, forecast, plates, profiles, store
from app.catalog import NOZZLES, PROCESS_PRESETS, job_nozzle, preset_nozzle
from conftest import make_user
from test_pipeline import CLEAR, draft, pooled_job, wait_phase


def test_every_nozzle_has_presets_with_unique_ids_and_the_old_ones_are_unchanged():
    ids = [p["id"] for p in PROCESS_PRESETS]
    assert len(ids) == len(set(ids)) and {p["nozzle"] for p in PROCESS_PRESETS} == set(NOZZLES)
    assert "0.20mm Standard" in ids and preset_nozzle("0.20mm Standard") == 0.4 and preset_nozzle("0.30mm Standard @0.6") == 0.6
    assert preset_nozzle(None) == 0.4 and preset_nozzle("nonsense") == 0.4
    assert job_nozzle({"spec": {"preset": "0.40mm Standard @0.8"}}) == 0.8 and job_nozzle({"preset": "0.12mm Fine"}) == 0.4 and job_nozzle({}) == 0.4


def printer(nozzle=None, status_nozzle=None, **kw):
    st = {"state": "IDLE", **({"nozzle_mm": status_nozzle} if status_nozzle else {})}
    return {"id": "p", "name": "p", "model": "P1S", "status": st, **({"nozzle_mm": nozzle} if nozzle else {}), **kw}


def test_what_a_connected_printer_reports_wins_over_what_an_admin_set():
    assert assign.nozzle_of(printer()) == 0.4
    assert assign.nozzle_of(printer(nozzle=0.6)) == 0.6
    assert assign.nozzle_of(printer(nozzle=0.6, status_nozzle=0.8)) == 0.8


def test_a_job_only_fits_a_printer_with_its_nozzle_and_says_why_not():
    c = assign.evaluate(printer(nozzle=0.6), (20, 20, 20), "PLA", 0.4)
    assert not c.eligible and any("needs a 0.4 mm nozzle; this printer has a 0.6 mm" in b for b in c.blockers)
    assert assign.evaluate(printer(nozzle=0.6), (20, 20, 20), "PLA", 0.6).eligible
    assert assign.evaluate(printer(), (20, 20, 20), "PLA").eligible                      # default stays 0.4


def test_the_pool_gives_each_job_a_printer_with_the_right_nozzle(client, fleet):
    me = client.get("/api/me").json()
    for k, pid in fleet.items():
        store.printers.update(pid, enabled=k in ("mini", "p1s"))
        bedstate.set_state(pid, "clear", "manual")
    store.printers.update(fleet["p1s"], nozzle_mm=0.6)
    thin = pooled_job(me, "thin", 100)                                                    # 0.4 mm preset
    thick = pooled_job(me, "thick", 110)
    store.jobs.update(thick, spec={"preset": "0.30mm Standard @0.6", "filament": "PLA", "settings": {}}, preset="0.30mm Standard @0.6")
    allocator.assign_pool()
    assert store.jobs.get(thin)["printer_id"] == fleet["mini"] and store.jobs.get(thick)["printer_id"] == fleet["p1s"]


def test_a_job_nobody_can_nozzle_is_refused_at_the_start_and_the_forecast_agrees(client, fleet):
    from conftest import upload, VALID
    mid = upload(client, VALID / "cube_binary.stl").json()["id"]
    r = client.post("/api/jobs/preview", json={"model_id": mid, "filament": "PLA", "preset": "0.40mm Standard @0.8"})
    assert r.status_code == 409 and "0.8 mm nozzle" in r.json()["detail"]
    r = client.post("/api/assign", json={"model_id": mid, "filament": "PLA", "nozzle_mm": 0.6})
    assert r.json()["chosen"] is None and all(not c["eligible"] for c in r.json()["candidates"])
    store.printers.update(fleet["p1s"], nozzle_mm=0.6)
    assert client.post("/api/assign", json={"model_id": mid, "filament": "PLA", "nozzle_mm": 0.6}).json()["chosen"] == fleet["p1s"]
    assert client.post("/api/jobs/preview", json={"model_id": mid, "filament": "PLA", "preset": "0.30mm Standard @0.6"}).status_code == 200


def test_jobs_for_different_nozzles_never_share_a_plate(client, fleet):
    me = client.get("/api/me").json()
    p = store.printers.get(fleet["p1s"])
    a, b = pooled_job(me, "a", 1), pooled_job(me, "b", 2)
    for j in (a, b):
        store.jobs.update(j, allow_batching=True)
    store.jobs.update(b, spec={"preset": "0.30mm Standard @0.6", "filament": "PLA", "settings": {}})
    assert plates.compatibility(store.jobs.get(a), store.jobs.get(b), p) is None


def test_admin_sets_the_nozzle_and_a_bad_size_is_refused(client, fleet):
    r = client.put(f"/api/admin/printers/{fleet['mini']}", json={"name": "mini", "model": "A1 mini", "nozzle_mm": 0.6})
    assert r.status_code == 200 and r.json()["nozzle_mm"] == 0.6
    assert next(p for p in client.get("/api/printers").json() if p["id"] == fleet["mini"])["nozzle_mm"] == 0.6
    assert client.put(f"/api/admin/printers/{fleet['mini']}", json={"name": "mini", "model": "A1 mini", "nozzle_mm": 0.5}).status_code == 400
    from app import bambu
    bambu._set_status(fleet["mini"], {"nozzle_mm": 0.4})                                  # the printer says otherwise: it wins
    assert next(p for p in client.get("/api/printers").json() if p["id"] == fleet["mini"])["nozzle_mm"] == 0.4


def test_the_connector_reads_the_nozzle_from_the_printers_report():
    import json
    from types import SimpleNamespace
    from app.bambulan import LanLink
    seen = {}
    link = LanLink({"id": "x", "host": "h", "port": 1, "serial": "s"}, "code", lambda pid, u: seen.update(u), lambda: {}, lambda: None)
    link._on_message(None, None, SimpleNamespace(payload=json.dumps({"print": {"nozzle_diameter": "0.6", "gcode_state": "IDLE"}}).encode()))
    assert seen["nozzle_mm"] == 0.6 and seen["state"] == "IDLE"
