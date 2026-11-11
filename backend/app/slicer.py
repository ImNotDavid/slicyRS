"""Run a slice job: real OrcaSlicer CLI, or a perimeter-only mock for dev."""
import json
import math
import re
import shlex
import subprocess
import zipfile
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

import numpy as np
import trimesh

from . import profiles
from .catalog import FIELDS, FILAMENTS, PRINTER_MODELS, preset_nozzle
from .config import ORCA_BIN, SLICE_TIMEOUT_S, SLICER_MODE
from .meshio import load_mesh

_DENSITY_G_CM3 = {"PLA": 1.24, "PETG": 1.27, "ABS": 1.04, "ASA": 1.07, "TPU": 1.21, "PC": 1.2,
                  "PA": 1.14, "PLA-CF": 1.3, "PA-CF": 1.2}


class SliceError(RuntimeError):
    pass


class PlateOverflow(SliceError):
    """The slicer's own arrange could not fit every object on one plate."""


def _orca_value(key: str, value: Any) -> str:
    if isinstance(value, bool):
        return "1" if value else "0"
    if FIELDS.get(key, {}).get("type") == "percent":
        return f"{value:g}%" if isinstance(value, (int, float)) else str(value)
    return f"{value:g}" if isinstance(value, float) else str(value)


def process_config(printer_model: str, preset: str, overrides: dict[str, Any]) -> dict[str, Any]:
    """The full process settings a slice would use (the preset's own values with the overrides on top), for comparing two jobs."""
    if SLICER_MODE != "orca":
        return dict(overrides)        # the mock has no profiles: its overrides already hold every value
    pm = PRINTER_MODELS[printer_model]
    name = profiles.process_name(preset, pm["process_suffix"])
    cfg = profiles.resolve(name) if name else {}
    return {**cfg, **{k: _orca_value(k, v) for k, v in overrides.items()}}


def run(workdir: Path, model_path: Path | list[Path], printer_model: str, preset: str, filament: str,
        overrides: dict[str, Any], object_settings: list[dict[str, Any]] | None = None,
        offset: tuple[float, float] | None = None) -> dict[str, Any]:
    """Slice one model, or several that share a plate (the slicer arranges them). `object_settings` gives each of several
    models its own settings on top of the plate's (the same order as the models). `offset` places a single model by hand: its
    centre goes that many mm from the middle of the printer's bed, and the slicer does not move it."""
    workdir.mkdir(parents=True, exist_ok=True)
    models = list(model_path) if isinstance(model_path, (list, tuple)) else [model_path]
    if SLICER_MODE == "orca":
        return _run_orca(workdir, models, printer_model, preset, filament, overrides, object_settings, offset)
    return _run_mock(workdir, models, printer_model, filament, overrides, offset)


# ---------------------------------------------------------------- OrcaSlicer

def _plate_3mf(workdir: Path, models: list[Path], object_settings: list[dict[str, Any]]) -> Path:
    """One 3MF holding every model as its own object, each with its own settings (Bambu's model_settings.config); the
    slicer still arranges them. Object ids follow the order the objects are added."""
    scene = trimesh.Scene()
    for i, path in enumerate(models):
        scene.add_geometry(load_mesh(path), node_name=f"part{i + 1}", geom_name=f"part{i + 1}")
    raw, out = workdir / "objects.3mf", workdir / "plate.3mf"
    scene.export(str(raw), file_type="3mf")
    cfg = ['<?xml version="1.0" encoding="UTF-8"?>', "<config>"]
    for i, ov in enumerate(object_settings):
        cfg.append(f' <object id="{i + 1}"><metadata key="name" value="part{i + 1}"/>')
        cfg += [f'  <metadata key="{escape(k)}" value="{escape(str(v))}"/>' for k, v in ov.items()]
        cfg.append(" </object>")
    cfg.append("</config>")
    with zipfile.ZipFile(raw) as zin, zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zout:
        for n in zin.namelist():
            zout.writestr(n, zin.read(n))
        zout.writestr("Metadata/model_settings.config", "\n".join(cfg))
    return out


def _placed_3mf(workdir: Path, model: Path, bed: tuple[float, float, float], offset: tuple[float, float]) -> Path:
    """A 3MF holding the model with its centre at (bed middle + offset), sitting on the bed; used with the slicer's arrange switched off."""
    mesh = load_mesh(model)
    lo, hi = mesh.bounds
    mesh.apply_translation([bed[0] / 2 + offset[0] - (lo[0] + hi[0]) / 2, bed[1] / 2 + offset[1] - (lo[1] + hi[1]) / 2, -lo[2]])
    scene = trimesh.Scene()
    scene.add_geometry(mesh, node_name="part", geom_name="part")
    out = workdir / "placed.3mf"
    scene.export(str(out), file_type="3mf")
    return out


def _run_orca(workdir: Path, models: list[Path], printer_model: str, preset: str, filament: str,
              overrides: dict[str, Any], object_settings: list[dict[str, Any]] | None = None,
              offset: tuple[float, float] | None = None) -> dict[str, Any]:
    pm = PRINTER_MODELS[printer_model]
    nozzle = preset_nozzle(preset)
    machine = profiles.machine_name(pm["machine"], nozzle)
    process = profiles.process_name(preset, pm["process_suffix"])
    fil = profiles.filament_name(FILAMENTS[filament]["orca"], pm["process_suffix"], nozzle)
    if not (machine and process and fil):
        raise SliceError(f"Missing OrcaSlicer presets (machine={machine}, process={process}, filament={fil})")

    process_json = profiles.resolve(process)
    for k, v in overrides.items():
        process_json[k] = _orca_value(k, v)
    machine_json, fil_json = profiles.resolve(machine), profiles.resolve(fil)
    # The CLI refuses presets whose compatibility list does not name the loaded printer.
    machine_json["printer_settings_id"] = machine
    process_json["print_settings_id"] = process
    process_json["compatible_printers"] = [machine, "machine", "machine.json"]
    fil_json["filament_settings_id"] = [fil]
    fil_json["compatible_printers"] = [machine, "machine", "machine.json"]
    machine_file, process_file, fil_file = (workdir / n for n in ("machine.json", "process.json", "filament.json"))
    machine_file.write_text(json.dumps(machine_json))
    process_file.write_text(json.dumps(process_json))
    fil_file.write_text(json.dumps(fil_json))

    out = workdir / "out"
    out.mkdir(exist_ok=True)
    n_models = len(models)
    placed = bool(offset and any(offset) and n_models == 1)
    if placed:
        models = [_placed_3mf(workdir, models[0], pm["bed"], offset)]
    if object_settings and n_models > 1 and any(object_settings):
        models = [_plate_3mf(workdir, models, object_settings)]
    cmd = [*_orca_cmd(), "--slice", "0", "--arrange", "0" if placed else "1",
           "--load-settings", f"{machine_file};{process_file}", "--load-filaments", str(fil_file),
           "--outputdir", str(out), "--export-3mf", "sliced.gcode.3mf", *[str(m.resolve()) for m in models]]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=SLICE_TIMEOUT_S, cwd=workdir)
    (workdir / "slicer.log").write_text(proc.stdout + "\n" + proc.stderr)
    gcodes = sorted(out.glob("*.gcode"))
    gcode = gcodes[0] if gcodes else None
    if proc.returncode != 0 or gcode is None:
        raise SliceError(_failure_reason(proc, out))
    if n_models > 1 and len(gcodes) > 1:
        raise PlateOverflow("The parts do not all fit on one plate")
    final = workdir / "model.gcode"
    gcode.replace(final)
    bundle = out / "sliced.gcode.3mf"
    if bundle.exists():
        bundle.replace(workdir / "model.gcode.3mf")
    return {"gcode": final.name, "bundle": "model.gcode.3mf" if (workdir / "model.gcode.3mf").exists() else None,
            **parse_stats(final)}


def _orca_cmd() -> list[str]:
    # ORCA_BIN may be a plain path (Windows paths contain spaces/backslashes) or a wrapper like "xvfb-run -a orca"
    return [ORCA_BIN] if Path(ORCA_BIN).exists() else shlex.split(ORCA_BIN)


def _failure_reason(proc: subprocess.CompletedProcess, out: Path) -> str:
    """Prefer the engine's own explanation (result.json) over raw log lines."""
    try:
        reason = json.loads((out / "result.json").read_text(encoding="utf-8")).get("error_string")
        if reason:
            return f"Slicer error: {reason}"
    except (OSError, ValueError):
        pass
    tail = (proc.stderr or proc.stdout).strip().splitlines()[-5:]
    return "Slicer failed: " + " | ".join(tail)


def _dur_to_s(text: str) -> int:
    total = 0
    for n, unit in re.findall(r"(\d+)\s*([dhms])", text):
        total += int(n) * {"d": 86400, "h": 3600, "m": 60, "s": 1}[unit]
    return total


def parse_stats(gcode: Path) -> dict[str, Any]:
    """Pull time / filament / layer count from the OrcaSlicer header+footer comments."""
    size = gcode.stat().st_size
    with open(gcode, "rb") as fh:
        head = fh.read(200_000).decode("utf-8", "ignore")
        fh.seek(max(0, size - 200_000))
        tail = fh.read().decode("utf-8", "ignore")
    text = head + "\n" + tail
    stats: dict[str, Any] = {}
    if m := re.search(r"total estimated time:\s*([^;\n]+)", text):
        stats["time_s"] = _dur_to_s(m.group(1))
    elif m := re.search(r"estimated printing time[^=]*=\s*([^\n]+)", text):
        stats["time_s"] = _dur_to_s(m.group(1))
    if m := re.search(r"total filament weight \[g\]\s*:\s*([\d.]+)", text) or \
            re.search(r"filament used \[g\]\s*=\s*([\d.]+)", text):
        stats["filament_g"] = float(m.group(1))
    if m := re.search(r"total layer number:\s*(\d+)", text):
        stats["layers"] = int(m.group(1))
    return stats


# ---------------------------------------------------------------------- mock

def _run_mock(workdir: Path, models: list[Path], printer_model: str, filament: str,
              overrides: dict[str, Any], offset: tuple[float, float] | None = None) -> dict[str, Any]:   # per-object settings are not modelled by the mock
    """Perimeter-only slicer: good enough to drive and demo the G-code viewer."""
    lh = float(overrides.get("layer_height", 0.2))
    width = float(overrides.get("line_width", 0.42))
    speed = float(overrides.get("outer_wall_speed", 200))
    bed = PRINTER_MODELS[printer_model]["bed"]
    meshes = [load_mesh(m) for m in models]
    x = 0.0                                   # mock arrange: parts side by side along X, 10 mm apart
    for mesh in meshes:
        mesh.apply_translation([x - mesh.bounds[0][0], -(mesh.bounds[0][1] + mesh.bounds[1][1]) / 2, -mesh.bounds[0][2]])
        x = mesh.bounds[1][0] + 10
    if len(meshes) > 1 and x - 10 > bed[0]:
        raise PlateOverflow("The parts do not all fit on one plate")
    mesh = trimesh.util.concatenate(meshes)
    mesh.apply_translation([-(mesh.bounds[0][0] + mesh.bounds[1][0]) / 2, 0, 0])
    cx, cy = bed[0] / 2 + (offset[0] if offset else 0), bed[1] / 2 + (offset[1] if offset else 0)
    height = float(mesh.bounds[1][2])
    n_layers = max(1, int(math.ceil(height / lh)))
    zs = np.minimum((np.arange(n_layers) + 1) * lh, height) - lh / 2 + 1e-6
    sections = mesh.section_multiplane([0, 0, 0], [0, 0, 1], zs - 0.0)

    lines = ["; generated by slicyRS mock slicer (perimeters only)", "G90", "M83", "G28"]
    e_per_mm = width * lh / (math.pi * (1.75 / 2) ** 2)
    length = 0.0
    for i, sec in enumerate(sections):
        z = round(min((i + 1) * lh, height), 3)
        lines += [f"; LAYER_CHANGE", f";Z:{z}", f"G1 Z{z} F1200"]
        if sec is None:
            continue
        for poly in sec.discrete:
            pts = np.asarray(poly)[:, :2]
            if len(pts) < 2:
                continue
            lines.append(f"G1 X{pts[0][0] + cx:.3f} Y{pts[0][1] + cy:.3f} F{int(speed * 60)}")
            for a, b in zip(pts[:-1], pts[1:]):
                d = float(np.hypot(*(b - a)))
                if d < 1e-3:  # degenerate edge (e.g. sphere poles): would round to E0
                    continue
                length += d
                lines.append(f"G1 X{b[0] + cx:.3f} Y{b[1] + cy:.3f} E{d * e_per_mm:.5f}")
    lines.append("M104 S0\nM140 S0")
    gcode = workdir / "model.gcode"
    gcode.write_text("\n".join(lines))
    vol_mm3 = length * width * lh
    grams = vol_mm3 / 1000 * _DENSITY_G_CM3.get(filament, 1.24)
    return {"gcode": gcode.name, "bundle": None, "layers": n_layers,
            "time_s": int(length / max(speed, 1) + n_layers * 4), "filament_g": round(grams, 1),
            "mock": True}
