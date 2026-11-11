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
    r = engine.run(tmp_path, FIX / "cube_binary.stl", printer, "0.20mm Standard", filament, {"curr_bed_type": "Textured PEI Plate"})
    g = (tmp_path / "model.gcode").read_text(errors="ignore")
    assert r["layers"] == 100 and r["time_s"] > 0 and r["filament_g"] > 0
    assert {"Outer wall", "Sparse infill"} <= set(re.findall(r"^; FEATURE: (.+)$", g, re.M))
    assert (tmp_path / "model.gcode.3mf").exists()


def test_settings_reach_the_engine(engine, tmp_path):
    base: dict = {}      # only what the user changed is sent; the printer's own profile supplies the rest
    a = engine.run(tmp_path / "a", FIX / "cube_binary.stl", "P1S", "0.20mm Standard", "PLA", {**base, "sparse_infill_density": 5})
    b = engine.run(tmp_path / "b", FIX / "cube_binary.stl", "P1S", "0.20mm Standard", "PLA", {**base, "sparse_infill_density": 60})
    assert b["filament_g"] > a["filament_g"] * 1.2
    c = engine.run(tmp_path / "c", FIX / "cube_binary.stl", "P1S", "0.20mm Standard", "PLA", {**base, "layer_height": 0.1})
    assert c["layers"] == 199  # 0.2 mm first layer + 199 x 0.1 mm


def test_incompatible_plate_gives_a_readable_error(engine, tmp_path):
    """ABS cannot print on a cool plate: the engine's own explanation must reach the user."""
    import pytest as _pytest
    from app.slicer import SliceError
    with _pytest.raises(SliceError, match="not compatible with the plate"):
        engine.run(tmp_path, FIX / "cube_binary.stl", "P1S", "0.20mm Standard", "ABS", {"curr_bed_type": "Cool Plate"})


def test_textured_pei_works_for_abs_and_petg(engine, tmp_path):
    for fil in ("ABS", "PETG"):
        r = engine.run(tmp_path / fil, FIX / "cube_binary.stl", "A1 mini", "0.20mm Standard", fil,
                       {"curr_bed_type": "Textured PEI Plate"})
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


def test_a_thumbnail_added_to_a_real_bundle_leaves_it_valid(engine, tmp_path):
    import zipfile
    from app import thumbnail
    from app.meshio import load_mesh
    got = engine.run(tmp_path / "thumb", FIX / "cube_binary.stl", "P1S", "0.20mm Standard", "PLA", {})
    bundle = tmp_path / "thumb" / got["bundle"]
    before = zipfile.ZipFile(bundle).read("Metadata/plate_1.gcode.md5")
    assert thumbnail.add_to_bundle(bundle, load_mesh(FIX / "cube_binary.stl"))
    z = zipfile.ZipFile(bundle)
    assert z.testzip() is None and z.read("Metadata/plate_1.gcode.md5") == before
    assert {"Metadata/plate_1.png", "Metadata/plate_1_small.png"} <= set(z.namelist())
    assert b"/Metadata/plate_1.png" in z.read("_rels/.rels")                      # the bundle already pointed at these files


@pytest.mark.parametrize("nozzle", [0.2, 0.4, 0.6, 0.8])
def test_every_printer_model_slices_for_every_nozzle_size_with_that_nozzle(engine, tmp_path, nozzle):
    import re
    from app.catalog import PRINTER_MODELS, PROCESS_PRESETS
    preset = next(p["id"] for p in PROCESS_PRESETS if p["nozzle"] == nozzle and "Standard" in p["id"] and p["layer_height"] >= 0.1)
    for model in PRINTER_MODELS:
        got = engine.run(tmp_path / f"{model}-{nozzle}", FIX / "cube_binary.stl", model, preset, "PLA", {})
        text = (tmp_path / f"{model}-{nozzle}" / got["gcode"]).read_text(errors="ignore")
        assert re.search(rf"nozzle_diameter\s*=\s*{nozzle:g}\b", text), (model, nozzle)
        assert got["time_s"] > 0


def _other_value(f, current):
    """A different, valid value for a setting (for the sweep below)."""
    t = f["type"]
    if t == "bool":
        return not bool(current)
    if t == "select":
        vals = [o["value"] for o in f["options"]]
        return next((v for v in vals if v != current), vals[0])
    lo, hi = f.get("min", -1e6), f.get("max", 1e6)
    if t == "percent":
        return min(hi, max(lo, 30 if current != 30 else 40))
    if t == "number_or_percent":
        return "50%" if str(current) != "50%" else "60%"
    base = float(current) if isinstance(current, (int, float)) else float(f.get("default", 0))
    v = min(hi, max(lo, base * 1.2 + (1 if f.get("integer") else 0.05)))
    return int(v) if f.get("integer") else round(v, 3)


def test_the_engine_accepts_every_setting_the_panel_can_send(engine, tmp_path, monkeypatch):
    """Changing any one setting of the generated schema must still slice: a setting the engine rejects would fail a user's job."""
    from app import defaults
    from app.catalog import FIELDS, validate_settings
    monkeypatch.setattr(defaults, "SLICER_MODE", "orca")
    defaults._ui_defaults.cache_clear()
    base = defaults.ui_defaults("0.20mm Standard")
    rejected = {}
    for key, f in FIELDS.items():
        value = _other_value(f, base.get(key))
        assert validate_settings({key: value}) is None, (key, value)
        try:
            engine.run(tmp_path / key, FIX / "cube_binary.stl", "P1S", "0.20mm Standard", "PLA",
                       {**defaults.effective_overrides("0.20mm Standard", {key: value}, "Textured PEI Plate")})
        except engine.SliceError as e:
            rejected[key] = str(e)[:120]
    defaults._ui_defaults.cache_clear()
    assert not rejected, rejected


def _x_extent(gcode):
    import re
    xs = [float(m.group(1)) for line in open(gcode) if line.startswith("G1") and " E" in line for m in [re.search(r" X([-\d.]+)", line)] if m]
    ys = [float(m.group(1)) for line in open(gcode) if line.startswith("G1") and " E" in line for m in [re.search(r" Y([-\d.]+)", line)] if m]
    return (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2


def test_a_part_placed_by_hand_is_printed_where_it_was_put(engine, tmp_path):
    """Zero offset is where the slicer's own arrange puts a part; every other offset moves it by exactly that far."""
    plate = {"curr_bed_type": "Textured PEI Plate"}
    engine.run(tmp_path / "arranged", FIX / "cube_binary.stl", "P1S", "0.20mm Standard", "PLA", plate)
    ax, ay = _x_extent(tmp_path / "arranged" / "model.gcode")
    for tag, off in (("zero", (0.0, 0.0)), ("moved", (30.0, -20.0)), ("corner", (-80.0, 70.0))):
        engine.run(tmp_path / tag, FIX / "cube_binary.stl", "P1S", "0.20mm Standard", "PLA", plate, offset=off)
        cx, cy = _x_extent(tmp_path / tag / "model.gcode")
        assert cx == pytest.approx(ax + off[0], abs=1.0) and cy == pytest.approx(ay + off[1], abs=1.0), (tag, cx, cy)


def test_the_slicers_arrange_gives_every_part_a_place_that_does_not_overlap_another(engine, tmp_path):
    """The positions and turns it returns must describe a layout where no two parts touch, with the parts in the order given."""
    import numpy as np
    from shapely import affinity
    from shapely.geometry import MultiPoint
    from app.catalog import PRINTER_MODELS
    from app.meshio import load_mesh
    files = [FIX / f for f in ("cube_binary.stl", "sphere.stl", "cube_binary.stl", "tube.stl", "sphere.stl", "plate_250.stl")[:5]]
    got = engine.arrange(tmp_path, files, "P1S", "0.20mm Standard", "PLA")
    assert len(got) == 5
    bed = PRINTER_MODELS["P1S"]["bed"]
    shapes = []
    for f, g in zip(files, got):
        v = load_mesh(f).vertices[:, :2]
        v = v - (v.min(axis=0) + v.max(axis=0)) / 2
        poly = affinity.rotate(MultiPoint(v).convex_hull, g["rotation_z"], origin=(0, 0))
        b = poly.bounds
        poly = affinity.translate(poly, g["position"][0] - (b[0] + b[2]) / 2, g["position"][1] - (b[1] + b[3]) / 2)   # offsets are bounding-box centres
        minx, miny, maxx, maxy = poly.bounds
        assert -bed[0] / 2 <= minx and maxx <= bed[0] / 2 and -bed[1] / 2 <= miny and maxy <= bed[1] / 2, (f.name, g)
        shapes.append(poly)
    for i in range(5):
        for j in range(i + 1, 5):
            assert shapes[i].distance(shapes[j]) > 0.5, (i, j, got)                  # the same shape twice would overlap if mixed up


def test_a_plate_laid_out_by_hand_prints_each_part_where_it_was_put(engine, tmp_path):
    """Two parts placed 80 mm apart come out 80 mm apart in the G-code, each at its own offset."""
    from app.meshio import apply_transform, export_stl, load_mesh
    a, b = tmp_path / "a.stl", tmp_path / "b.stl"
    export_stl(apply_transform(load_mesh(FIX / "cube_binary.stl"), (0, 0, 0), 1.0), a)
    export_stl(apply_transform(load_mesh(FIX / "sphere.stl"), (0, 0, 0), 1.0), b)
    plate = {"curr_bed_type": "Textured PEI Plate"}
    engine.run(tmp_path / "ref", FIX / "cube_binary.stl", "P1S", "0.20mm Standard", "PLA", plate)
    rx, _ = _x_extent(tmp_path / "ref" / "model.gcode")
    got = engine.run(tmp_path / "plate", [a, b], "P1S", "0.20mm Standard", "PLA", plate, placements=[(a, (-40.0, 0.0)), (b, (40.0, 10.0))])
    text = (tmp_path / "plate" / got["gcode"]).read_text(errors="ignore")
    import re
    xs = [float(m.group(1)) for line in text.splitlines() if line.startswith("G1") and " E" in line for m in [re.search(r" X([-\d.]+)", line)] if m]
    assert min(xs) == pytest.approx(rx - 40 - 20, abs=1.5) and max(xs) == pytest.approx(rx + 40 + 15, abs=1.5)     # cube 40 wide, sphere 30 wide
