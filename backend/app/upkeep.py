"""Printers that are out of the pool for a while (2.6): maintenance set by an operator, and "needs attention" set by the app
when a print fails or the printer raises an error mid-print. Either keeps the printer out of assignment until an operator or
admin marks it ready; the printer itself stays set up. Also the estimated wait for each printer (2.5)."""
import time
from typing import Any

from . import store

NOTE_MAX = 200
HOLDING = {"assigned", "slicing", "ready"}
BUSY = {"RUNNING", "PREPARE", "PAUSE", "SLICING"}


def unavailable(printer: dict[str, Any]) -> str | None:
    """Why the printer is out of the pool right now, in words anyone may read; None when it is available."""
    m = printer.get("maintenance") or {}
    if m.get("on"):
        return "In maintenance" + (f": {m['note']}" if m.get("note") else "")
    a = printer.get("attention") or {}
    if a.get("reason"):
        return f"Needs attention: {a['reason']}"
    return None


def _audit(user: dict[str, Any] | None, action: str, detail: str) -> None:
    from .auth import audit   # local import: auth imports the store and mailer
    audit(user, action, detail)


def set_maintenance(printer_id: str, on: bool, note: str, user: dict[str, Any]) -> dict[str, Any]:
    p = store.printers.get(printer_id) or {}
    note = (note or "").strip()[:NOTE_MAX]
    rec = {"on": bool(on), "note": note if on else "", "by": user["email"], "at": time.time()}
    out = store.printers.update(printer_id, maintenance=rec)
    _audit(user, "printer_maintenance", f"{p.get('name')}: {'in maintenance' if on else 'back in service'}{(' (' + note + ')') if on and note else ''}")
    return out  # type: ignore[return-value]


def flag_attention(printer_id: str, reason: str, job_id: str | None = None) -> None:
    """Called by the app: the printer leaves the pool until someone has looked at it."""
    p = store.printers.get(printer_id)
    if not p:
        return
    store.printers.update(printer_id, attention={"reason": reason[:NOTE_MAX], "at": time.time(), "job_id": job_id})
    _audit(None, "printer_needs_attention", f"{p.get('name')}: {reason[:NOTE_MAX]}")


def mark_ready(printer_id: str, user: dict[str, Any]) -> dict[str, Any]:
    p = store.printers.get(printer_id) or {}
    out = store.printers.update(printer_id, attention=None)
    _audit(user, "printer_ready", f"{p.get('name')}: marked ready after needing attention")
    return out  # type: ignore[return-value]


def wait_s(printer: dict[str, Any], now: float | None = None) -> float | None:
    """Rough wait before this printer could start something new (2.5): the rest of the current print plus the estimates of the
    jobs already sent to it. None when it is out of the pool or offline, because then nobody can say."""
    from .forecast import estimate_s
    now = now or time.time()
    st = printer.get("status") or {}
    if unavailable(printer) or st.get("state") in ("OFFLINE", "ERROR") or not printer.get("enabled", True):
        return None
    total = 0.0
    if st.get("state") in BUSY:
        total += float(st.get("remaining_min") or 0) * 60
    for j in store.jobs.all():
        if j.get("printer_id") == printer["id"] and j.get("phase") in HOLDING and not j.get("batch_lead"):
            total += estimate_s(j)
    return total
