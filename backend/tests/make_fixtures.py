"""Generate the format-coverage fixture set (STL ascii/binary, 3MF, OBJ, STEP + invalid files).

Run from backend/:  python tests/make_fixtures.py
STEP files need cadquery (dev only). Expected bounding boxes live in MANIFEST so tests can check them.
"""
import json
import struct
from pathlib import Path

import numpy as np
import trimesh

OUT = Path(__file__).parent / "fixtures"
VALID, INVALID = OUT / "valid", OUT / "invalid"
MANIFEST: dict[str, dict] = {}


def reg(name: str, dims, tris_min=1, note=""):
    MANIFEST[name] = {"dims": [round(float(d), 2) for d in dims], "note": note}


def ascii_stl(mesh: trimesh.Trimesh) -> str:
    out = ["solid t"]
    for f, n in zip(mesh.triangles, mesh.face_normals):
        out.append(f"facet normal {n[0]} {n[1]} {n[2]}\n outer loop")
        out += [f"  vertex {v[0]} {v[1]} {v[2]}" for v in f]
        out.append(" endloop\nendfacet")
    out.append("endsolid t")
    return "\n".join(out)



def cube_xml(oid: int, x: float, y: float, z: float) -> str:
    v = [(0, 0, 0), (x, 0, 0), (x, y, 0), (0, y, 0), (0, 0, z), (x, 0, z), (x, y, z), (0, y, z)]
    t = [(0, 2, 1), (0, 3, 2), (4, 5, 6), (4, 6, 7), (0, 1, 5), (0, 5, 4), (1, 2, 6), (1, 6, 5), (2, 3, 7), (2, 7, 6), (3, 0, 4), (3, 4, 7)]
    vs = "".join(f'<vertex x="{a}" y="{b}" z="{c}"/>' for a, b, c in v)
    ts = "".join(f'<triangle v1="{a}" v2="{b}" v3="{c}"/>' for a, b, c in t)
    return f'<object id="{oid}" type="model"><mesh><vertices>{vs}</vertices><triangles>{ts}</triangles></mesh></object>'


def write_3mf(path: Path, objects: list[str], items: list[tuple[int, str]], unit: str = "millimeter") -> None:
    import zipfile
    build = "".join(f'<item objectid="{i}" transform="{t}"/>' for i, t in items)
    model = ('<?xml version="1.0" encoding="UTF-8"?><model unit="%s" xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02">'
             '<resources>%s</resources><build>%s</build></model>' % (unit, "".join(objects), build))
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                   '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                   '<Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/></Types>')
        z.writestr("_rels/.rels", '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   '<Relationship Target="/3D/3dmodel.model" Id="rel0" Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/></Relationships>')
        z.writestr("3D/3dmodel.model", model)


def main() -> None:
    VALID.mkdir(parents=True, exist_ok=True)
    INVALID.mkdir(parents=True, exist_ok=True)

    box = trimesh.creation.box(extents=(40, 30, 20))
    (VALID / "cube_ascii.stl").write_text(ascii_stl(box)); reg("cube_ascii.stl", (40, 30, 20), note="ASCII STL")
    box.export(VALID / "cube_binary.stl"); reg("cube_binary.stl", (40, 30, 20), note="binary STL")
    trimesh.creation.icosphere(subdivisions=3, radius=15).export(VALID / "sphere.stl"); reg("sphere.stl", (30, 30, 30))
    trimesh.creation.annulus(r_min=8, r_max=10, height=90).export(VALID / "tube.stl"); reg("tube.stl", (20, 20, 90), note="hollow")
    trimesh.creation.box(extents=(250, 250, 5)).export(VALID / "plate_250.stl"); reg("plate_250.stl", (250, 250, 5), note="fits 256 beds only")
    trimesh.creation.box(extents=(300, 300, 10)).export(VALID / "plate_300.stl"); reg("plate_300.stl", (300, 300, 10), note="H2D only")
    trimesh.creation.box(extents=(20, 20, 300)).export(VALID / "tall_300.stl"); reg("tall_300.stl", (20, 20, 300), note="H2D only (Z)")
    trimesh.creation.box(extents=(400, 400, 400)).export(VALID / "huge_400.stl"); reg("huge_400.stl", (400, 400, 400), note="fits nothing")
    trimesh.creation.box(extents=(2, 2, 2)).export(VALID / "tiny_2mm.stl"); reg("tiny_2mm.stl", (2, 2, 2))
    trimesh.creation.box(extents=(80, 60, 0.4)).export(VALID / "thin_sheet.stl"); reg("thin_sheet.stl", (80, 60, 0.4), note="single-layer-ish")
    # off-origin + negative coordinates: loader must not care about placement
    off = trimesh.creation.box(extents=(25, 25, 25)); off.apply_translation([-500, 300, -80])
    off.export(VALID / "offset_negative.stl"); reg("offset_negative.stl", (25, 25, 25), note="far from origin, negative z")

    # 3MF: single body, multi-body
    trimesh.creation.torus(major_radius=20, minor_radius=5).export(VALID / "torus.3mf"); reg("torus.3mf", (50, 50, 10))
    write_3mf(VALID / "two_bodies.3mf", [cube_xml(1, 20, 20, 20), cube_xml(2, 10, 10, 40)],
              [(1, "1 0 0 0 1 0 0 0 1 0 0 0"), (2, "1 0 0 0 1 0 0 0 1 40 0 10")])
    reg("two_bodies.3mf", (50, 20, 50), note="two build items merged")
    comp = ('<object id="3" type="model"><components><component objectid="1" transform="1 0 0 0 1 0 0 0 1 100 50 0"/>'
            '<component objectid="1" transform="0 1 0 -1 0 0 0 0 1 0 0 0"/></components></object>')
    write_3mf(VALID / "components.3mf", [cube_xml(1, 20, 10, 5), comp], [(3, "1 0 0 0 1 0 0 0 1 0 0 0")])
    reg("components.3mf", (130, 60, 5), note="Bambu-style components + transforms")
    write_3mf(VALID / "unit_inch.3mf", [cube_xml(1, 1, 1, 1)], [(1, "1 0 0 0 1 0 0 0 1 0 0 0")], unit="inch")
    reg("unit_inch.3mf", (25.4, 25.4, 25.4), note="1in cube, unit=inch")

    # OBJ: triangles and quads
    trimesh.creation.cylinder(radius=12, height=30, sections=32).export(VALID / "cylinder.obj"); reg("cylinder.obj", (24, 24, 30))
    (VALID / "quad_cube.obj").write_text(
        "v 0 0 0\nv 20 0 0\nv 20 20 0\nv 0 20 0\nv 0 0 20\nv 20 0 20\nv 20 20 20\nv 0 20 20\n"
        "f 1 4 3 2\nf 5 6 7 8\nf 1 2 6 5\nf 2 3 7 6\nf 3 4 8 7\nf 4 1 5 8\n")
    reg("quad_cube.obj", (20, 20, 20), note="quad faces must be triangulated")

    # STEP via cadquery
    try:
        import cadquery as cq
        cq.exporters.export(cq.Workplane("XY").box(30, 20, 10), str(VALID / "box.step")); reg("box.step", (30, 20, 10))
        bracket = (cq.Workplane("XY").box(60, 30, 5).faces(">Z").workplane().hole(6)
                   .faces(">Z").workplane().moveTo(25, 0).rect(10, 30).extrude(30))
        cq.exporters.export(bracket, str(VALID / "bracket.step")); reg("bracket.step", (60, 30, 35), note="holes + extrusion")
        cq.exporters.export(cq.Workplane("XY").cylinder(40, 15).faces(">Z").hole(10), str(VALID / "tube_hole.step"))
        reg("tube_hole.step", (30, 30, 40), note="cylinder with bore")
        cq.exporters.export(cq.Workplane("XY").sphere(12), str(VALID / "sphere.stp"), exportType="STEP"); reg("sphere.stp", (24, 24, 24), note=".stp extension")
    except ImportError:
        print("cadquery missing: STEP fixtures skipped")

    # invalid files
    (INVALID / "empty.stl").write_bytes(b"")
    (INVALID / "garbage.stl").write_bytes(b"this is not a mesh at all" * 50)
    good = (VALID / "cube_binary.stl").read_bytes()
    (INVALID / "truncated_binary.stl").write_bytes(good[: len(good) // 2])
    (INVALID / "zero_triangles.stl").write_bytes(b"\0" * 80 + struct.pack("<I", 0))
    (INVALID / "corrupt.3mf").write_bytes(b"PK\x03\x04 not really a zip")
    (INVALID / "empty.obj").write_text("# nothing here\n")
    (INVALID / "garbage.step").write_text("ISO-10303-21;\nHEADER;\nthis is broken\nENDSEC;\n")
    (INVALID / "notes.txt").write_text("hello")
    (INVALID / "model.exe").write_bytes(b"MZ")

    (OUT / "manifest.json").write_text(json.dumps(MANIFEST, indent=2))
    print(f"{len(MANIFEST)} valid fixtures, {len(list(INVALID.iterdir()))} invalid")


if __name__ == "__main__":
    main()
