"""Plate thumbnail for the print bundle (Milestone 1.11). The slicer's headless export names `Metadata/plate_1.png` and
`plate_1_small.png` in the bundle but does not make them, so a printer has no picture to show for the job. This renders one
with plain numpy (no GPU, no image library): a shaded view of the part from the front corner, on a transparent background.
On by default (owner decision); BUNDLE_THUMBNAIL=0 switches it off. Not yet seen on a real printer's screen."""
import logging
import math
import os
import struct
import zipfile
import zlib
from pathlib import Path

import numpy as np
import trimesh

log = logging.getLogger("thumbnail")
ENABLED = os.environ.get("BUNDLE_THUMBNAIL", "1") == "1"
AZIMUTH, ELEVATION = math.radians(35), math.radians(35)
DEFAULT_COLOUR = (0, 174, 66)         # Bambu green, used when the plate does not say which filament it is
LIGHT = np.array([-0.45, -0.55, 0.7])


def _png(rgba: np.ndarray) -> bytes:
    h, w = rgba.shape[:2]
    raw = b"".join(b"\x00" + rgba[y].tobytes() for y in range(h))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b"")


def _view() -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    c = np.array([math.sin(AZIMUTH) * math.cos(ELEVATION), -math.cos(AZIMUTH) * math.cos(ELEVATION), math.sin(ELEVATION)])
    right = np.cross([0, 0, 1], c)
    right /= np.linalg.norm(right)
    return c, right, np.cross(c, right)


def render(mesh: trimesh.Trimesh, size: int = 512, colour: tuple[int, int, int] = DEFAULT_COLOUR) -> bytes:
    """A PNG (RGBA, `size` square) of the part. Drawn at twice the size and averaged down for smooth edges."""
    S = size * 2
    c, right, up = _view()
    tri = mesh.triangles.astype(float)                       # (n, 3, 3)
    sx, sy, depth = tri @ right, tri @ up, tri @ c            # (n, 3) each
    lo = np.array([sx.min(), sy.min()])
    span = max(sx.max() - lo[0], sy.max() - lo[1], 1e-9)
    scale = S * 0.88 / span
    off = (S - np.array([sx.max() - lo[0], sy.max() - lo[1]]) * scale) / 2
    px = (sx - lo[0]) * scale + off[0]
    py = S - ((sy - lo[1]) * scale + off[1])                  # image rows run downwards

    n = mesh.face_normals
    facing = n @ c > 0                                         # back faces are hidden by the front ones
    shade = 0.32 + 0.68 * np.clip(n @ (LIGHT / np.linalg.norm(LIGHT)), 0, 1)
    img = np.zeros((S, S, 4), np.uint8)
    zbuf = np.full((S, S), -np.inf)
    col = np.array(colour, float)

    area = 0.5 * np.abs((px[:, 1] - px[:, 0]) * (py[:, 2] - py[:, 0]) - (px[:, 2] - px[:, 0]) * (py[:, 1] - py[:, 0]))
    idx = np.where(facing)[0]
    small = idx[area[idx] < 3.0]
    big = idx[area[idx] >= 3.0]

    if len(small):                                              # dense meshes: one 2 x 2 splat at each tiny triangle's centre
        cx, cy = px[small].mean(axis=1), py[small].mean(axis=1)
        z = depth[small].mean(axis=1)
        for dx in (0, 1):
            for dy in (0, 1):
                ix, iy = np.clip(cx.astype(int) + dx, 0, S - 1), np.clip(cy.astype(int) + dy, 0, S - 1)
                np.maximum.at(zbuf, (iy, ix), z)
                win = zbuf[iy, ix] <= z
                img[iy[win], ix[win], :3] = (col * shade[small[win], None]).astype(np.uint8)
                img[iy[win], ix[win], 3] = 255
    for t in big[np.argsort(depth[big].mean(axis=1))]:         # far to near, each pixel tested against the depth buffer
        x, y, z = px[t], py[t], depth[t]
        x0, x1 = max(int(x.min()), 0), min(int(x.max()) + 1, S - 1)
        y0, y1 = max(int(y.min()), 0), min(int(y.max()) + 1, S - 1)
        if x1 < x0 or y1 < y0:
            continue
        gx, gy = np.meshgrid(np.arange(x0, x1 + 1) + 0.5, np.arange(y0, y1 + 1) + 0.5)
        d = (y[1] - y[2]) * (x[0] - x[2]) + (x[2] - x[1]) * (y[0] - y[2])
        if abs(d) < 1e-12:
            continue
        w0 = ((y[1] - y[2]) * (gx - x[2]) + (x[2] - x[1]) * (gy - y[2])) / d
        w1 = ((y[2] - y[0]) * (gx - x[2]) + (x[0] - x[2]) * (gy - y[2])) / d
        w2 = 1 - w0 - w1
        inside = (w0 >= -0.02) & (w1 >= -0.02) & (w2 >= -0.02)
        zz = w0 * z[0] + w1 * z[1] + w2 * z[2]
        sub = zbuf[y0:y1 + 1, x0:x1 + 1]
        win = inside & (zz >= sub)
        sub[win] = zz[win]
        img[y0:y1 + 1, x0:x1 + 1][win, :3] = (col * shade[t]).astype(np.uint8)
        img[y0:y1 + 1, x0:x1 + 1][win, 3] = 255

    a = img.astype(float).reshape(size, 2, size, 2, 4)           # average 2 x 2 blocks, weighting colour by coverage
    cov = a[..., 3].mean(axis=(1, 3))
    rgb = (a[..., :3] * a[..., 3:4]).sum(axis=(1, 3)) / np.maximum(a[..., 3].sum(axis=(1, 3))[..., None], 1)
    out = np.dstack([rgb, cov[..., None]]).round().astype(np.uint8)
    return _png(out)


def add_to_bundle(bundle: Path, mesh: trimesh.Trimesh, colour: tuple[int, int, int] = DEFAULT_COLOUR) -> bool:
    """Put `Metadata/plate_1.png` and `plate_1_small.png` into a sliced bundle (the bundle already refers to them). False if it
    already has them. The bundle is replaced only after the new file is complete."""
    with zipfile.ZipFile(bundle) as zin:
        if "Metadata/plate_1.png" in zin.namelist():
            return False
        tmp = bundle.with_suffix(bundle.suffix + ".tmp")
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zout:
            for item in zin.infolist():
                zout.writestr(item, zin.read(item.filename))
            zout.writestr("Metadata/plate_1.png", render(mesh, 512, colour))
            zout.writestr("Metadata/plate_1_small.png", render(mesh, 128, colour))
    tmp.replace(bundle)
    return True


def maybe_add(bundle: Path | None, input_path: Path) -> None:
    """Called after a slice. Never allowed to fail the slice."""
    if not ENABLED or bundle is None or not bundle.exists():
        return
    try:
        from .meshio import load_mesh
        add_to_bundle(bundle, load_mesh(input_path))
    except Exception:
        log.exception("could not add a thumbnail to %s", bundle)
