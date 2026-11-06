"""Slices with a real installed engine (Bambu Studio / OrcaSlicer). Skipped when none is found."""
import importlib
import os
import re
from pathlib import Path

import pytest

CANDIDATES = [
    # inside the Docker image (and anywhere the engine is configured explicitly through the environment)
    (os.environ.get("ORCA_BIN", ""), os.environ.get("ORCA_PROFILES_DIR", "")),
    ("C:/Program Files/Bambu Studio/bambu-studio.exe", "C:/Program Files/Bambu Studio/resources/profiles"),
    ("C:/Program Files/OrcaSlicer/orca-slicer.exe", "C:/Program Files/OrcaSlicer/resources/profiles"),
]
FOUND = next(((b, p) for b, p in CANDIDATES if b and p and Path(b).is_file()), None)
pytestmark = pytest.mark.skipif(FOUND is None, reason="no slicer install found")

FIX = Path(__file__).parent / "fixtures" / "valid"


@pytest.fixture()
def engine(monkeypatch):
    from app import profiles, slicer
    monkeypatch.setattr(slicer, "SLICER_MODE", "orca")
    monkeypatch.setattr(slicer, "ORCA_BIN", FOUND[0])
    monkeypatch.setattr(profiles, "ORCA_PROFILES_DIR", Path(FOUND[1]))
    profiles._index.cache_clear()
    yield slicer
    profiles._index.cache_clear()


@pytest.mark.parametrize("printer", ["A1 mini", "P1S", "X1C", "H2D"])
@pytest.mark.parametrize("filament", ["PLA", "PETG", "ABS", "TPU"])
def test_every_printer_and_filament_slices(engine, tmp_path, printer, filament):
    from app.catalog import preset_defaults
    r = engine.run(tmp_path, FIX / "cube_binary.stl", printer, "0.20mm Standard", filament, preset_defaults("0.20mm Standard"))
    g = (tmp_path / "model.gcode").read_text(errors="ignore")
    assert r["layers"] == 100 and r["time_s"] > 0 and r["filament_g"] > 0
    assert {"Outer wall", "Sparse infill"} <= set(re.findall(r"^; FEATURE: (.+)$", g, re.M))
    assert (tmp_path / "model.gcode.3mf").exists()


def test_settings_reach_the_engine(engine, tmp_path):
    from app.catalog import preset_defaults
    base = preset_defaults("0.20mm Standard")
    a = engine.run(tmp_path / "a", FIX / "cube_binary.stl", "P1S", "0.20mm Standard", "PLA", {**base, "sparse_infill_density": 5})
    b = engine.run(tmp_path / "b", FIX / "cube_binary.stl", "P1S", "0.20mm Standard", "PLA", {**base, "sparse_infill_density": 60})
    assert b["filament_g"] > a["filament_g"] * 1.2
    c = engine.run(tmp_path / "c", FIX / "cube_binary.stl", "P1S", "0.20mm Standard", "PLA", {**base, "layer_height": 0.1})
    assert c["layers"] == 199  # 0.2 mm first layer + 199 x 0.1 mm


def test_incompatible_plate_gives_a_readable_error(engine, tmp_path):
    """ABS cannot print on a cool plate: the engine's own explanation must reach the user."""
    import pytest as _pytest
    from app.catalog import preset_defaults
    from app.slicer import SliceError
    with _pytest.raises(SliceError, match="not compatible with the plate"):
        engine.run(tmp_path, FIX / "cube_binary.stl", "P1S", "0.20mm Standard", "ABS",
                   {**preset_defaults("0.20mm Standard"), "curr_bed_type": "Cool Plate"})


def test_textured_pei_works_for_abs_and_petg(engine, tmp_path):
    from app.catalog import preset_defaults
    for fil in ("ABS", "PETG"):
        r = engine.run(tmp_path / fil, FIX / "cube_binary.stl", "A1 mini", "0.20mm Standard", fil,
                       {**preset_defaults("0.20mm Standard"), "curr_bed_type": "Textured PEI Plate"})
        assert r["layers"] == 100
