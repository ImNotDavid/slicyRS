"""Pause, resume and stop a running print from the web (2.4).

Who may do it is decided in `permissions` (print.pause, print.stop). The command goes through the printer's adapter (direct
LAN link or gateway); the printer's own status report then drives everything else as before: a pause alerts the plate's owners
and the operators (but not the person who pressed the button), and a stop ends the job without flagging the printer as needing
attention, since a person chose to stop it.
"""
import time
from typing import Any

from . import adapter, jobstate, plates, store
from .adapter import AdapterError

ALLOWED_FROM = {"pause": {"RUNNING", "PREPARE"}, "resume": {"PAUSE"}, "stop": {"RUNNING", "PREPARE", "PAUSE"}}
RECENT_S = 120         # a printer report this soon after a web command is taken as its result


class ControlError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status, self.message = status, message


def recent_control(job: dict[str, Any], action: str, now: float | None = None) -> dict[str, Any] | None:
    """The web command behind a printer report, if one was sent for this job's plate a moment ago."""
    c = job.get("control") or {}
    return c if c.get("action") == action and (now or time.time()) - (c.get("at") or 0) < RECENT_S else None


def run(job: dict[str, Any], action: str, user: dict[str, Any]) -> dict[str, Any]:
    if action not in ALLOWED_FROM:
        raise ControlError(400, "Unknown command")
    if job.get("phase") != "printing" or not job.get("printer_id"):
        raise ControlError(409, "This job is not printing")
    printer = store.printers.get(job["printer_id"]) or {}
    state = (printer.get("status") or {}).get("state")
    if state not in ALLOWED_FROM[action]:
        raise ControlError(409, {"pause": "The print is not running", "resume": "The print is not paused",
                                 "stop": "The printer is not printing"}[action] + f" (the printer reports {state or 'nothing'})")
    link = adapter.get(printer["id"])
    if link is None:
        raise ControlError(409, "This printer has no connection from the server; use the printer's screen")
    plate = plates.jobs_on_plate(job)
    now = time.time()
    for j in plate:                       # remembered so the printer's report can be told apart from a fault
        store.jobs.update(j["id"], control={"action": action, "by": user["email"], "at": now})
        jobstate.event(j["id"], f"web_{action}", user["email"])
    try:
        done = link.control(action)
    except (AdapterError, OSError, ValueError) as e:
        raise ControlError(502, str(e))
    except Exception as e:                # the LAN link raises its own PrinterError
        raise ControlError(502, getattr(e, "message", None) or str(e))
    from .auth import audit
    audit(user, f"print_{action}", f"{job['name']} on {printer.get('name')}" + ("" if done else " (sent; the printer had not confirmed yet)"))
    return {"ok": True, "done": done, "state": ((store.printers.get(printer["id"]) or {}).get("status") or {}).get("state")}
