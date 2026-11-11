"""Plates made of several parts laid out in the editor (Milestone 1.6). A plate is a list of parts (copies are just more parts); each has
its own turn, scale and position. Positions are the part's bounding-box centre, in mm from the middle of the bed. The server checks the
layout itself and never trusts the editor: at most MAX_PARTS parts, no two closer than MIN_GAP_MM, and the whole plate inside the bed of
whichever printer takes it. A plate is one job with one owner; it never joins other people's plates."""
from dataclasses import dataclass
from typing import Any, Callable

import trimesh
from shapely import affinity
from shapely.geometry import MultiPoint, Polygon

MAX_PARTS = 12
MIN_GAP_MM = 0.5


class PlateError(ValueError):
    """A layout the person can fix (too many parts, parts touching)."""


@dataclass
class Instance:
    index: int
    model_id: str
    name: str
    rotation: tuple[float, float, float]
    scale: float
    position: tuple[float, float]
    mesh: trimesh.Trimesh                  # as printed: bounding box centred on x and y, standing on z = 0
    footprint: Polygon                     # convex outline from above, centred like the mesh
    dims: tuple[float, float, float]

    def placed(self) -> Polygon:
        return affinity.translate(self.footprint, *self.position)

    def key(self) -> tuple:
        return (self.model_id, self.rotation, self.scale)


def outline(mesh: trimesh.Trimesh) -> Polygon:
    hull = MultiPoint(mesh.vertices[:, :2]).convex_hull
    return hull if isinstance(hull, Polygon) and hull.area > 0 else hull.buffer(0.05)


def build(parts: list[dict[str, Any]], load: Callable[[str, tuple, float], trimesh.Trimesh], name_of: Callable[[str], str]) -> list[Instance]:
    """`load(model_id, rotation, scale)` returns the transformed mesh (centred, on the bed); raises HTTP errors for models the person may not use."""
    if not parts:
        raise PlateError("Add at least one part")
    if len(parts) > MAX_PARTS:
        raise PlateError(f"A plate holds at most {MAX_PARTS} parts")
    cache: dict[tuple, tuple[trimesh.Trimesh, Polygon]] = {}
    out = []
    for i, p in enumerate(parts):
        rotation, scale = tuple(float(a) for a in p["rotation"]), float(p["scale"])
        key = (p["model_id"], rotation, scale)
        if key not in cache:
            m = load(p["model_id"], rotation, scale)
            cache[key] = (m, outline(m))
        mesh, poly = cache[key]
        lo, hi = mesh.bounds
        out.append(Instance(i, p["model_id"], name_of(p["model_id"]), rotation, scale, (float(p["position"][0]), float(p["position"][1])), mesh, poly,
                            (float(hi[0] - lo[0]), float(hi[1] - lo[1]), float(hi[2] - lo[2]))))
    return out


def check_layout(instances: list[Instance]) -> None:
    shapes = [i.placed() for i in instances]
    for a in range(len(shapes)):
        for b in range(a + 1, len(shapes)):
            if shapes[a].distance(shapes[b]) < MIN_GAP_MM:
                raise PlateError(f"{instances[a].name} and {instances[b].name} overlap or are closer than {MIN_GAP_MM:g} mm; move one of them")


def envelope(instances: list[Instance]) -> tuple[tuple[float, float, float], tuple[float, float]]:
    """The smallest box around every part: its size and its centre (mm from the middle of the bed). Because no part overlaps another, a
    printer takes the plate exactly when this box fits where it is."""
    minx = miny = float("inf")
    maxx = maxy = float("-inf")
    for i in instances:
        b = i.placed().bounds
        minx, miny, maxx, maxy = min(minx, b[0]), min(miny, b[1]), max(maxx, b[2]), max(maxy, b[3])
    height = max(i.dims[2] for i in instances)
    return (round(maxx - minx, 3), round(maxy - miny, 3), round(height, 3)), (round((minx + maxx) / 2, 3), round((miny + maxy) / 2, 3))


def overflow_names(instances: list[Instance], bed: tuple[float, float, float], margin: float) -> list[str]:
    """Names of the parts that would hang off a bed (or are too tall), for the message that tells the person which parts do not fit."""
    out = []
    for i in instances:
        x0, y0, x1, y1 = i.placed().bounds
        if x0 < -bed[0] / 2 + margin or x1 > bed[0] / 2 - margin or y0 < -bed[1] / 2 + margin or y1 > bed[1] / 2 - margin or i.dims[2] > bed[2]:
            out.append(i.name)
    return list(dict.fromkeys(out))


def plate_name(instances: list[Instance]) -> str:
    first = instances[0].name
    return first if len(instances) == 1 else f"{first} + {len(instances) - 1} more"
