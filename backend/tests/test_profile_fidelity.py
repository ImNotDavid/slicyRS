"""Regression tests for the failed first layer on a real A1 Mini.

Two causes were found: (1) the profile resolver ignored the printer's "template" G-code files, so every slice used a
generic start G-code (no nozzle clean/purge, a purge line past the bed edge); (2) the app overrode the printer's own
settings with hard-coded defaults instead of sending only what the user changed.
"""
import json
import re
import zipfile

import pytest

from app import defaults, profiles
from app.catalog import PRINTER_MODELS, preset_defaults
from test_real_engine import FIX, FOUND, engine  # noqa: F401  (fixture)


# ----------------------------------------------------- edits-only overrides (no slicer needed)

@pytest.fixture()
def orca_mode(monkeypatch):
    monkeypatch.setattr(defaults, "SLICER_MODE", "orca")
    defaults._ui_defaults.cache_clear()
    yield
    defaults._ui_defaults.cache_clear()


def test_untouched_settings_send_nothing_but_the_plate(orca_mode):
    shown = defaults.ui_defaults("0.20mm Standard")
    assert defaults.effective_overrides("0.20mm Standard", shown, "Textured PEI Plate") == {"curr_bed_type": "Textured PEI Plate"}


def test_only_changed_settings_are_sent(orca_mode):
    shown = defaults.ui_defaults("0.20mm Standard")
    sent = defaults.effective_overrides("0.20mm Standard", {**shown, "sparse_infill_density": 40, "enable_support": True}, "Cool Plate")
    assert sent == {"sparse_infill_density": 40, "enable_support": True, "curr_bed_type": "Cool Plate"}


def test_unknown_or_server_managed_keys_are_never_forwarded(orca_mode):
    sent = defaults.effective_overrides("0.20mm Standard", {"gcode_injection": "x", "curr_bed_type": "Cool Plate"}, "Textured PEI Plate")
    assert sent == {"curr_bed_type": "Textured PEI Plate"}


def test_mock_engine_still_gets_complete_values(monkeypatch):
    monkeypatch.setattr(defaults, "SLICER_MODE", "mock")
    sent = defaults.effective_overrides("0.12mm Fine", {}, "Textured PEI Plate")
    assert sent["layer_height"] == 0.12 and sent["curr_bed_type"] == "Textured PEI Plate" and "wall_loops" in sent


@pytest.mark.parametrize("raw,like,expected", [("15%", 15, 15), (["6000"], 5000, 6000), ("1", False, True), ("0", True, False),
                                               ("0.42", 0.4, 0.42), ("gyroid", "grid", "gyroid"), ("bad", 3, 3)])
def test_profile_value_parsing(raw, like, expected):
    assert defaults._parse(raw, like) == expected


# ------------------------------------------------------------ real profiles (needs Bambu Studio)

pytestmark_real = pytest.mark.skipif(FOUND is None, reason="no slicer install found")


@pytestmark_real
@pytest.mark.parametrize("model", list(PRINTER_MODELS))
def test_every_printer_gets_its_real_start_gcode(engine, model):  # noqa: F811
    d = profiles.resolve(profiles.find(PRINTER_MODELS[model]["machine"]))
    start = d["machine_start_gcode"]
    assert "gcode_claim_action" in start, f"{model} would use a generic start G-code"
    assert "Draw the first line" not in start
    assert len(start) > 3000 and len(d.get("machine_end_gcode", "")) > 500 and len(d.get("change_filament_gcode", "")) > 1000


@pytestmark_real
@pytest.mark.parametrize("model", ["A1 mini", "A1", "P1S", "X1C", "H2D"])
def test_sliced_file_starts_with_the_printers_real_routine(engine, tmp_path, model):  # noqa: F811
    r = engine.run(tmp_path, FIX / "cube_binary.stl", model, "0.20mm Standard", "PLA", {"curr_bed_type": "Textured PEI Plate"})
    lines = (tmp_path / "model.gcode").read_text(errors="ignore").splitlines()
    start = lines.index(next(l for l in lines if l.startswith("; EXECUTABLE_BLOCK_START")))
    first_layer = lines.index(next(l for l in lines if l.startswith("; CHANGE_LAYER")))
    section = lines[start:first_layer]
    assert len(section) > 100, f"{model}: start sequence only {len(section)} lines"
    assert any("gcode_claim_action" in l for l in section)
    assert not any("Draw the first line" in l for l in section)
    assert r["layers"] == 100


@pytestmark_real
def test_untouched_panel_equals_the_printers_own_profile(engine, tmp_path, monkeypatch):  # noqa: F811
    """Slicing with the panel left alone must match a pure profile slice (apart from the plate we set)."""
    monkeypatch.setattr(defaults, "SLICER_MODE", "orca")
    defaults._ui_defaults.cache_clear()
    sent = defaults.effective_overrides("0.20mm Standard", defaults.ui_defaults("0.20mm Standard"), "Textured PEI Plate")
    engine.run(tmp_path / "ours", FIX / "cube_binary.stl", "A1 mini", "0.20mm Standard", "PLA", sent)
    engine.run(tmp_path / "pure", FIX / "cube_binary.stl", "A1 mini", "0.20mm Standard", "PLA", {"curr_bed_type": "Textured PEI Plate"})

    def cfg(d):
        return json.loads(zipfile.ZipFile(d / "model.gcode.3mf").read("Metadata/project_settings.config"))

    a, b = cfg(tmp_path / "ours"), cfg(tmp_path / "pure")
    assert {k for k in a if a[k] != b.get(k)} == set()
    defaults._ui_defaults.cache_clear()


@pytestmark_real
def test_old_hard_coded_defaults_did_differ_from_the_profile(engine, tmp_path):  # noqa: F811
    """Documents the bug: sending my old full defaults changed acceleration and travel speed on the A1 mini."""
    engine.run(tmp_path / "old", FIX / "cube_binary.stl", "A1 mini", "0.20mm Standard", "PLA",
               {**preset_defaults("0.20mm Standard"), "curr_bed_type": "Textured PEI Plate"})
    engine.run(tmp_path / "pure", FIX / "cube_binary.stl", "A1 mini", "0.20mm Standard", "PLA", {"curr_bed_type": "Textured PEI Plate"})

    def cfg(d):
        return json.loads(zipfile.ZipFile(d / "model.gcode.3mf").read("Metadata/project_settings.config"))

    a, b = cfg(tmp_path / "old"), cfg(tmp_path / "pure")
    assert {k for k in a if a[k] != b.get(k)} >= {"default_acceleration", "travel_speed"}
