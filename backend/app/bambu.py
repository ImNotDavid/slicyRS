"""Bambu Lab LAN-mode connector: MQTT (status) + implicit FTPS (file upload).

Requires the printer's IP, serial and LAN access code. The printer must be
reachable from wherever the backend runs.
"""
import ftplib
import json
import logging
import ssl
import threading
import time
from pathlib import Path
from typing import Any

import paho.mqtt.client as mqtt

import ipaddress
import socket

from . import store
from .catalog import AMS_TYPE_ALIASES, BED_TYPES, DEFAULT_BED_TYPE
from .crypto import unseal

log = logging.getLogger("bambu")


class _ImplicitFTPS(ftplib.FTP_TLS):
    """Bambu printers speak implicit FTPS on port 990."""

    def connect(self, host="", port=0, timeout=-999, source_address=None):
        if host:
            self.host = host
        if port:
            self.port = port
        if timeout != -999:
            self.timeout = timeout
        self.sock = self._wrap(self.host, self.port, self.timeout)
        self.af = self.sock.family
        self.file = self.sock.makefile("r", encoding=self.encoding)
        self.welcome = self.getresp()
        return self.welcome

    @staticmethod
    def _wrap(host, port, timeout):
        import socket
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx.wrap_socket(socket.create_connection((host, port), timeout), server_hostname=host)


class PrinterLink:
    def __init__(self, printer: dict[str, Any]):
        self.id = printer["id"]
        self.host = printer["host"]
        self.port = int(printer.get("port") or 8883)
        self.ftp_port = int(printer.get("ftp_port") or 990)  # fixed on real printers; overridable for tests
        self.serial = printer["serial"]
        self.code = unseal(printer["access_code"]) or ""
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"slicyrs-{self.id}")
        self.client.username_pw_set("bblp", self.code)
        self.client.tls_set(cert_reqs=ssl.CERT_NONE)
        self.client.tls_insecure_set(True)
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message
        self.client.on_disconnect = self._on_disconnect

    def start(self) -> None:
        self.client.connect_async(self.host, self.port, keepalive=30)
        self.client.loop_start()

    def stop(self) -> None:
        self.client.loop_stop()
        self.client.disconnect()

    def _on_connect(self, client, userdata, flags, rc, props=None):
        if rc != 0 and getattr(rc, "is_failure", False):
            _set_status(self.id, {"state": "OFFLINE", "error": f"MQTT refused: {rc}"})
            return
        _set_status(self.id, {"error": None})
        client.subscribe(f"device/{self.serial}/report")
        self.request_full_status()

    def _on_disconnect(self, client, userdata, flags, rc, props=None):
        _set_status(self.id, {"state": "OFFLINE"})

    def request_full_status(self) -> None:
        self.client.publish(f"device/{self.serial}/request",
                            json.dumps({"pushing": {"sequence_id": "0", "command": "pushall"}}))

    def _on_message(self, client, userdata, msg):
        try:
            report = json.loads(msg.payload).get("print")
        except ValueError:
            return
        if not report:
            return
        update: dict[str, Any] = {}
        if "gcode_state" in report:
            update["state"] = report["gcode_state"]
        if "mc_percent" in report:
            update["progress"] = report["mc_percent"]
        if "mc_remaining_time" in report:
            update["remaining_min"] = report["mc_remaining_time"]
        if "nozzle_temper" in report:
            update["nozzle_c"] = report["nozzle_temper"]
        if "bed_temper" in report:
            update["bed_c"] = report["bed_temper"]
        if "hms" in report:
            update["hms"] = report["hms"]
        ams_units = (report.get("ams") or {}).get("ams")
        if ams_units is not None:
            update["ams"] = _parse_ams(ams_units)
        if update:
            _set_status(self.id, update)

    def send_print(self, local_path: Path, remote_name: str, plate: str = "Metadata/plate_1.gcode") -> bool:
        """Upload a sliced .gcode.3mf over FTPS, then start it over MQTT. Returns True once the printer reports it started.

        Raises PrinterError with a user-readable message on any failure.
        """
        if not self.client.is_connected():
            raise PrinterError("The server is not connected to this printer's MQTT service (check IP, serial and access code)")
        state = (store.printers.get(self.id) or {}).get("status", {}).get("state", "OFFLINE")
        if state in BUSY_STATES:
            raise PrinterError(f"The printer is busy ({state.lower()}); wait for it to finish")
        self._upload(local_path, remote_name)
        rec = store.printers.get(self.id) or {}
        ams = rec.get("status", {}).get("ams") or []
        bed = BED_TYPES.get(rec.get("bed_type") or DEFAULT_BED_TYPE, "textured_plate")
        use_ams = bool(ams)  # printers without an AMS (e.g. A1 mini) print from the external spool
        cmd = {"print": {
            "sequence_id": "0", "command": "project_file", "param": plate,
            "project_id": "0", "profile_id": "0", "task_id": "0", "subtask_id": "0",
            "subtask_name": remote_name.removesuffix(".gcode.3mf"), "url": f"file:///sdcard/{remote_name}",
            "md5": _plate_md5(local_path, plate),
            "timelapse": False, "bed_leveling": True, "flow_cali": True, "vibration_cali": True,
            "layer_inspect": True, "use_ams": use_ams, "bed_type": bed,
        }}
        if use_ams:
            cmd["print"]["ams_mapping"] = [ams[0]["slot"]]
        known_hms = {_hms_key(e) for e in _current_status(self.id).get("hms", [])}
        info = self.client.publish(f"device/{self.serial}/request", json.dumps(cmd), qos=1)
        try:
            info.wait_for_publish(timeout=10)
        except (ValueError, RuntimeError) as e:
            raise PrinterError(f"The file was uploaded but the start command could not be sent: {e}")
        deadline = time.time() + START_WAIT_S
        on_card = f"The file was uploaded to the printer's SD card as '{remote_name}'; you can start it from the printer's screen."
        while time.time() < deadline:
            st = _current_status(self.id)
            if st.get("state") in ("PREPARE", "RUNNING"):
                return True
            for e in st.get("hms", []):
                if _hms_key(e) in known_hms:
                    continue
                code = _hms_code(e)
                if code == AUTH_REJECTED:
                    raise PrinterError(f"The printer rejected the start command (HMS {code.replace('_', '-')}: command verification failed). "
                                       f"Newer Bambu firmware only accepts print commands from authorized software unless Developer Mode is on "
                                       f"(printer screen: Settings > Network > LAN Only Mode, then Developer Mode). {on_card}")
                raise PrinterError(f"The printer reported HMS error {code.replace('_', '-')} when asked to start. {on_card}")
            time.sleep(0.5)
        return False

    def _upload(self, local_path: Path, remote_name: str) -> None:
        size = local_path.stat().st_size
        ftp = _ImplicitFTPS()
        try:
            ftp.connect(self.host, self.ftp_port, timeout=30)
            ftp.login("bblp", self.code)
            ftp.prot_p()
            ftp.voidcmd("TYPE I")
            conn = ftp.transfercmd(f"STOR {remote_name}")
            try:
                with open(local_path, "rb") as fh:
                    while chunk := fh.read(64 * 1024):
                        conn.sendall(chunk)
            finally:
                conn.close()
            try:
                ftp.voidresp()
            except (ssl.SSLError, EOFError, TimeoutError, ConnectionError):
                pass  # Bambu's FTP server often drops the control channel after the transfer; verify by size instead
            try:
                remote_size = ftp.size(remote_name)
            except (ftplib.Error, OSError, EOFError):
                remote_size = None
            if remote_size is not None and remote_size != size:
                raise PrinterError(f"Upload incomplete ({remote_size} of {size} bytes). Is the SD card full?")
        except ftplib.error_perm as e:
            if str(e).startswith("530"):
                raise PrinterError("The printer rejected the access code (FTP login failed)")
            raise PrinterError(f"The printer refused the upload: {e}")
        except (socket.timeout, TimeoutError):
            raise PrinterError(f"Timed out talking to the printer at {self.host}:{self.ftp_port}")
        except ssl.SSLError as e:
            raise PrinterError(f"Secure connection to the printer failed: {e.reason or e}")
        except OSError as e:
            raise PrinterError(f"Cannot reach the printer at {self.host}:{self.ftp_port} ({e.strerror or e})")
        finally:
            try:
                ftp.quit()
            except Exception:
                ftp.close()


class PrinterError(Exception):
    """A failure the user can act on; surfaced by the API as a 502 with this message."""


BUSY_STATES = {"RUNNING", "PREPARE", "PAUSE"}
START_WAIT_S = 12


AUTH_REJECTED = "0500_0500_0001_0007"


def _hms_code(e: dict[str, Any]) -> str:
    a, c = int(e.get("attr", 0)), int(e.get("code", 0))
    return "%04X_%04X_%04X_%04X" % ((a >> 16) & 0xFFFF, a & 0xFFFF, (c >> 16) & 0xFFFF, c & 0xFFFF)


def _hms_key(e: dict[str, Any]) -> tuple:
    return (e.get("attr"), e.get("code"), e.get("timestamp"))


def _current_status(printer_id: str) -> dict[str, Any]:
    return (store.printers.get(printer_id) or {}).get("status", {})


def _plate_md5(bundle: Path, plate: str) -> str:
    import zipfile
    try:
        with zipfile.ZipFile(bundle) as z:
            return z.read(plate + ".md5").decode().strip()
    except (KeyError, zipfile.BadZipFile, OSError):
        return ""


def _parse_ams(units: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for unit in units:
        base = int(unit.get("id", 0)) * 4
        for tray in unit.get("tray", []):
            ttype = AMS_TYPE_ALIASES.get(tray.get("tray_type", ""), tray.get("tray_type", ""))
            out.append({"slot": base + int(tray.get("id", 0)), "type": ttype,
                        "color": "#" + tray.get("tray_color", "888888")[:6],
                        "remain": tray.get("remain", 100)})
    return out


def _set_status(printer_id: str, update: dict[str, Any]) -> None:
    p = store.printers.get(printer_id)
    if not p:
        return
    status = dict(p.get("status", {}))
    status.update(update)
    status["updated"] = time.time()
    store.printers.update(printer_id, status=status)


def _fingerprint(p: dict[str, Any]) -> tuple:
    return (p.get("host"), p.get("port"), p.get("serial"), p.get("access_code"))


def check_host(host: str) -> None:
    """Refuse targets that can never be a printer on the LAN (loopback, link-local/metadata, multicast)."""
    try:
        ip = ipaddress.ip_address(socket.gethostbyname(host))
    except (socket.gaierror, ValueError, UnicodeError):
        raise ValueError(f"Cannot resolve host {host!r}")
    if ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_unspecified:
        raise ValueError(f"{host} is not an allowed printer address")


def probe(host: str, port: int, serial: str, code: str, timeout: float = 6.0) -> dict[str, Any]:
    """One-shot MQTT connection test used by the admin UI. Never stores anything."""
    check_host(host)
    done = threading.Event()
    result: dict[str, Any] = {"ok": False, "error": "No response from printer"}
    c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"slicyrs-probe-{int(time.time())}")
    c.username_pw_set("bblp", code)
    c.tls_set(cert_reqs=ssl.CERT_NONE)
    c.tls_insecure_set(True)

    def on_connect(client, userdata, flags, rc, props=None):
        if rc != 0:
            result.update(ok=False, error="Printer rejected the access code" if str(rc) in ("Not authorized", "5", "134")
                          else f"MQTT refused: {rc}")
            done.set()
            return
        client.subscribe(f"device/{serial}/report")
        client.publish(f"device/{serial}/request", json.dumps({"pushing": {"sequence_id": "0", "command": "pushall"}}))

    def on_message(client, userdata, msg):
        try:
            rep = json.loads(msg.payload).get("print")
        except ValueError:
            return
        if rep:
            result.update(ok=True, error=None, state=rep.get("gcode_state"), ams=_parse_ams((rep.get("ams") or {}).get("ams", [])))
            done.set()

    c.on_connect, c.on_message = on_connect, on_message
    try:
        c.connect(host, port, keepalive=10)
        c.loop_start()
        done.wait(timeout)
    except OSError as e:
        result.update(ok=False, error=f"Cannot reach {host}:{port} ({e.strerror or e})")
    finally:
        c.loop_stop()
        try:
            c.disconnect()
        except Exception:
            pass
    return result


class LinkManager:
    def __init__(self) -> None:
        self.links: dict[str, PrinterLink] = {}
        self.prints: dict[str, tuple] = {}
        self._lock = threading.Lock()

    def sync(self) -> None:
        """Start links for printers that have connection details; restart changed ones, drop removed ones."""
        with self._lock:
            wanted = {p["id"]: p for p in store.printers.all()
                      if p.get("host") and p.get("serial") and p.get("access_code") and p.get("enabled", True)}
            for pid in list(self.links):
                if pid not in wanted or self.prints.get(pid) != _fingerprint(wanted[pid]):
                    self.links.pop(pid).stop()
                    self.prints.pop(pid, None)
                    if pid not in wanted and store.printers.get(pid):
                        _set_status(pid, {"state": "OFFLINE"})
            for pid, p in wanted.items():
                if pid not in self.links:
                    link = PrinterLink(p)
                    try:
                        link.start()
                        self.links[pid] = link
                        self.prints[pid] = _fingerprint(p)
                    except Exception:  # connection details bad; leave printer offline
                        log.exception("could not start link for %s", pid)

    def get(self, printer_id: str) -> PrinterLink | None:
        return self.links.get(printer_id)


links = LinkManager()
