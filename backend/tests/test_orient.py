"""Milestone 1.2 / 1.7 / 1.8: lay flat and auto-orient, quiet repair on upload, and plain-language checks."""
import numpy as np
import pytest
import trimesh

from app import meshcheck, meshio, orient
from conftest import make_user, upload


def mushroom():
    stem = trimesh.creation.cylinder(radius=4, height=20, transform=trimesh.transformations.translation_matrix([0, 0, 10]))
    cap = trimesh.creation.cylinder(radius=20, height=4, transform=trimesh.transformations.translation_matrix([0, 0, 22]))
    return trimesh.util.concatenate([stem, cap])


def on_bed(mesh, rotation):
    return meshio.apply_transform(mesh, rotation, 1.0)


# ------------------------------------------------------------------ orientation

@pytest.mark.parametrize("tilt", [(0, 0, 0), (30, 50, 70), (123, 17, 200), (90, 90, 0)])
def test_lay_flat_puts_the_largest_face_on_the_bed_whatever_the_starting_tilt(tilt):
    box = meshio.apply_transform(trimesh.creation.box((10, 20, 40)), tilt, 1.0)
    r = orient.suggest(box, "flat")
    turned = on_bed(box, r["rotation"])
    assert meshio.dims(turned)[2] == pytest.approx(10, abs=0.01)                      # the 20 x 40 face is down
    assert orient.measure(turned, np.eye(3))["contact_mm2"] == pytest.approx(800, rel=0.01)
    assert r["after"]["contact_mm2"] == pytest.approx(800, rel=0.01)                  # what it reports is what happens


def test_auto_orient_turns_a_mushroom_cap_down_and_removes_the_overhang():
    cap_up = mushroom()
    r = orient.suggest(cap_up, "auto")
    assert r["before"]["overhang_percent"] > 25 and r["after"]["overhang_percent"] < 3
    turned = on_bed(cap_up, r["rotation"])
    assert orient.measure(turned, np.eye(3))["overhang_percent"] < 3
    assert meshio.dims(turned) == pytest.approx((40, 40, 24), abs=0.1)


@pytest.mark.parametrize("tilt", [(0, 0, 0), (40, 25, 80)])
def test_a_picked_face_is_the_one_that_ends_up_on_the_bed(tilt):
    box = trimesh.creation.box((10, 20, 40))
    for normal, area in (([0, 0, 1], 200), ([0, 1, 0], 400), ([-1, 0, 0], 800), ([0, 0, -1], 200)):
        r = orient.face_down(box, normal)
        turned = on_bed(box, r["rotation"])
        assert orient.measure(turned, np.eye(3))["contact_mm2"] == pytest.approx(area, rel=0.01), normal
        assert r["after"]["contact_mm2"] == pytest.approx(area, rel=0.01)


def test_a_direction_that_is_not_a_face_balances_the_part_and_nonsense_is_refused():
    wedge = trimesh.creation.box((20, 20, 20))
    slant = np.array([1.0, 0.0, 1.0]) / np.sqrt(2)
    turned = on_bed(wedge, orient.face_down(wedge, slant.tolist())["rotation"])
    top = turned.vertices @ np.array([0, 0, 1.0])
    assert top.min() == pytest.approx(0, abs=1e-6) and meshio.dims(turned)[2] == pytest.approx(20 * np.sqrt(2), abs=0.05)   # balanced on an edge
    for bad in ([0, 0, 0], [1, 2], [float("nan"), 0, 1]):
        with pytest.raises(ValueError):
            orient.face_down(wedge, bad)


def test_the_orient_endpoint_takes_a_picked_face(client):
    from pathlib import Path
    m = upload(client, Path(__file__).parent / "fixtures" / "valid" / "cube_binary.stl").json()
    r = client.post(f"/api/models/{m['id']}/orient", json={"mode": "face", "normal": [0, 1, 0]})
    assert r.status_code == 200 and r.json()["after"]["contact_mm2"] > 0
    assert client.post(f"/api/models/{m['id']}/orient", json={"mode": "face"}).status_code == 422
    assert client.post(f"/api/models/{m['id']}/orient", json={"mode": "face", "normal": [0, 0, 0]}).status_code == 422


def test_auto_orient_leaves_a_good_orientation_alone():
    assert orient.suggest(trimesh.creation.box((30, 20, 10)), "auto")["rotation"] == [0, 0, 0]


def test_auto_orient_never_makes_things_worse():
    for mesh in (trimesh.creation.icosphere(radius=15), trimesh.creation.cone(radius=10, height=30), trimesh.creation.torus(20, 5)):
        r = orient.suggest(mesh, "auto")
        assert orient._score(r["after"]) <= orient._score(r["before"]) + 1e-6


def test_the_orient_endpoint_returns_a_rotation_for_your_own_model_only(client):
    m = upload(client, meshio.Path(__file__).parent / "fixtures" / "valid" / "tube.stl").json()
    r = client.post(f"/api/models/{m['id']}/orient", json={"mode": "flat"})
    assert r.status_code == 200 and len(r.json()["rotation"]) == 3 and r.json()["dims"][2] <= max(m["dims"])
    assert client.post(f"/api/models/{m['id']}/orient", json={"mode": "sideways"}).status_code == 422
    other = make_user(client, "orient.other@imperial.ac.uk")
    assert other.post(f"/api/models/{m['id']}/orient", json={"mode": "auto"}).status_code == 404


# ------------------------------------------------------------------ repair

def save(mesh, tmp_path, name="m.stl"):
    p = tmp_path / name
    mesh.export(str(p))
    return p


def test_a_model_with_a_missing_face_has_its_hole_closed(tmp_path):
    box = trimesh.creation.box((20, 20, 20))
    keep = np.ones(len(box.faces), bool)
    keep[np.where(box.face_normals[:, 2] > 0.9)[0]] = False                           # remove the top face
    holed = trimesh.Trimesh(box.vertices, box.faces[keep], process=False)
    assert not holed.is_watertight
    mesh = meshio.load_mesh(save(holed, tmp_path))
    assert mesh.is_watertight and any("holes" in r for r in mesh.metadata["repairs"])


def test_an_inside_out_model_is_turned_the_right_way_round(tmp_path):
    box = trimesh.creation.box((20, 20, 20))
    box.invert()
    mesh = meshio.load_mesh(save(box, tmp_path))
    assert mesh.volume > 0 and mesh.is_watertight and mesh.metadata["repairs"]


def test_zero_area_faces_are_removed(tmp_path):
    box = trimesh.creation.box((20, 20, 20))
    verts = np.vstack([box.vertices, [[0, 0, 30]]])
    faces = np.vstack([box.faces, [[0, 0, 8]]])                                      # a sliver with two equal corners
    mesh = meshio.load_mesh(save(trimesh.Trimesh(verts, faces, process=False), tmp_path))
    assert mesh.is_watertight and any("zero-area" in r for r in mesh.metadata["repairs"])


def test_a_clean_model_is_not_touched(tmp_path):
    mesh = meshio.load_mesh(save(trimesh.creation.box((20, 20, 20)), tmp_path))
    assert mesh.metadata["repairs"] == []


# ------------------------------------------------------------------ advice

def advice(mesh):
    return {c["text"] for c in meshcheck.check(mesh, [])}


def test_advice_is_plain_and_only_given_when_it_applies():
    assert advice(trimesh.creation.box((30, 30, 30))) == set()
    assert any("only 1.00 mm" in t for t in advice(trimesh.creation.box((1, 20, 20))))
    assert any("overhangs steeply" in t and "Auto-orient" in t for t in advice(mushroom()))
    assert any("touches the bed" in t for t in advice(trimesh.creation.icosphere(subdivisions=2, radius=15)))
    holed = trimesh.Trimesh(trimesh.creation.box((20, 20, 20)).vertices, trimesh.creation.box((20, 20, 20)).faces[:-2], process=False)
    assert any("holes or open edges" in t for t in advice(holed))
    two = trimesh.util.concatenate([trimesh.creation.box((20, 20, 20)), trimesh.creation.box((20, 20, 20)).apply_translation([50, 0, 0])])
    assert any("2 separate pieces" in t for t in advice(two))


def test_the_upload_reply_carries_the_checks(client, tmp_path):
    p = save(mushroom(), tmp_path, "mushroom.stl")
    with open(p, "rb") as fh:
        r = client.post("/api/models", files={"file": ("mushroom.stl", fh)})
    assert r.status_code == 200
    assert any(c["level"] == "warn" and "overhangs" in c["text"] for c in r.json()["checks"])
    assert upload(client, meshio.Path(__file__).parent / "fixtures" / "valid" / "cube_binary.stl").json()["checks"] == []
