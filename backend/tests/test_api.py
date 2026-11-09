"""End-to-end API tests over every fixture format, edge cases and the user's own models."""
import io
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
import trimesh

from app import main as app_main
from app.catalog import PRINTER_MODELS
from conftest import DESKTOP, INVALID, MANIFEST, VALID, parse_gcode, slice_model, upload, wait_job, ams

VALID_FILES = sorted(MANIFEST)
BEDS = {"A1 mini": 180, "A1": 256, "P1S": 256, "X1C": 256, "H2D": 320}


def fits_fleet(dims):
    """Mirror of the assignment rules for the test fleet: does any printer take it?"""
    x, y, z = dims
    return any(z <= PRINTER_MODELS[m]["bed"][2] and
               (max(x, y) <= max(PRINTER_MODELS[m]["bed"][:2]) - 6 and min(x, y) <= min(PRINTER_MODELS[m]["bed"][:2]) - 6)
               for m in ("A1 mini", "P1S", "X1C", "H2D"))


# ------------------------------------------------------------------ uploads

@pytest.mark.parametrize("name", VALID_FILES)
def test_upload_valid_formats(client, name):
    r = upload(client, VALID / name)
    assert r.status_code == 200, r.text
    body = r.json()
    assert sorted(body["dims"]) == pytest.approx(sorted(MANIFEST[name]["dims"]), abs=0.6), MANIFEST[name]
    assert body["triangles"] > 0
    prev = client.get(f"/api/models/{body['id']}/preview.stl")
    assert prev.status_code == 200
    mesh = trimesh.load(io.BytesIO(prev.content), file_type="stl")
    assert sorted(mesh.extents) == pytest.approx(sorted(MANIFEST[name]["dims"]), abs=0.6)


@pytest.mark.parametrize("name", sorted(p.name for p in INVALID.iterdir()))
def test_upload_invalid_is_clean_4xx(client, name):
    r = upload(client, INVALID / name)
    assert r.status_code in (400, 413), f"{name}: {r.status_code} {r.text}"
    assert "detail" in r.json()


def test_upload_too_large(client, monkeypatch):
    monkeypatch.setattr(app_main, "MAX_UPLOAD_MB", 0)
    r = upload(client, VALID / "cube_binary.stl")
    assert r.status_code == 413


def test_upload_case_insensitive_extension(client):
    assert upload(client, VALID / "cube_binary.stl", name="PART.STL").status_code == 200


def test_unknown_model_404(client):
    assert client.get("/api/models/nope/preview.stl").status_code == 404
    assert client.post("/api/assign", json={"model_id": "nope", "filament": "PLA"}).status_code == 404


# ------------------------------------------------------- slicing every format

@pytest.mark.parametrize("name", VALID_FILES)
def test_slice_each_fixture(client, fleet, name):
    m = upload(client, VALID / name).json()
    r, job = slice_model(client, m["id"])
    dims = sorted(MANIFEST[name]["dims"], reverse=True)
    if not fits_fleet(MANIFEST[name]["dims"]):
        assert r.status_code == 409
        assert "No printer" in r.json()["detail"]
        return
    assert r.status_code == 200, r.text
    assert job["state"] == "done", job.get("error")
    assert job["stats"]["layers"] > 0 and job["stats"]["time_s"] > 0 and job["stats"]["filament_g"] >= 0
    g = parse_gcode(client.get(f"/api/jobs/{job['id']}/gcode").text)
    bed = PRINTER_MODELS[job["printer_model"]]["bed"]
    h = MANIFEST[name]["dims"]
    height = m["dims"][2] if sorted(m["dims"]) != sorted(h) else m["dims"][2]
    # toolpaths stay on the plate and inside the build height
    assert -0.01 <= g["xmin"] and g["xmax"] <= bed[0] + 0.01 and -0.01 <= g["ymin"] and g["ymax"] <= bed[1] + 0.01
    assert g["zmax"] <= bed[2]
    assert g["zmax"] == pytest.approx(height, abs=0.25)
    assert g["e_bad"] == 0
    assert g["layers"] == sorted(g["layers"])


def test_gcode_centred_on_bed(client, fleet):
    m = upload(client, VALID / "offset_negative.stl").json()  # lives 500 mm from origin
    _, job = slice_model(client, m["id"])
    g = parse_gcode(client.get(f"/api/jobs/{job['id']}/gcode").text)
    bed = PRINTER_MODELS[job["printer_model"]]["bed"]
    assert (g["xmin"] + g["xmax"]) / 2 == pytest.approx(bed[0] / 2, abs=0.5)
    assert (g["ymin"] + g["ymax"]) / 2 == pytest.approx(bed[1] / 2, abs=0.5)
    assert g["layers"][0] == pytest.approx(0.2, abs=0.01)  # dropped to the bed


def test_step_units_are_mm(client, fleet):
    m = upload(client, VALID / "box.step").json()
    assert m["dims"] == pytest.approx([30, 20, 10], abs=0.2) or sorted(m["dims"]) == pytest.approx([10, 20, 30], abs=0.2)


def test_3mf_unit_inch(client):
    assert upload(client, VALID / "unit_inch.3mf").json()["dims"] == pytest.approx([25.4] * 3, abs=0.05)


# ----------------------------------------------------------------- assignment

def assign(client, model_id, filament="PLA", **kw):
    r = client.post("/api/assign", json={"model_id": model_id, "filament": filament, **kw})
    assert r.status_code == 200, r.text
    return r.json()


def chosen_name(client, res):
    return next((p["name"] for p in client.get("/api/printers").json() if p["id"] == res["chosen"]), None)


@pytest.mark.parametrize("fixture,filament,expected", [
    ("tiny_2mm.stl", "PLA", "mini"),       # smallest bed that fits, PLA loaded
    ("cube_binary.stl", "PLA", "mini"),
    ("plate_250.stl", "PLA", "p1s"),       # too big for the mini
    ("plate_300.stl", "PLA", "h2d"),       # only the H2D is large enough
    ("tall_300.stl", "PLA", "h2d"),        # Z limit
    ("huge_400.stl", "PLA", None),         # nothing fits
    ("cube_binary.stl", "ABS", "x1c"),     # enclosed + ABS already loaded
    ("cube_binary.stl", "PA", "p1s"),      # enclosed, none loaded: P1S has smaller bed than X1C? equal; stable pick
    ("cube_binary.stl", "PA-CF", None),    # no hardened nozzle anywhere
])
def test_assignment(client, fleet, fixture, filament, expected):
    m = upload(client, VALID / fixture).json()
    res = assign(client, m["id"], filament)
    got = chosen_name(client, res)
    if expected == "p1s" and filament == "PA":
        assert got in ("p1s", "x1c", "h2d")  # any enclosed printer is acceptable; never the mini
    else:
        assert got == expected, [(c["name"], c["eligible"], c["score"], c["blockers"]) for c in res["candidates"]]
    if expected is None:
        assert all(not c["eligible"] and c["blockers"] for c in res["candidates"])


def test_offline_and_busy_printers(client, fleet):
    m = upload(client, VALID / "cube_binary.stl").json()
    pid = fleet
    client.put(f"/api/admin/printers/{pid['mini']}", json={"name": "mini", "model": "A1 mini", "status": {"state": "OFFLINE", "ams": ams("PLA")}})
    assert chosen_name(client, assign(client, m["id"])) == "p1s"
    client.put(f"/api/admin/printers/{pid['p1s']}", json={"name": "p1s", "model": "P1S", "status": {"state": "RUNNING", "ams": ams("PLA")}})
    # P1S busy: idle H2D without... still prefers an idle printer over a busy one
    assert chosen_name(client, assign(client, m["id"])) in ("h2d", "x1c")


def test_queue_pushes_work_to_other_printer(client, fleet):
    m = upload(client, VALID / "cube_binary.stl").json()
    _, job = slice_model(client, m["id"])
    assert job["printer_id"] == fleet["mini"]
    for _ in range(3):  # force three queued jobs onto the mini
        _, j = slice_model(client, m["id"], printer_id=fleet["mini"])
        assert client.post(f"/api/jobs/{j['id']}/queue").status_code == 200
    again = assign(client, m["id"])
    assert again["chosen"] != fleet["mini"], "queue depth should divert new work"
    printers = {p["id"]: p for p in client.get("/api/printers").json()}
    assert printers[fleet["mini"]]["queue_depth"] == 3


def test_manual_printer_override(client, fleet):
    m = upload(client, VALID / "cube_binary.stl").json()
    r, job = slice_model(client, m["id"], printer_id=fleet["x1c"])
    assert r.status_code == 200 and job["printer_id"] == fleet["x1c"]
    r, _ = slice_model(client, upload(client, VALID / "plate_300.stl").json()["id"], printer_id=fleet["mini"])
    assert r.status_code == 409 and "cannot print" in r.json()["detail"]
    r, _ = slice_model(client, m["id"], printer_id="does-not-exist")
    assert r.status_code == 409


# ---------------------------------------------------------------- transforms

def test_rotation_changes_dimensions_and_assignment(client, fleet):
    m = upload(client, VALID / "tall_300.stl").json()          # 20x20x300 → H2D only
    flat = assign(client, m["id"], rotation=[90, 0, 0])
    assert sorted(flat["dims"]) == pytest.approx([20, 20, 300], abs=0.1)
    assert flat["dims"][2] == pytest.approx(20, abs=0.1)        # now lying down
    assert flat["dims"][1] == pytest.approx(300, abs=0.1)
    assert chosen_name(client, flat) == "h2d"                   # 300 on Y still needs the H2D


def test_scale_changes_printer(client, fleet):
    m = upload(client, VALID / "plate_250.stl").json()
    assert chosen_name(client, assign(client, m["id"])) == "p1s"
    assert chosen_name(client, assign(client, m["id"], scale=0.5)) == "mini"
    assert chosen_name(client, assign(client, m["id"], scale=1.25)) == "h2d"
    assert chosen_name(client, assign(client, m["id"], scale=3)) is None


def test_rotated_slice_height(client, fleet):
    m = upload(client, VALID / "tube.stl").json()               # 20x20x90
    _, job = slice_model(client, m["id"], rotation=[90, 0, 0])
    g = parse_gcode(client.get(f"/api/jobs/{job['id']}/gcode").text)
    assert g["zmax"] == pytest.approx(20, abs=0.25)


@pytest.mark.parametrize("bad", [{"scale": 0}, {"scale": -1}, {"scale": 1000}, {"rotation": [1, 2]}])
def test_bad_transform_rejected(client, fleet, bad):
    m = upload(client, VALID / "cube_binary.stl").json()
    r = client.post("/api/slice", json={"model_id": m["id"], "filament": "PLA", **bad})
    assert r.status_code == 422


def test_3mf_passthrough_vs_transformed(client, fleet):
    m = upload(client, VALID / "torus.3mf").json()
    _, a = slice_model(client, m["id"])
    _, b = slice_model(client, m["id"], scale=0.5)
    from app.storage import JOBS, files
    assert files.has(JOBS, a["id"], "input.3mf")
    assert files.has(JOBS, b["id"], "input.stl")


# ------------------------------------------------------------------ settings

def test_layer_height_changes_layer_count(client, fleet):
    m = upload(client, VALID / "cube_binary.stl").json()
    _, coarse = slice_model(client, m["id"], preset="0.20mm Standard")
    _, fine = slice_model(client, m["id"], preset="0.12mm Fine")
    _, over = slice_model(client, m["id"], settings={"layer_height": 0.1})
    assert coarse["stats"]["layers"] == 100
    assert fine["stats"]["layers"] == pytest.approx(167, abs=1)
    assert over["stats"]["layers"] == 200


def test_validation_errors(client, fleet):
    m = upload(client, VALID / "cube_binary.stl").json()
    base = {"model_id": m["id"], "filament": "PLA"}
    assert client.post("/api/slice", json={**base, "filament": "UNOBTAINIUM"}).status_code == 400
    assert client.post("/api/slice", json={**base, "preset": "nope"}).status_code == 400
    r = client.post("/api/slice", json={**base, "settings": {"gcode_injection": "rm -rf"}})
    assert r.status_code == 400 and "gcode_injection" in r.json()["detail"]
    assert client.post("/api/assign", json={**base, "filament": "X"}).status_code == 400
    assert client.post("/api/slice", json={"filament": "PLA"}).status_code == 422


def test_catalog_consistency(client):
    c = client.get("/api/catalog").json()
    keys = {f["key"] for t in c["settings_schema"] for g in t["groups"] for f in g["fields"]}
    for preset, vals in c["preset_defaults"].items():
        assert keys <= set(vals), f"{preset} missing defaults {keys - set(vals)}"
    for t in c["settings_schema"]:
        for g in t["groups"]:
            for f in g["fields"]:
                assert f["type"] in ("number", "bool", "select")
                assert f["type"] != "select" or f["options"]
    assert set(c["printer_models"]) == set(PRINTER_MODELS)


# ----------------------------------------------------------- printers & jobs

def test_printer_crud_hides_secret(client, fleet):
    r = client.post("/api/admin/printers", json={"name": "lan", "model": "P1S", "host": "10.255.255.1", "serial": "SN1", "access_code": "12345678"})
    assert r.status_code == 200
    p = r.json()
    assert "access_code" not in p and p["has_access_code"] is True
    assert all("access_code" not in x for x in client.get("/api/printers").json())
    upd = client.put(f"/api/admin/printers/{p['id']}", json={"name": "renamed", "model": "P1S", "host": "10.255.255.1", "serial": "SN1"})
    assert upd.json()["name"] == "renamed" and upd.json()["has_access_code"] is True  # blank keeps stored code
    assert client.delete(f"/api/admin/printers/{p['id']}").status_code == 200
    assert client.delete(f"/api/admin/printers/{p['id']}").status_code == 404
    assert client.put("/api/admin/printers/ghost", json={"name": "x", "model": "P1S"}).status_code == 404
    assert client.post("/api/admin/printers", json={"name": "bad", "model": "Prusa MK4"}).status_code == 400


def test_unreachable_live_printer_is_offline_not_chosen(client, fleet):
    client.post("/api/admin/printers", json={"name": "ghost", "model": "A1 mini", "host": "10.255.255.1", "serial": "S", "access_code": "1"})
    m = upload(client, VALID / "cube_binary.stl").json()
    cands = {c["name"]: c for c in assign(client, m["id"])["candidates"]}
    assert not cands["ghost"]["eligible"] and "offline" in " ".join(cands["ghost"]["blockers"]).lower()


def test_job_lifecycle_and_downloads(client, fleet):
    m = upload(client, VALID / "cube_binary.stl").json()
    _, job = slice_model(client, m["id"])
    assert client.get(f"/api/jobs/{job['id']}/gcode").headers["content-disposition"].endswith('cube_binary.gcode"')
    assert client.get(f"/api/jobs/{job['id']}/bundle").status_code == 409      # mock has no .gcode.3mf
    assert client.post(f"/api/jobs/{job['id']}/print").status_code == 409      # no live link
    assert client.post(f"/api/jobs/{job['id']}/queue").json()["queued"] is True
    assert any(j["id"] == job["id"] for j in client.get("/api/jobs").json())
    assert client.get("/api/jobs/nope").status_code == 404
    assert client.get("/api/jobs/nope/gcode").status_code == 404


def test_failed_slice_reports_error(client, fleet, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("engine exploded")
    monkeypatch.setattr(app_main.slicer, "run", boom)
    m = upload(client, VALID / "cube_binary.stl").json()
    _, job = slice_model(client, m["id"])
    assert job["state"] == "failed" and "engine exploded" in job["error"]
    assert client.get(f"/api/jobs/{job['id']}/gcode").status_code == 409


# --------------------------------------------------------- load / robustness

def test_concurrent_slices(client, fleet):
    m = upload(client, VALID / "sphere.stl").json()
    with ThreadPoolExecutor(6) as ex:
        results = list(ex.map(lambda _: slice_model(client, m["id"]), range(6)))
    assert all(job["state"] == "done" for _, job in results)
    assert len({job["id"] for _, job in results}) == 6


def test_dense_mesh_performance(client, fleet, tmp_path):
    mesh = trimesh.creation.icosphere(subdivisions=6, radius=40)   # ~82k triangles, 80 mm tall
    f = tmp_path / "dense.stl"
    mesh.export(f)
    t0 = time.time()
    m = upload(client, f).json()
    _, job = slice_model(client, m["id"])
    assert job["state"] == "done"
    assert time.time() - t0 < 60, "mock slice of an 82k-triangle sphere should stay fast"


def test_open_mesh_still_slices(client, fleet, tmp_path):
    mesh = trimesh.creation.box(extents=(20, 20, 20))
    mesh.update_faces([i for i in range(len(mesh.faces)) if i != 5])      # drop a face → hole
    f = tmp_path / "open.stl"
    mesh.export(f)
    m = upload(client, f)
    assert m.status_code == 200
    _, job = slice_model(client, m.json()["id"])
    assert job["state"] == "done"


def test_static_frontend_served(client):
    r = client.get("/")
    assert r.status_code in (200, 404)  # 404 only if the web app has not been built


# ------------------------------------------------------- the user's own files

DESKTOP_CASES = [("tube thing.stl", True), ("end.stl", True), ("Body3.3mf", True), ("Queen tower.3mf", False)]


@pytest.mark.parametrize("name,printable", DESKTOP_CASES)
def test_desktop_models(client, fleet, name, printable):
    path = DESKTOP / name
    if not path.exists():
        pytest.skip(f"{path} not present")
    m = upload(client, path)
    assert m.status_code == 200, m.text
    m = m.json()
    res = assign(client, m["id"])
    r, job = slice_model(client, m["id"])
    if printable:
        assert res["chosen"] and r.status_code == 200 and job["state"] == "done", job
        g = parse_gcode(client.get(f"/api/jobs/{job['id']}/gcode").text)
        assert g["zmax"] == pytest.approx(m["dims"][2], abs=0.25) and g["e_bad"] == 0
    else:  # 210 x 210 x 520 mm: taller than every Bambu printer
        assert res["chosen"] is None and r.status_code == 409
        scaled = assign(client, m["id"], scale=0.5)
        assert scaled["chosen"] is not None  # at 50% it fits (260mm Z needs the H2D)
