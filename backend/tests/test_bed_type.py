"""Per-printer build plate: admin-configured, default textured PEI, drives both the slice and the start command."""
import time
import uuid

import pytest

from app import main as app_main, store
from app.catalog import BED_TYPES, DEFAULT_BED_TYPE, SETTINGS_SCHEMA
from conftest import VALID, upload, wait_job
from test_print import FakeMQTT, ftps, bundle, make_link, make_job, me  # noqa: F401  (fixtures)


def printer_body(**kw):
    return {"name": "Plate test", "model": "P1S", **kw}


def test_default_is_textured_pei(client):
    p = client.post("/api/admin/printers", json=printer_body()).json()
    assert p["bed_type"] == "Textured PEI Plate" == DEFAULT_BED_TYPE
    assert next(x for x in client.get("/api/printers").json() if x["id"] == p["id"])["bed_type"] == "Textured PEI Plate"


def test_catalog_lists_plates(client):
    c = client.get("/api/catalog").json()
    assert c["bed_types"] == list(BED_TYPES) and c["default_bed_type"] == "Textured PEI Plate"


def test_printer_without_the_field_defaults_to_textured(client):
    pid = uuid.uuid4().hex[:8]
    store.printers.put(pid, {"id": pid, "name": "legacy", "model": "P1S", "status": {"state": "IDLE"}})   # saved before the feature
    assert next(p for p in client.get("/api/printers").json() if p["id"] == pid)["bed_type"] == "Textured PEI Plate"


@pytest.mark.parametrize("plate", list(BED_TYPES))
def test_admin_can_set_each_plate(client, plate):
    p = client.post("/api/admin/printers", json=printer_body(bed_type=plate)).json()
    assert p["bed_type"] == plate
    upd = client.put(f"/api/admin/printers/{p['id']}", json=printer_body(bed_type="Cool Plate")).json()
    assert upd["bed_type"] == "Cool Plate"


def test_invalid_plate_rejected(client):
    r = client.post("/api/admin/printers", json=printer_body(bed_type="Glass"))
    assert r.status_code == 400 and "Unknown build plate" in r.json()["detail"]


def test_only_admins_can_change_it_but_everyone_can_see_it(client):
    from conftest import make_user
    alice = make_user(client, "plate.user@imperial.ac.uk")
    p = client.post("/api/admin/printers", json=printer_body(bed_type="Engineering Plate")).json()
    assert alice.put(f"/api/admin/printers/{p['id']}", json=printer_body(bed_type="Cool Plate")).status_code == 403
    assert next(x for x in alice.get("/api/printers").json() if x["id"] == p["id"])["bed_type"] == "Engineering Plate"


def test_plate_is_no_longer_a_user_setting(client, fleet, monkeypatch):
    keys = {f["key"] for t in SETTINGS_SCHEMA for g in t["groups"] for f in g["fields"]}
    assert "curr_bed_type" not in keys
    assert all("curr_bed_type" not in d for d in client.get("/api/catalog").json()["preset_defaults"].values())
    seen = {}
    real = app_main.slicer.run
    monkeypatch.setattr(app_main.slicer, "run", lambda w, m, pm, pr, f, o: (seen.update(o), real(w, m, pm, pr, f, o))[1])
    m = upload(client, VALID / "cube_binary.stl").json()
    r = client.post("/api/slice", json={"model_id": m["id"], "filament": "PLA", "settings": {"curr_bed_type": "Cool Plate"}})
    assert r.status_code == 200                                           # tolerated, not an error ...
    assert wait_job(client, r.json()["id"])["state"] == "done"
    assert seen["curr_bed_type"] == "Textured PEI Plate"                  # ... but the printer's plate always wins


def test_unknown_settings_are_still_rejected(client, fleet):
    m = upload(client, VALID / "cube_binary.stl").json()
    r = client.post("/api/slice", json={"model_id": m["id"], "filament": "PLA", "settings": {"made_up_key": 1}})
    assert r.status_code == 400 and "made_up_key" in r.json()["detail"]


@pytest.mark.parametrize("preset", ["0.12mm Fine", "0.20mm Standard", "0.20mm Strength", "0.28mm Extra Draft"])
def test_web_app_payload_slices(client, fleet, preset):
    """Regression: the UI posts the catalog's preset defaults (plus edits) straight back as `settings`."""
    defaults = client.get("/api/catalog").json()["preset_defaults"][preset]
    m = upload(client, VALID / "cube_binary.stl").json()
    r = client.post("/api/slice", json={"model_id": m["id"], "filament": "PLA", "preset": preset,
                                        "settings": {**defaults, "sparse_infill_density": 30}, "rotation": [0, 0, 0], "scale": 1})
    assert r.status_code == 200, r.text
    assert wait_job(client, r.json()["id"])["state"] == "done"


def test_slice_uses_the_assigned_printers_plate(client, fleet, monkeypatch):
    seen = {}
    real = app_main.slicer.run

    def spy(workdir, model, printer_model, preset, filament, overrides):
        seen.update(overrides)
        return real(workdir, model, printer_model, preset, filament, overrides)

    monkeypatch.setattr(app_main.slicer, "run", spy)
    for p in client.get("/api/printers").json():                          # make the A1 mini (smallest, preferred) use a cool plate
        if p["model"] == "A1 mini":
            client.put(f"/api/admin/printers/{p['id']}", json={"name": p["name"], "model": p["model"], "bed_type": "Cool Plate",
                                                              "status": p["status"]})
    m = upload(client, VALID / "cube_binary.stl").json()
    job = client.post("/api/slice", json={"model_id": m["id"], "filament": "PLA"}).json()
    job = wait_job(client, job["id"])
    assert job["printer_model"] == "A1 mini" and job["bed_type"] == "Cool Plate"
    assert seen["curr_bed_type"] == "Cool Plate"
    # the default printer path still uses textured PEI
    m2 = upload(client, VALID / "plate_250.stl").json()
    job2 = wait_job(client, client.post("/api/slice", json={"model_id": m2["id"], "filament": "PLA"}).json()["id"])
    assert job2["bed_type"] == "Textured PEI Plate" and seen["curr_bed_type"] == "Textured PEI Plate"


@pytest.mark.parametrize("plate,mqtt", [("Textured PEI Plate", "textured_plate"), ("Cool Plate", "cool_plate"),
                                        ("Engineering Plate", "eng_plate"), ("High Temp Plate", "hot_plate")])
def test_start_command_carries_the_plate(ftps, bundle, plate, mqtt):  # noqa: F811
    pid, link = make_link(ftps.port)
    store.printers.update(pid, bed_type=plate)
    link.send_print(bundle, "x.gcode.3mf")
    assert link.client.published[-1][1]["print"]["bed_type"] == mqtt


def test_start_command_defaults_to_textured_for_old_printers(ftps, bundle):  # noqa: F811
    pid, link = make_link(ftps.port)
    link.send_print(bundle, "x.gcode.3mf")
    assert link.client.published[-1][1]["print"]["bed_type"] == "textured_plate"
