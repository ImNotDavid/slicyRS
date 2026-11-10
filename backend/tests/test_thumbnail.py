"""Milestone 1.11: plate thumbnail in the print bundle (off by default until seen on a real printer)."""
import struct
import zipfile
import zlib

import numpy as np
import pytest
import trimesh

from app import thumbnail


def decode(png: bytes) -> np.ndarray:
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    pos, idat = 8, b""
    while pos < len(png):
        n = struct.unpack(">I", png[pos:pos + 4])[0]
        tag, data = png[pos + 4:pos + 8], png[pos + 8:pos + 8 + n]
        assert zlib.crc32(tag + data) & 0xFFFFFFFF == struct.unpack(">I", png[pos + 8 + n:pos + 12 + n])[0]   # every chunk checks out
        if tag == b"IHDR":
            w, h, depth, ctype = struct.unpack(">IIBB", data[:10])
            assert (depth, ctype) == (8, 6)
        if tag == b"IDAT":
            idat += data
        pos += 12 + n
    return np.frombuffer(zlib.decompress(idat), np.uint8).reshape(h, 1 + w * 4)[:, 1:].reshape(h, w, 4)


def box(dims=(10, 20, 40)):
    m = trimesh.creation.box(dims)
    m.apply_translation([0, 0, dims[2] / 2])
    return m


def test_the_picture_is_a_valid_png_of_the_part_on_a_transparent_background():
    img = decode(thumbnail.render(box(), 128))
    assert img.shape == (128, 128, 4) and img[0, 0, 3] == 0                      # corner is empty
    ys, xs = np.where(img[..., 3] > 0)
    assert (ys.max() - ys.min()) > (xs.max() - xs.min())                         # a tall part looks tall
    assert 0.05 < (img[..., 3] > 0).mean() < 0.6 and (xs.min() > 0 and ys.min() > 0)   # fits inside the frame with a margin


def test_the_part_is_shaded_so_its_faces_can_be_told_apart():
    img = decode(thumbnail.render(box((30, 30, 30)), 128))
    shown = img[img[..., 3] == 255][:, :3]
    assert len({tuple(p) for p in shown}) >= 3                                    # top and two sides at different brightness


def test_a_dense_part_renders_in_reasonable_time():
    import time
    sphere = trimesh.creation.icosphere(subdivisions=6, radius=40)                # about 82,000 triangles
    t = time.time()
    img = decode(thumbnail.render(sphere, 256))
    assert time.time() - t < 15 and (img[..., 3] > 0).mean() == pytest.approx(0.61, abs=0.05)


def bundle(tmp_path, with_png=False):
    p = tmp_path / "model.gcode.3mf"
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("Metadata/plate_1.gcode", "G1 X1\n")
        z.writestr("Metadata/plate_1.gcode.md5", "ABCDEF")
        z.writestr("_rels/.rels", "<rels/>")
        if with_png:
            z.writestr("Metadata/plate_1.png", b"already")
    return p


def test_the_pictures_go_into_the_bundle_and_nothing_else_changes(tmp_path):
    p = bundle(tmp_path)
    assert thumbnail.add_to_bundle(p, box()) is True
    with zipfile.ZipFile(p) as z:
        assert z.testzip() is None
        assert z.read("Metadata/plate_1.gcode") == b"G1 X1\n" and z.read("Metadata/plate_1.gcode.md5") == b"ABCDEF"
        assert decode(z.read("Metadata/plate_1.png")).shape[:2] == (512, 512) and decode(z.read("Metadata/plate_1_small.png")).shape[:2] == (128, 128)
    assert not list(tmp_path.glob("*.tmp"))


def test_an_existing_picture_is_left_alone(tmp_path):
    p = bundle(tmp_path, with_png=True)
    assert thumbnail.add_to_bundle(p, box()) is False
    assert zipfile.ZipFile(p).read("Metadata/plate_1.png") == b"already"


def test_it_does_nothing_unless_switched_on_and_never_fails_a_slice(tmp_path, monkeypatch):
    p = bundle(tmp_path)
    thumbnail.maybe_add(p, tmp_path / "missing.stl")
    assert "Metadata/plate_1.png" not in zipfile.ZipFile(p).namelist()            # off by default
    monkeypatch.setattr(thumbnail, "ENABLED", True)
    thumbnail.maybe_add(p, tmp_path / "missing.stl")                              # cannot read the model: logged, not raised
    thumbnail.maybe_add(None, tmp_path / "missing.stl")
    assert "Metadata/plate_1.png" not in zipfile.ZipFile(p).namelist()
