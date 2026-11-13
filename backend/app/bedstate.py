"""Bed state (A.3): is there something on the printer's build plate? Owned by the printer side of the system so a camera or a
bed-clearing robot can later set it without any change to the rest.

  occupied  a print is running, or has ended and nobody has confirmed the plate was emptied
  clear     someone confirmed it is empty (source "manual" today; "camera" or "robot" later)
  unknown   nothing is known: after a server restart, or when a printer first appears

A finished print always makes the bed `occupied`. Only `clear` lets the allocator start the next job. Parts are assumed
collected once the bed is cleared: nothing tracks them afterwards.
"""
import time
from typing import Any

from . import pipelinecfg, store


def audit(user, action, detail):
    from .auth import audit as _audit  # local import: auth must not be needed to import this module
    _audit(user, action, detail)


BUSY = {"RUNNING", "PREPARE", "PAUSE", "SLICING"}
ENDED = {"FINISH", "FAILED"}
STATES = ("occupied", "clear", "unknown")
CHECKLIST = ("bed_empty", "plate_matches", "filament_matches")


def get(printer: dict[str, Any]) -> dict[str, Any]:
    return printer.get("bed") or {"state": "unknown", "source": None, "confidence": None, "at": None}


def set_state(printer_id: str, state: str, source: str, by: str | None = None, confidence: str = "high", **extra: Any) -> None:
    assert state in STATES
    p = store.printers.get(printer_id)
    if not p:
        return
    old = get(p)
    bed = {"state": state, "source": source, "confidence": confidence, "by": by, "at": time.time(), **extra}
    if state == "occupied" and old.get("state") == "occupied":   # keep when it first became occupied
        bed.setdefault("since", old.get("since") or old.get("at"))
    else:
        bed.setdefault("since", bed["at"])
    store.printers.update(printer_id, bed=bed)


def on_status(printer_id: str, prev_state: str | None, status: dict[str, Any]) -> None:
    """Called for every report from the printer. Only changes in state matter, otherwise a printer that keeps saying
    FINISH would undo the operator's confirmation every few seconds."""
    new = status.get("state")
    if not new or new == prev_state:
        return
    if new in BUSY:
        set_state(printer_id, "occupied", "printer")
    elif new in ENDED and prev_state in BUSY:
        set_state(printer_id, "occupied", "printer", finished=True, finished_at=time.time())
    elif new in ENDED and prev_state in (None, "OFFLINE"):
        p = store.printers.get(printer_id) or {}
        if get(p)["state"] == "clear":
            set_state(printer_id, "unknown", "printer", confidence="low")   # came back showing an old result: plate not known
    elif prev_state in BUSY and new == "IDLE":
        set_state(printer_id, "occupied", "printer", finished=True, finished_at=time.time())   # stopped from the printer's screen


def reset_all() -> None:
    """After a restart nothing about any plate is known."""
    for p in store.printers.all():
        if get(p)["state"] != "unknown":
            set_state(p["id"], "unknown", "restart", confidence="low")


def last_plate(printer_id: str) -> list[dict[str, Any]]:
    """The jobs on the printer's most recent plate: one, or several when owners shared it."""
    jobs = [j for j in store.jobs.all() if j.get("printer_id") == printer_id and j.get("started_at")]
    if not jobs:
        return []
    latest = max(j["started_at"] for j in jobs)
    return [j for j in jobs if j["started_at"] == latest]


def can_set(user: dict[str, Any], printer_id: str) -> bool:
    from . import permissions
    return permissions.can(user, "plate.clear", printer_id)


def confirm_clear(printer_id: str, user: dict[str, Any], checklist: dict[str, bool]) -> dict[str, Any]:
    if not can_set(user, printer_id):
        raise PermissionError("Only the owner of the last print, an operator or an admin can confirm the plate is clear")
    missing = [k for k in CHECKLIST if not checklist.get(k)]
    if missing:
        raise ValueError("Tick every item before confirming: " + ", ".join(m.replace("_", " ") for m in missing))
    p = store.printers.get(printer_id) or {}
    if (p.get("status") or {}).get("state") in BUSY:
        raise ValueError("The printer is printing; the plate cannot be clear")
    others = {j["owner_email"] for j in last_plate(printer_id) if j.get("owner_email")} - {user["email"]}
    set_state(printer_id, "clear", "manual", by=user["email"])
    audit(user, "bed_clear", f"{p.get('name')}: confirmed clear")
    from . import notify
    for email in others:                 # a shared plate: tell the other owners who cleared it, so they stop looking for their part
        notify.emit("plate_cleared", f"{p.get('name')} was cleared", f"{user['email']} confirmed the plate on {p.get('name')} is clear, "
                    "which included your part. You do not need to collect it from the printer.", owners=[email])
    return store.printers.get(printer_id)["bed"]


def mark_occupied(printer_id: str, user: dict[str, Any]) -> dict[str, Any]:
    from . import permissions
    if not permissions.can(user, "plate.mark_occupied"):
        raise PermissionError("Only an operator or an admin can mark a plate as occupied")
    p = store.printers.get(printer_id) or {}
    set_state(printer_id, "occupied", "manual", by=user["email"])
    audit(user, "bed_occupied", f"{p.get('name')}: marked occupied")
    return store.printers.get(printer_id)["bed"]


def reminders(now: float | None = None) -> int:
    """Tell the owner when a print has ended, and the operators if the plate stays unconfirmed past the admin-set delay."""
    from . import notify
    now = now or time.time()
    cfg = pipelinecfg.get()
    sent = 0
    for p in store.printers.all():
        bed = get(p)
        if bed["state"] != "occupied" or not bed.get("finished"):
            continue
        if not bed.get("owner_notified"):
            store.printers.update(p["id"], bed={**bed, "owner_notified": now})
            for j in last_plate(p["id"]):
                if j.get("owner_email"):
                    sent += notify.emit("finished", f"Your print on {p['name']} has finished",
                                        f"{j['name']} has finished on {p['name']}. Please collect it and confirm the build plate is clear.",
                                        owners=[j["owner_email"]], link="/#/jobs", job=j)
            bed = {**bed, "owner_notified": now}
        elif not bed.get("operators_notified") and now - bed["owner_notified"] >= cfg["operator_delay_min"] * 60:
            store.printers.update(p["id"], bed={**bed, "operators_notified": now})
            sent += notify.emit("plate_waiting", f"{p['name']} is waiting to be cleared",
                                f"{p['name']} finished a print and its plate has not been cleared for {cfg['operator_delay_min']} minutes. "
                                f"You can clear it at any time.", link="/#/fleet")
    return sent
