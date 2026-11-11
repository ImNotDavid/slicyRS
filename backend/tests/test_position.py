"""Milestone 1.2: a part placed by hand (an offset from the middle of the bed) and auto-arrange (offset back to zero)."""
import re
import time

import pytest

from app import assign, plates, store
from app.catalog import PRINTER_MODELS, job_position
from conftest import VALID, upload, wait_job
from test_pipeline import pooled_job, wait_phase

MINI = PRINTER_MODELS["A1 mini"]["bed"]        # 180 x 180


def test_the_footprint_must_stay_on_the_plate_where_it_was_put():
    size = (40, 30, 20)
    assert assign.fits(MINI, size, (0, 0)) and assign.fits(MINI, size, (50, 50))
    assert not assign.fits(MINI, size, (80, 0))                    # 90 - 80 = 10 mm of half-width left, the part needs 20 + margin
    assert assign.fits(MINI, size, (66, 0)) and not assign.fits(MINI, size, (68, 0))     # right edge 90 + off + 20 must stay under 180 - 3
    assert not assign.fits(MINI, size, (0, -80))
    assert not assign.fits(MINI, (40, 30, 200), (10, 10))          # still too tall
    assert assign.fits(MINI, (170, 100, 10)) and not assign.fits(MINI, (170, 100, 10), (5, 0))   # no room to move a part that fills the plate


def test_zero_offset_keeps_the_old_rule_that_the_slicer_may_turn_the_part():
    assert assign.fits(MINI, (170, 40, 10)) and assign.fits(MINI, (40, 170, 10))
    assert assign.fits(MINI, (170, 40, 10), (0.0, 0.0))


def test_a_blocker_says_the_position_is_the_problem_not_the_part():
    p = {"id": "p", "name": "mini", "model": "A1 mini", "status": {"state": "IDLE"}}
    ok = assign.evaluate(p, (40, 30, 20), "PLA", 0.4, (0, 0))
    far = assign.evaluate(p, (40, 30, 20), "PLA", 0.4, (80, 0))
    assert ok.eligible and not far.eligible and "At the position you chose" in far.blockers[0]
    huge = assign.evaluate(p, (400, 30, 20), "PLA", 0.4, (10, 0))
    assert "exceeds" in huge.blockers[0]                              # a part too big for the printer is still reported as that


def test_job_position_reads_the_spec_and_defaults_to_the_centre():
    assert job_position({}) == (0.0, 0.0) and job_position({"spec": {}}) == (0.0, 0.0)
    assert job_position({"spec": {"position": [12.5, -3]}}) == (12.5, -3.0)


def test_the_position_chooses_the_printer(client, fleet):
    m = upload(client, VALID / "cube_binary.stl").json()                 # 40 x 30 x 20
    base = {"model_id": m["id"], "filament": "PLA"}
    centred = client.post("/api/assign", json={**base, "position": [0, 0]}).json()
    assert any(c["eligible"] and c["model"] == "A1 mini" for c in centred["candidates"])
    edge = client.post("/api/assign", json={**base, "position": [80, 0]}).json()
    mini = next(c for c in edge["candidates"] if c["model"] == "A1 mini")
    assert not mini["eligible"] and "hang off" in mini["blockers"][0]
    assert any(c["eligible"] for c in edge["candidates"])                  # the larger printers still take it there


def test_a_part_that_fits_no_printer_where_it_was_put_is_refused_at_the_start(client, fleet):
    m = upload(client, VALID / "cube_binary.stl").json()
    r = client.post("/api/jobs/preview", json={"model_id": m["id"], "filament": "PLA", "position": [400, 0]})
    assert r.status_code == 409 and "position you chose" in r.json()["detail"]
    ok = client.post("/api/jobs/preview", json={"model_id": m["id"], "filament": "PLA", "position": [30, -20]})
    assert ok.status_code == 200
    d = wait_phase(client, ok.json()["id"], {"previewed", "failed"})
    assert d["spec"]["position"] == [30, -20]


def mock_extent(client, job_id):
    text = client.get(f"/api/jobs/{job_id}/gcode").text
    xs = [float(x) for x in re.findall(r"G1 X([-\d.]+) Y[-\d.]+ E", text)]
    return min(xs), max(xs)


def test_the_slice_puts_the_part_where_it_was_placed_and_zero_means_the_slicers_choice(client, fleet):
    m = upload(client, VALID / "cube_binary.stl").json()
    def slice_at(position):
        r = client.post("/api/slice", json={"model_id": m["id"], "filament": "PLA", "printer_id": fleet["p1s"], "position": position})
        assert r.status_code == 200, r.text
        return wait_job(client, r.json()["id"])
    centred = slice_at([0, 0])
    moved = slice_at([30, 0])
    lo0, hi0 = mock_extent(client, centred["id"])
    lo1, hi1 = mock_extent(client, moved["id"])
    assert (lo1 + hi1) / 2 - (lo0 + hi0) / 2 == pytest.approx(30, abs=0.5)           # shifted by the offset along X
    assert not any(j.get("spec") for j in [centred])                                  # direct slices keep no spec: position is per request


def test_a_part_placed_by_hand_never_shares_a_plate(client, fleet):
    me = client.get("/api/me").json()
    printer = store.printers.get(fleet["p1s"])
    a, b = pooled_job(me, "a", time.time()), pooled_job(me, "b", time.time() + 1)
    for j in (a, b):
        store.jobs.update(j, allow_batching=True, preview={"time_s": 600})
    pool = [j for j in store.jobs.all() if j["phase"] == "pooled"]
    assert {j["id"] for j in plates.find_partners(store.jobs.get(a), printer, pool, time.time())} == {b}
    store.jobs.update(b, spec={"preset": "0.20mm Standard", "filament": "PLA", "settings": {}, "position": [20, 0]})
    pool = [j for j in store.jobs.all() if j["phase"] == "pooled"]
    assert plates.find_partners(store.jobs.get(a), printer, pool, time.time()) == []
    assert plates.hold_reason(store.jobs.get(b), time.time()) is None                   # and it does not wait for partners either
