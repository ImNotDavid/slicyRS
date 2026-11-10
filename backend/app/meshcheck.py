"""Quiet repairs and plain-language warnings for an uploaded model (Milestone 1.7, 1.8). Repairs are the common CAD-export
faults (duplicate vertices, zero-area faces, flipped faces, small holes); anything that cannot be fixed is reported."""
from typing import Any

import numpy as np
import trimesh

from . import orient

SMALL_MM = 2.0


def repair(mesh: trimesh.Trimesh) -> list[str]:
    """Fix the mesh in place; return what was done."""
    done: list[str] = []
    n = len(mesh.vertices)
    mesh.merge_vertices()
    if len(mesh.vertices) < n * 0.999 and not mesh.is_watertight:
        done.append("joined duplicate vertices")
    bad = ~mesh.nondegenerate_faces()
    if bad.any():
        mesh.update_faces(~bad)
        mesh.remove_unreferenced_vertices()
        done.append(f"removed {int(bad.sum())} zero-area face(s)")
    if not mesh.is_winding_consistent:
        trimesh.repair.fix_winding(mesh)
        trimesh.repair.fix_normals(mesh)
        done.append("made the faces consistent")
    if mesh.is_watertight and mesh.volume < 0:
        trimesh.repair.fix_inversion(mesh)
        done.append("turned an inside-out model the right way round")
    if not mesh.is_watertight:
        trimesh.repair.fill_holes(mesh)
        if mesh.is_watertight:
            done.append("closed small holes")
    return done


def check(mesh: trimesh.Trimesh, repairs: list[str]) -> list[dict[str, str]]:
    """Advice for the person who uploaded it: [{level: "info" | "warn", text}]."""
    out: list[dict[str, str]] = []
    if repairs:
        out.append({"level": "info", "text": "Repaired on upload: " + ", ".join(repairs) + "."})
    if not mesh.is_watertight:
        out.append({"level": "warn", "text": "The model still has holes or open edges after repair. The slice may have gaps or missing walls; "
                                              "fix it in your CAD program or a mesh repair tool and upload it again."})
    bodies = mesh.body_count
    if bodies > 1:
        out.append({"level": "info", "text": f"The file holds {bodies} separate pieces; they will print together."})
    dims = np.ptp(mesh.bounds, axis=0)
    if dims.min() < SMALL_MM:
        out.append({"level": "warn", "text": f"One dimension is only {dims.min():.2f} mm. Details this thin may not print; check the size and units "
                                              "(a model exported in metres or inches looks tiny)."})
    m = orient.measure(mesh, np.eye(3))
    if m["overhang_percent"] >= 15:
        out.append({"level": "warn", "text": f"About {m['overhang_percent']:.0f}% of the surface overhangs steeply and will need supports in this "
                                              "orientation. Try Auto-orient under Rotate."})
    elif m["contact_mm2"] < 20 and dims.min() >= SMALL_MM:
        out.append({"level": "warn", "text": f"Only {m['contact_mm2']:.0f} mm² of the part touches the bed, so it may not stick. Try Lay flat or Auto-orient "
                                              "under Rotate, or add a brim."})
    return out


def report(mesh: trimesh.Trimesh) -> list[dict[str, Any]]:
    return check(mesh, mesh.metadata.get("repairs", []))
