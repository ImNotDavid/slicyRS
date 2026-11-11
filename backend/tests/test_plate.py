"""Milestone 1.6: several parts on one plate, laid out in the editor."""
import re
import time

import pytest
import trimesh

from app import multipart, plates, store
from app.catalog import job_is_plate
from conftest import VALID, upload, wait_job
from test_pipeline import pooled_job, wait_phase


def box(w, d, h):
    m = trimesh.creation.box((w, d, h))
    m.apply_translation([0, 0, h / 2])
    return m


def build(parts):
    sizes = {"a": (40, 30, 20), "b": (20, 20, 50), "c": (100, 100, 5)}
    return multipart.build(parts, lambda mid, rot, sc: box(*[v * sc for v in sizes[mid]]), lambda mid: f"part-{mid}")


def part(mid, x=0.0, y=0.0, scale=1.0):
    return {"model_id": mid, "rotation": (0, 0, 0), "scale": scale, "position": (x, y)}


# ------------------------------------------------------------------ the layout rules

def test_the_envelope_is_the_box_around_every_part_and_height_is_the_tallest():
    inst = build([part("a", -40, 0), part("b", 40, 20)])
    size, centre = multipart.envelope(inst)
    # x runs from -60 to 50; y from -15 to 30
    assert size[0] == 110.0 and size[1] == 45.0 and size[2] == 50.0 and centre == (-5.0, 7.5)


def test_parts_may_not_overlap_or_touch():
    multipart.check_layout(build([part("a", -40, 0), part("a", 40, 0)]))
    with pytest.raises(multipart.PlateError, match="overlap"):
        multipart.check_layout(build([part("a", 0, 0), part("a", 10, 0)]))
    with pytest.raises(multipart.PlateError, match="overlap"):
        multipart.check_layout(build([part("a", 0, 0), part("b", 30.2, 0)]))          # 10 mm from the middle of b to its edge, 20 + 10 = 30: touching
    multipart.check_layout(build([part("a", 0, 0), part("b", 31, 0)]))                  # a 1 mm gap is enough


def test_a_plate_holds_at_most_twelve_parts_and_at_least_one():
    multipart.build([part("a", i * 50) for i in range(12)], lambda m, r, s: box(10, 10, 10), lambda m: "x")
    with pytest.raises(multipart.PlateError, match="at most 12"):
        multipart.build([part("a", i * 50) for i in range(13)], lambda m, r, s: box(10, 10, 10), lambda m: "x")
    with pytest.raises(multipart.PlateError):
        multipart.build([], lambda m, r, s: box(10, 10, 10), lambda m: "x")


def test_the_parts_that_do_not_fit_a_bed_are_named():
    inst = build([part("a", -60, 0), part("b", 85, 0), part("c", 0, 70)])
    bed = (180, 180, 180)
    assert multipart.overflow_names(inst, bed, 3.0) == ["part-b", "part-c"]
    assert multipart.overflow_names(inst, (256, 256, 256), 3.0) == []
    assert multipart.overflow_names(build([part("b", 0, 0)]), (180, 180, 40), 3.0) == ["part-b"]          # too tall
    assert multipart.plate_name(build([part("a"), part("b", 50)])) == "part-a + 1 more"


# ------------------------------------------------------------------ the API

@pytest.fixture()
def two_models(client, fleet):
    return upload(client, VALID / "cube_binary.stl").json()["id"], upload(client, VALID / "sphere.stl").json()["id"]


def P(mid, x=0, y=0, **kw):
    return {"model_id": mid, "position": [x, y], **kw}


def test_assign_looks_at_the_whole_plate(client, two_models):
    cube, sphere = two_models                                            # 40 x 30 x 20 and a 30 mm sphere
    one = client.post("/api/assign", json={"model_id": cube, "filament": "PLA"}).json()
    plate = client.post("/api/assign", json={"parts": [P(cube, -30), P(sphere, 40)], "filament": "PLA"}).json()
    assert plate["dims"][0] == pytest.approx(105.0, abs=0.5) and plate["dims"][0] > one["dims"][0] * 2         # from -50 to 55 mm
    assert plate["dims"][2] == 30.0 and any(c["eligible"] for c in plate["candidates"])


def test_parts_too_wide_for_a_printer_are_named_in_its_blockers(client, two_models):
    cube, sphere = two_models
    r = client.post("/api/assign", json={"parts": [P(cube, -80), P(sphere, 80)], "filament": "PLA"}).json()      # 180 mm bed edge is 90 mm from the middle
    mini = next(c for c in r["candidates"] if c["model"] == "A1 mini")
    assert not mini["eligible"] and any("Parts that do not fit" in b for b in mini["blockers"])
    assert any(c["eligible"] for c in r["candidates"])


def test_overlapping_parts_and_too_many_parts_are_refused(client, two_models):
    cube, sphere = two_models
    r = client.post("/api/assign", json={"parts": [P(cube, 0), P(sphere, 10)], "filament": "PLA"})
    assert r.status_code == 400 and "overlap" in r.json()["detail"]
    assert client.post("/api/assign", json={"parts": [P(cube, i * 45) for i in range(13)], "filament": "PLA"}).status_code == 422
    assert client.post("/api/assign", json={"filament": "PLA"}).status_code == 422                                       # neither a model nor parts
    assert client.post("/api/assign", json={"parts": [P("nope")], "filament": "PLA"}).status_code == 404


def test_other_peoples_models_cannot_be_put_on_your_plate(client, two_models):
    from conftest import make_user
    other = make_user(client, "plate.thief@imperial.ac.uk")
    assert other.post("/api/assign", json={"parts": [P(two_models[0], -40), P(two_models[1], 40)], "filament": "PLA"}).status_code == 404


def test_a_plate_of_one_part_is_just_that_part(client, two_models):
    cube, _ = two_models
    a = client.post("/api/assign", json={"parts": [P(cube, 30, -10)], "filament": "PLA"}).json()
    b = client.post("/api/assign", json={"model_id": cube, "position": [30, -10], "filament": "PLA"}).json()
    assert a == b


def test_arrange_returns_a_place_for_every_part_that_does_not_overlap(client, two_models):
    cube, sphere = two_models
    r = client.post("/api/plate/arrange", json={"parts": [{"model_id": cube}, {"model_id": sphere}, {"model_id": cube}], "printer_model": "A1 mini"})
    assert r.status_code == 200, r.text
    placed = r.json()["parts"]
    assert len(placed) == 3 and all(len(p["position"]) == 2 and "rotation_z" in p for p in placed)
    laid = client.post("/api/assign", json={"parts": [{"model_id": m, "position": p["position"], "rotation": [0, 0, p["rotation_z"]]}
                                                      for m, p in zip([cube, sphere, cube], placed)], "filament": "PLA"})
    assert laid.status_code == 200                                    # the layout it returned passes the server's own overlap check
    assert client.post("/api/plate/arrange", json={"parts": [{"model_id": cube}], "printer_model": "Nope"}).status_code == 400


def test_a_plate_draft_keeps_every_part_and_is_one_job(client, two_models):
    cube, sphere = two_models
    r = client.post("/api/jobs/preview", json={"parts": [P(cube, -30), P(sphere, 40), P(cube, 0, 40)], "filament": "PLA"})
    assert r.status_code == 200, r.text
    d = wait_phase(client, r.json()["id"], {"previewed", "failed"})
    assert d["phase"] == "previewed" and d["name"].endswith("+ 2 more") and len(d["spec"]["parts"]) == 3
    assert {p["file"] for p in d["spec"]["parts"]} == {"input_0.stl", "input_1.stl"}               # the two cubes share a file
    assert job_is_plate(d) and d["spec"]["position"] and d["dims"][2] == 30.0
    from app.storage import JOBS, files
    assert files.has(JOBS, d["id"], "input_0.stl") and files.has(JOBS, d["id"], "input_1.stl")
    assert [j["id"] for j in client.get("/api/jobs").json() if j["id"] == d["id"]] == []           # still a draft


def test_a_plate_nobody_can_print_is_refused_with_the_names(client, two_models):
    cube, sphere = two_models
    r = client.post("/api/jobs/preview", json={"parts": [P(cube, -400), P(sphere, 400)], "filament": "PLA"})
    assert r.status_code == 409 and "No printer in the fleet can ever print this plate" in r.json()["detail"] and "Parts that do not fit" in r.json()["detail"]


def test_a_direct_slice_of_a_plate_puts_each_part_where_it_was_placed(client, two_models, fleet):
    cube, sphere = two_models
    r = client.post("/api/slice", json={"parts": [P(cube, -40), P(sphere, 40)], "filament": "PLA", "printer_id": fleet["p1s"]})
    assert r.status_code == 200, r.text
    j = wait_job(client, r.json()["id"])
    assert j["state"] == "done" and j["name"].endswith("+ 1 more")
    xs = [float(x) for x in re.findall(r"G1 X([-\d.]+) Y[-\d.]+ E", client.get(f"/api/jobs/{j['id']}/gcode").text)]
    centre = 128.0                                                          # the P1S bed is 256 mm wide
    assert min(xs) < centre - 40 - 10 and max(xs) > centre + 40 + 10      # both parts, 80 mm apart, are in the G-code


def test_a_laid_out_plate_never_shares_with_other_peoples_jobs(client, fleet):
    me = client.get("/api/me").json()
    printer = store.printers.get(fleet["p1s"])
    a, b = pooled_job(me, "a", time.time()), pooled_job(me, "b", time.time() + 1)
    for j in (a, b):
        store.jobs.update(j, allow_batching=True, preview={"time_s": 600})
    store.jobs.update(b, spec={"preset": "0.20mm Standard", "filament": "PLA", "settings": {},
                               "parts": [{"file": "x"}, {"file": "y"}], "position": [0, 0]})
    pool = [j for j in store.jobs.all() if j["phase"] == "pooled"]
    assert plates.find_partners(store.jobs.get(a), printer, pool, time.time()) == []
    assert plates.hold_reason(store.jobs.get(b), time.time()) is None
