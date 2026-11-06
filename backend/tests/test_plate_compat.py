"""Filament/build-plate compatibility: the allocator must never pick a printer whose fitted plate refuses the filament."""
import pytest

from app import assign, plate_compat
from app.catalog import BED_TYPES, FILAMENTS, PRINTER_MODELS
from conftest import VALID, upload
from test_real_engine import FIX, FOUND, engine  # noqa: F401  (fixture)


def printer(pid, model, plate=None, state="IDLE", **kw):
    p = {"id": pid, "name": pid, "model": model, "status": {"state": state, "ams": []}, **kw}
    if plate:
        p["bed_type"] = plate
    return p


# ------------------------------------------------------------------ the rule

@pytest.mark.parametrize("filament", ["PETG", "ABS", "ASA", "PC", "PA", "PA-CF"])
def test_cool_plate_refuses_high_temperature_materials(filament):
    assert not plate_compat.compatible("P1S", filament, "Cool Plate")


@pytest.mark.parametrize("filament", ["PLA", "PLA-CF", "TPU"])
def test_cool_plate_accepts_pla_and_tpu(filament):
    assert plate_compat.compatible("P1S", filament, "Cool Plate")


@pytest.mark.parametrize("filament", ["PLA", "PLA-CF"])
def test_engineering_plate_refuses_pla_except_on_h2d(filament):
    assert not plate_compat.compatible("X1C", filament, "Engineering Plate")
    assert not plate_compat.compatible("A1 mini", filament, "Engineering Plate")
    assert plate_compat.compatible("H2D", filament, "Engineering Plate")


@pytest.mark.parametrize("plate", ["Textured PEI Plate", "High Temp Plate"])
@pytest.mark.parametrize("filament", list(FILAMENTS))
def test_textured_and_high_temp_plates_take_everything(plate, filament):
    assert plate_compat.compatible("P1S", filament, plate)


def test_unknown_names_do_not_block_twice():
    assert plate_compat.compatible("P1S", "UNOBTAINIUM", "Cool Plate")
    assert plate_compat.compatible("P1S", "PLA", "Glass")


# ------------------------------------------------------------ in the allocator

def test_incompatible_printer_is_ineligible_with_a_clear_reason():
    c = assign.evaluate(printer("a", "P1S", "Cool Plate"), (30, 30, 30), "ABS")
    assert not c.eligible and any("ABS cannot be printed on the Cool Plate" in b for b in c.blockers)


def test_allocator_skips_the_wrong_plate_and_uses_another_printer():
    fleet = [printer("cool", "P1S", "Cool Plate"), printer("pei", "P1S", "Textured PEI Plate")]
    assert assign.choose(fleet, (30, 30, 30), "PETG").printer_id == "pei"
    assert assign.choose(fleet, (30, 30, 30), "PLA") is not None


def test_printer_without_a_plate_setting_counts_as_textured_pei():
    assert assign.evaluate(printer("old", "P1S"), (30, 30, 30), "ABS").eligible


def test_no_eligible_printer_when_every_plate_refuses():
    fleet = [printer("a", "P1S", "Cool Plate"), printer("b", "X1C", "Cool Plate")]
    assert assign.choose(fleet, (30, 30, 30), "ABS") is None


# --------------------------------------------------------------------- the API

def test_api_assignment_explains_plate_mismatch(client, fleet):
    from app import store
    for p in store.printers.all():
        store.printers.update(p["id"], bed_type="Cool Plate")
    m = upload(client, VALID / "cube_binary.stl").json()
    res = client.post("/api/assign", json={"model_id": m["id"], "filament": "ABS"}).json()
    assert res["chosen"] is None
    assert any("cannot be printed on the Cool Plate" in b for c in res["candidates"] for b in c["blockers"])
    r = client.post("/api/slice", json={"model_id": m["id"], "filament": "ABS"})
    assert r.status_code == 409 and "Cool Plate" in r.json()["detail"]
    assert client.post("/api/assign", json={"model_id": m["id"], "filament": "PLA"}).json()["chosen"]   # PLA is fine on cool plate


def test_manual_choice_of_a_wrong_plate_printer_is_refused(client, fleet):
    from app import store
    store.printers.update(fleet["mini"], bed_type="Cool Plate")
    m = upload(client, VALID / "cube_binary.stl").json()
    r = client.post("/api/slice", json={"model_id": m["id"], "filament": "PETG", "printer_id": fleet["mini"]})
    assert r.status_code == 409 and "Cool Plate" in r.json()["detail"]


# ------------------------------------------- against the real slicer (when installed)

real = pytest.mark.skipif(FOUND is None, reason="no slicer install found")


@real
@pytest.mark.parametrize("model", list(PRINTER_MODELS))
def test_static_table_matches_the_slicers_own_profiles(engine, model, monkeypatch):  # noqa: F811
    """If a slicer upgrade changes which plates accept which filaments, this fails and the table must be updated."""
    monkeypatch.setattr(plate_compat, "SLICER_MODE", "orca")
    plate_compat._profile_says.cache_clear()
    for plate in BED_TYPES:
        for filament in FILAMENTS:
            real_answer = plate_compat._profile_says(model, filament, plate)
            assert real_answer is not None, (model, filament, plate)
            refused = plate_compat.STATIC_EXCEPTIONS.get((model, plate), plate_compat.STATIC_INCOMPATIBLE[plate])
            assert real_answer == (filament not in refused), f"{model}: {filament} on {plate}: profile says {real_answer}"
    plate_compat._profile_says.cache_clear()


@real
@pytest.mark.parametrize("filament,plate,model", [("ABS", "Cool Plate", "P1S"), ("PETG", "Cool Plate", "A1 mini"),
                                                  ("PLA", "Engineering Plate", "P1S")])
def test_slicer_really_refuses_what_we_call_incompatible(engine, tmp_path, filament, plate, model):  # noqa: F811
    from app.catalog import preset_defaults
    from app.slicer import SliceError
    assert not plate_compat.compatible(model, filament, plate)
    with pytest.raises(SliceError, match="not compatible with the plate"):
        engine.run(tmp_path, FIX / "cube_binary.stl", model, "0.20mm Standard", filament,
                   {**preset_defaults("0.20mm Standard"), "curr_bed_type": plate})


@real
@pytest.mark.parametrize("filament,plate,model", [("PLA", "Cool Plate", "P1S"), ("TPU", "Cool Plate", "A1 mini"),
                                                  ("PLA", "Engineering Plate", "H2D"), ("ABS", "High Temp Plate", "X1C")])
def test_slicer_really_accepts_what_we_call_compatible(engine, tmp_path, filament, plate, model):  # noqa: F811
    from app.catalog import preset_defaults
    assert plate_compat.compatible(model, filament, plate)
    r = engine.run(tmp_path, FIX / "cube_binary.stl", model, "0.20mm Standard", filament,
                   {**preset_defaults("0.20mm Standard"), "curr_bed_type": plate})
    assert r["layers"] == 100
