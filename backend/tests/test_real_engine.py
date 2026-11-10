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


def test_several_parts_share_one_plate_and_too_many_are_refused(engine, tmp_path):
    cube, sphere = FIX / "cube_binary.stl", FIX / "sphere.stl"
    alone = engine.run(tmp_path / "one", [cube], "A1 mini", "0.20mm Standard", "PLA", {})
    both = engine.run(tmp_path / "two", [cube, sphere], "A1 mini", "0.20mm Standard", "PLA", {})
    assert both["time_s"] > alone["time_s"] and both["filament_g"] > alone["filament_g"]     # one plate carrying both parts
    with pytest.raises(engine.SliceError):                                                  # two 250 mm plates cannot share a 256 mm bed
        engine.run(tmp_path / "big", [FIX / "plate_250.stl"] * 2, "X1C", "0.20mm Standard", "PLA", {})


# Settings jobs on a shared plate may differ in must really be honoured per object by the pinned slicer, or a part would
# silently print with another job's settings. Each case changes the setting on both parts and expects a visible change.
PER_OBJECT_CASES = {
    "outer_wall_line_width": ({}, 0.6), "inner_wall_line_width": ({}, 0.7), "ironing_type": ({}, "top"), "wall_loops": ({}, 5),
    "top_shell_layers": ({}, 8), "bottom_shell_layers": ({}, 7), "sparse_infill_density": ({}, "40%"),
    "sparse_infill_pattern": ({}, "gyroid"), "outer_wall_speed": ({}, 50), "inner_wall_speed": ({}, 20),
    "sparse_infill_speed": ({}, 60), "top_surface_speed": ({}, 10), "enable_support": ({}, 1),
    "support_threshold_angle": ({"enable_support": 1}, 90), "raft_layers": ({}, 2), "brim_type": ({"brim_width": 15}, "outer_only"),
    "brim_width": ({"brim_type": "outer_only"}, 20), "fuzzy_skin": ({}, "allwalls"),
}


def plate_stats(engine, tmp_path, tag, settings):
    models = [FIX / "cube_binary.stl", FIX / "sphere.stl"]
    return engine.run(tmp_path / tag, models, "A1 mini", "0.20mm Standard", "PLA", {}, object_settings=settings)


def changed(a, b):
    return abs(b["time_s"] - a["time_s"]) > 0.015 * a["time_s"] or abs(b["filament_g"] - a["filament_g"]) > 0.25


def test_every_setting_a_shared_plate_lets_parts_differ_in_is_honoured_per_object(engine, tmp_path):
    from app.plates import PER_OBJECT
    base = plate_stats(engine, tmp_path, "base", [{}, {}])
    untested = PER_OBJECT - set(PER_OBJECT_CASES) - {"line_width", "skeleton_infill_density", "skin_infill_density"}
    assert not untested, f"add a case for {sorted(untested)}"
    ignored = []
    for key, (ctx, alt) in PER_OBJECT_CASES.items():
        ref = plate_stats(engine, tmp_path, "ref_" + key, [ctx, ctx]) if ctx else base
        got = plate_stats(engine, tmp_path, key, [{**ctx, key: alt}] * 2)
        if not changed(ref, got):
            ignored.append(key)
    assert not ignored, f"the slicer ignored these as per-object settings: {ignored}"


def test_each_part_gets_its_own_settings_in_the_order_given(engine, tmp_path):
    base = plate_stats(engine, tmp_path, "b", [{}, {}])
    first = plate_stats(engine, tmp_path, "f", [{"sparse_infill_density": "100%"}, {}])
    second = plate_stats(engine, tmp_path, "s", [{}, {"sparse_infill_density": "100%"}])
    # the cube (first) is the bigger part, so solid infill on it costs more filament than on the sphere (second)
    assert first["filament_g"] - base["filament_g"] > second["filament_g"] - base["filament_g"] + 3
    assert second["filament_g"] > base["filament_g"] + 3


@pytest.mark.parametrize("filament", ["PLA", "TPU", "ABS"])
def test_every_print_goal_slices_with_the_real_engine(engine, tmp_path, filament):
    from app import goals
    from app.defaults import effective_overrides
    for g in goals.GOALS:
        r = goals.resolve(g["id"], filament)
        got = engine.run(tmp_path / f"{g['id']}-{filament}", FIX / "cube_binary.stl", "P1S", r["preset"], filament,
                         effective_overrides(r["preset"], r["settings"], "Textured PEI Plate"))
        assert got["time_s"] > 0 and got["filament_g"] > 0
