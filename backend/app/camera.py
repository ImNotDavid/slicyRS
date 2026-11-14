"""Printer cameras (2.7). The A1 and P1 families send JPEG pictures over a TLS socket on port 6000; the X1 and H2D families use
an RTSPS video stream instead, which is not supported yet (owner decision: A1 first).

Protocol (as used by Bambu Studio and the community clients): connect with TLS (the printer's certificate is self-signed), send
an 80-byte login packet, then read frames. Each frame is a 16-byte header (payload length, little-endian) followed by one JPEG.
"""
import socket
import ssl
import struct
import threading
import time
from typing import Any

PORT = 6000
JPEG_START, JPEG_END = b"\xff\xd8", b"\xff\xd9"
SUPPORTED_MODELS = {"A1", "A1 mini", "P1P", "P1S"}       # the port-6000 picture stream; only the A1 mini is confirmed so far
MAX_FRAME = 4 * 1024 * 1024


class CameraError(Exception):
    pass


def supported(printer: dict[str, Any]) -> bool:
    return printer.get("model") in SUPPORTED_MODELS


def login_packet(access_code: str, username: str = "bblp") -> bytes:
    return struct.pack("<IIII", 0x40, 0x3000, 0, 0) + username.encode().ljust(32, b"\0") + access_code.encode().ljust(32, b"\0")


def _read_exact(sock: Any, n: int) -> bytes:
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise CameraError("The camera closed the connection")
        buf += chunk
    return buf


def read_frame(sock: Any) -> bytes:
    """One JPEG from an authenticated stream (testable with a plain socket)."""
    header = _read_exact(sock, 16)
    size = struct.unpack("<I", header[:4])[0]
    if not 0 < size <= MAX_FRAME:
        raise CameraError("The camera sent a picture of an impossible size (wrong access code?)")
    data = _read_exact(sock, size)
    if not (data.startswith(JPEG_START) and data.rstrip(b"\0").endswith(JPEG_END)):
        raise CameraError("The camera did not send a JPEG picture")
    return data


def snapshot(host: str, access_code: str, port: int = PORT, timeout: float = 8.0) -> bytes:
    """Connect, log in, return the first picture. Raises CameraError."""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE           # printers use a self-signed certificate; the access code is the secret
    try:
        with socket.create_connection((host, port), timeout) as raw, ctx.wrap_socket(raw, server_hostname=host) as s:
            s.settimeout(timeout)
            s.sendall(login_packet(access_code))
            return read_frame(s)
    except (OSError, ssl.SSLError) as e:
        raise CameraError(f"Could not reach the printer's camera: {e}") from e


# ---- server side: one cached picture per printer, so many viewers do not each open a connection to the printer

_cache: dict[str, tuple[float, bytes]] = {}
_locks: dict[str, threading.Lock] = {}
CACHE_S = 2.5


def cached_snapshot(printer: dict[str, Any], fetch, max_age: float = CACHE_S) -> bytes:
    """`fetch()` takes a fresh picture. Pictures younger than `max_age` are reused; one fetch per printer at a time."""
    pid = printer["id"]
    lock = _locks.setdefault(pid, threading.Lock())
    with lock:
        hit = _cache.get(pid)
        if hit and time.time() - hit[0] < max_age:
            return hit[1]
        data = fetch()
        _cache[pid] = (time.time(), data)
        return data


def printer_picture(printer: dict[str, Any], max_age: float = CACHE_S) -> bytes:
    """A recent picture from a printer the server can reach (directly or through its gateway). Raises CameraError."""
    from . import adapter
    if not supported(printer):
        raise CameraError(f"Camera pictures are not supported for the {printer.get('model')} yet")
    link = adapter.get(printer["id"])
    if link is None:
        raise CameraError("The server has no connection to this printer")
    return cached_snapshot(printer, link.snapshot, max_age)


PHOTO = "plate_photo.jpg"
PHOTO_DELAY_S = 20          # let the toolhead park before the picture
LIGHT_SETTLE_S = 2.0        # after switching the light on, before the picture (the camera adjusts its exposure)


def lit_picture(printer: dict[str, Any], settle: float = LIGHT_SETTLE_S) -> bytes:
    """A picture taken with the printer's light on (owner request); the light goes back to how it was afterwards.
    If the light cannot be switched, the picture is still taken."""
    import logging
    from . import adapter, store
    if not supported(printer):
        raise CameraError(f"Camera pictures are not supported for the {printer.get('model')} yet")
    link = adapter.get(printer["id"])
    if link is None:
        raise CameraError("The server has no connection to this printer")
    was_on = ((store.printers.get(printer["id"]) or {}).get("status") or {}).get("light") == "on"
    switched = False
    if not was_on:
        try:
            link.light(True)
            switched = True
            time.sleep(settle)
        except Exception:
            logging.getLogger("camera").info("could not switch the light on for %s", printer.get("name"), exc_info=True)
    try:
        data = link.snapshot()
        _cache[printer["id"]] = (time.time(), data)
        return data
    finally:
        if switched:
            try:
                link.light(False)
            except Exception:
                logging.getLogger("camera").info("could not switch the light back off for %s", printer.get("name"), exc_info=True)


def photograph_plate(job_id: str, printer_id: str, delay: float = PHOTO_DELAY_S) -> None:
    """After a print ends, keep one picture of the plate with the job's files (in the background; never raises)."""
    def run() -> None:
        import logging
        from . import store
        from .storage import JOBS, files
        time.sleep(delay)
        try:
            p = store.printers.get(printer_id)
            job = store.jobs.get(job_id)
            if not p or not job or not files.exists(JOBS, job_id):
                return
            data = lit_picture(p)
            files.write_bytes(JOBS, job_id, PHOTO, data)
            store.jobs.update(job_id, photo_at=time.time())
        except Exception:
            logging.getLogger("camera").info("no plate photo for job %s", job_id, exc_info=True)
    if delay <= 0:
        run()
    else:
        threading.Thread(target=run, daemon=True).start()
