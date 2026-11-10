"""Lay a part flat or turn it to need the least support (Milestone 1.2).

Candidates are the supporting planes of the part (the faces of its convex hull) plus the six axis directions: for each, the
part is turned so that plane lies on the bed. "Flat" takes the one with the most surface touching the bed; "auto" takes the one
that needs the least support, preferring more contact and a lower part. The answer is a rotation in the same X, Y, Z degrees
the rest of the app uses, applied to the part as uploaded.
"""
import math
from typing import Any

import numpy as np
import trimesh

from . import meshio

OVERHANG_SIN = math.sin(math.radians(45))    # a surface facing down more steeply than 45 degrees from the vertical needs support
CONTACT_TOL_MM = 0.15
MAX_CANDIDATES = 30


def _down_candidates(mesh: trimesh.Trimesh) -> list[np.ndarray]:
    hull = mesh.convex_hull
    order = np.argsort(-hull.facets_area)[:MAX_CANDIDATES]
    out = [np.asarray(hull.facets_normal[i], dtype=float) for i in order]
    for axis in np.vstack([np.eye(3), -np.eye(3)]):
        if not any(np.allclose(axis, c, atol=1e-3) for c in out):
            out.append(axis)
    return out


def _rotation_for(down: np.ndarray) -> np.ndarray:
    return trimesh.geometry.align_vectors(down, [0, 0, -1])[:3, :3]


def measure(mesh: trimesh.Trimesh, R: np.ndarray) -> dict[str, float]:
    """Contact area, overhang area and height of the part turned by R (normals and vertex heights only; fast on dense meshes)."""
    z = mesh.vertices @ R[2]
    fz = z[mesh.faces]                                   # (faces, 3) heights
    nz = mesh.face_normals @ R[2]
    low = z.min()
    area = mesh.area_faces
    base = (nz < -0.99) & (fz.max(axis=1) - low < CONTACT_TOL_MM)
    over = (nz < -OVERHANG_SIN) & ~base
    return {"contact_mm2": float(area[base].sum()), "overhang_mm2": float(area[over].sum()),
            "overhang_percent": float(100 * area[over].sum() / max(area.sum(), 1e-9)), "height_mm": float(z.max() - low)}


def _euler_degrees(R: np.ndarray) -> list[float]:
    M = np.eye(4)
    M[:3, :3] = R
    return [round(float(np.degrees(a)) + 0.0, 2) for a in trimesh.transformations.euler_from_matrix(M, "sxyz")]


def _score(m: dict[str, float]) -> float:
    return m["overhang_mm2"] - 0.25 * m["contact_mm2"] + 0.05 * m["height_mm"]


def suggest(mesh: trimesh.Trimesh, mode: str) -> dict[str, Any]:
    """mode: "flat" (largest flat face on the bed) or "auto" (least support). `mesh` is the part as uploaded."""
    before = measure(mesh, np.eye(3))
    cands = [(d, measure(mesh, _rotation_for(d))) for d in _down_candidates(mesh)]
    if mode == "flat":
        down, after = max(cands, key=lambda c: (round(c[1]["contact_mm2"], 1), -c[1]["height_mm"]))
    else:
        down, after = min(cands, key=lambda c: _score(c[1]))
        if _score(after) >= _score(before) - 1e-6:        # already as good as it gets: leave it alone
            down, after = np.array([0.0, 0.0, -1.0]), before
    R = _rotation_for(down)
    rotation = _euler_degrees(R)
    turned = meshio.apply_transform(mesh, rotation, 1.0)
    return {"rotation": rotation, "dims": meshio.dims(turned), "before": before, "after": after}
