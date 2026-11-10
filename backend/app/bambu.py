"""Server-side wiring of the Bambu LAN connector: links for printers this server reaches directly, kept in step with the printer
records. Printers behind a remote gateway are handled by `gateways.py` instead."""
import logging
import threading
from pathlib import Path
from typing import Any

from . import bambulan, store
from .bambulan import (AUTH_REJECTED, BUSY_STATES, PrinterError, _hms_code, _hms_key, _parse_ams, _plate_md5,  # noqa: F401  (re-exported)
                       check_host, probe)
from .crypto import unseal
from .printerstate import apply as _set_status

log = logging.getLogger("bambu")


class PrinterLink(bambulan.LanLink):
    """A LAN link whose status goes into the stored printer record and whose settings come from it."""

    def __init__(self, printer: dict[str, Any]):
        pid = printer["id"]
        super().__init__(printer, unseal(printer.get("access_code")) or "", _set_status,
                         lambda: (store.printers.get(pid) or {}).get("status") or {},
                         lambda: (store.printers.get(pid) or {}).get("bed_type"))


def _fingerprint(p: dict[str, Any]) -> tuple:
    return (p.get("host"), p.get("port"), p.get("serial"), p.get("access_code"))


class LinkManager:
    def __init__(self) -> None:
        self.links: dict[str, PrinterLink] = {}
        self.prints: dict[str, tuple] = {}
        self._lock = threading.Lock()

    def sync(self) -> None:
        """Start links for printers that have connection details; restart changed ones, drop removed ones."""
        with self._lock:
            wanted = {p["id"]: p for p in store.printers.all()
                      if p.get("host") and p.get("serial") and p.get("access_code") and p.get("enabled", True) and not p.get("gateway")}
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
        from .gateways import hub   # printers behind a gateway changed too
        hub.push_all()

    def get(self, printer_id: str) -> PrinterLink | None:
        return self.links.get(printer_id)


links = LinkManager()
