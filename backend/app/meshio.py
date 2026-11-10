"""Mesh loading, transforms and bounding boxes (trimesh)."""
from pathlib import Path

import numpy as np
import trimesh

STEP_EXT = {".step", ".stp"}
SUPPORTED = {".stl", ".3mf", ".obj"} | STEP_EXT


def load_mesh(path: Path) -> trimesh.Trimesh:
    try:
        loaded = trimesh.load(str(path), force="mesh")
    except Exception:
        # some 3MF/OBJ files only load as a scene; merge its bodies
        scene = trimesh.load(str(path))
        geoms = list(scene.geometry.values()) if isinstance(scene, trimesh.Scene) else [scene]
        loaded = trimesh.util.concatenate([g for g in geoms if isinstance(g, trimesh.Trimesh)])
    if isinstance(loaded, trimesh.Scene):
        loaded = trimesh.util.concatenate(list(loaded.geometry.values()))
    if path.suffix.lower() == ".3mf":
        loaded.apply_scale(_threemf_unit_mm(path))
    if path.suffix.lower() in STEP_EXT:
        loaded.apply_scale(1000.0)  # cascadio emits metres; slicing works in mm
    if not isinstance(loaded, trimesh.Trimesh) or len(loaded.faces) == 0:
        raise ValueError("File contains no printable geometry")
    from . import meshcheck   # late import: meshcheck uses this package's helpers
    loaded.metadata["repairs"] = meshcheck.repair(loaded)
    return loaded


_UNIT_MM = {"micron": 0.001, "millimeter": 1.0, "centimeter": 10.0, "inch": 25.4, "foot": 304.8, "meter": 1000.0}


def _threemf_unit_mm(path: Path) -> float:
    """3MF files declare their unit on the <model> element; trimesh ignores it."""
    import re
    import zipfile
    try:
        with zipfile.ZipFile(path) as z:
            name = next(n for n in z.namelist() if n.lower().endswith(".model"))
            head = z.open(name).read(4096).decode("utf-8", "ignore")
        m = re.search(r'<model[^>]*\sunit="(\w+)"', head)
        return _UNIT_MM.get(m.group(1), 1.0) if m else 1.0
    except (StopIteration, zipfile.BadZipFile, KeyError):
        return 1.0


def apply_transform(mesh: trimesh.Trimesh, rotation_deg=(0, 0, 0), scale=1.0) -> trimesh.Trimesh:
    """Rotate (XYZ degrees), scale uniformly, then drop to z=0 and centre on XY."""
    m = mesh.copy()
    if scale != 1.0:
        m.apply_scale(float(scale))
    rx, ry, rz = (np.radians(a) for a in rotation_deg)
    for angle, axis in ((rx, [1, 0, 0]), (ry, [0, 1, 0]), (rz, [0, 0, 1])):
        if angle:
            m.apply_transform(trimesh.transformations.rotation_matrix(angle, axis))
    lo, hi = m.bounds
    m.apply_translation([-(lo[0] + hi[0]) / 2, -(lo[1] + hi[1]) / 2, -lo[2]])
    return m


def dims(mesh: trimesh.Trimesh) -> tuple[float, float, float]:
    lo, hi = mesh.bounds
    return tuple(round(float(v), 3) for v in (hi - lo))  # type: ignore[return-value]


def export_stl(mesh: trimesh.Trimesh, path: Path) -> None:
    mesh.export(str(path), file_type="stl")
