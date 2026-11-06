"""Allocator (A.2). Jobs wait in one shared pool; a printer that is free (enabled, reachable, idle, plate confirmed clear,
nothing else holding it) takes the best job it can print.

Order: jobs at risk of missing their pickup time come first (earliest pickup first), then everyone else in submission
order. Colour is recorded on the job but not matched yet (parked on the roadmap).
Run `tick()` often; it is safe to call at any time and from more than one place.
"""
import logging
import threading
import time
from typing import Any

from . import assign, bedstate, forecast, jobstate, mailer, pipeline, pipelinecfg, store, taskqueue
from .bedstate import BUSY

log = logging.getLogger("allocator")
HOLDING = {"assigned", "slicing", "ready", "printing"}
_lock = threading.Lock()


def free_printers() -> list[dict[str, Any]]:
    held = {j.get("printer_id") for j in store.jobs.all() if j.get("phase") in HOLDING}
    out = []
    for p in store.printers.all():
        st = (p.get("status") or {}).get("state", "OFFLINE")
        if not p.get("enabled", True) or p["id"] in held or bedstate.get(p)["state"] != "clear":
            continue
        if st in BUSY or st == "ERROR" or (st == "OFFLINE" and not p.get("assume_available")):
            continue
        out.append(p)
    return out


def priority(job: dict[str, Any]) -> tuple:
    risk = bool(job.get("at_risk"))
    return (0 if risk else 1, (job.get("pickup_by") or 0) if risk else 0, job.get("submitted_at") or 0)


def assign_pool() -> int:
    n = 0
    pooled = sorted((j for j in store.jobs.all() if j.get("phase") == "pooled"), key=priority)
    for job in pooled:
        free = free_printers()
        if not free:
            break
        ranked = assign.rank([{**p, "queue_depth": 0} for p in free], tuple(job["dims"]), job["filament"])
        best = next((c for c in ranked if c.eligible), None)
        if best is None:
            continue
        printer = store.printers.get(best.printer_id)
        jobstate.transition(job["id"], "assigned", "allocator", printer_id=printer["id"], printer_name=printer["name"],
                            printer_model=best.model, bed_type=printer.get("bed_type"), loaded_slot=best.loaded_slot,
                            candidates=[c.asdict() for c in ranked], reason="; ".join(best.reasons))
        jobstate.transition(job["id"], "slicing", "allocator", state="slicing")
        pipeline.request_slice(job["id"])
        n += 1
    return n


def expire_holds(now: float) -> int:
    """A sliced job its owner has not started goes back to the pool so the printer is not held for ever."""
    hold = pipelinecfg.get()["ready_hold_min"] * 60
    n = 0
    for j in store.jobs.all():
        if j.get("phase") == "ready" and not j.get("auto_start") and j.get("submitted_at") and now - (j.get("phase_at") or now) > hold:
            jobstate.transition(j["id"], "pooled", "allocator", state="pooled", reason="not started within the hold time",
                                printer_id=None, printer_name=None)
            n += 1
    return n


def check_pickups(now: float) -> None:
    """Flag jobs that will probably miss their pickup time (once), and tell their owner; flag jobs already past it."""
    for j in store.jobs.all():
        by = j.get("pickup_by")
        if not by or j.get("phase") not in ("pooled", "assigned", "slicing", "ready", "printing"):
            continue
        if j["phase"] == "printing":
            end = (j.get("started_at") or now) + forecast.estimate_s(j)
            late_by_forecast = end + pipelinecfg.get()["cooling_margin_min"] * 60 > by
        else:
            late_by_forecast = not forecast.pickup_check(j, by, now)["likely"]
        if late_by_forecast and not j.get("at_risk"):
            store.jobs.update(j["id"], at_risk=True)
            jobstate.event(j["id"], "at_risk", "allocator", pickup_by=by)
            _tell(j, "may miss its pickup time", f"{j['name']} is now expected to finish after the pickup time you asked for.")
        elif not late_by_forecast and j.get("at_risk"):
            store.jobs.update(j["id"], at_risk=False)
            jobstate.event(j["id"], "recovered", "allocator")
        if now > by and not j.get("late_notified"):
            store.jobs.update(j["id"], late_notified=now)
            jobstate.event(j["id"], "late", "allocator", pickup_by=by)
            _tell(j, "is past its pickup time", f"{j['name']} has not finished by the pickup time you asked for.")


def _tell(job: dict[str, Any], subject: str, body: str) -> None:
    if job.get("owner_email"):
        mailer.send_in_background(job["owner_email"], f"Your print {subject}", body)


def tick(now: float | None = None) -> dict[str, int]:
    now = now or time.time()
    with _lock:
        for dead in taskqueue.reap_dead(now):
            pipeline.on_dead(dead)
        held = expire_holds(now)
        check_pickups(now)
        assigned = assign_pool()
        reminders = bedstate.reminders(now)
    return {"assigned": assigned, "returned_to_pool": held, "reminders": reminders}


def start_background(interval_s: float) -> None:
    if interval_s <= 0:
        return

    def loop():
        while True:
            time.sleep(interval_s)
            try:
                tick()
            except Exception:
                log.exception("allocator tick failed")

    threading.Thread(target=loop, daemon=True, name="allocator").start()
