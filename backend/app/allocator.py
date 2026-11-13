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

from . import assign, bedstate, forecast, jobstate, notify, pipeline, upkeep, pipelinecfg, plates, queue, store, taskqueue
from .bedstate import BUSY
from .catalog import job_nozzle, job_position

log = logging.getLogger("allocator")
HOLDING = {"assigned", "slicing", "ready", "printing"}
_lock = threading.Lock()


def _holds(j: dict[str, Any]) -> bool:
    """A job holds its printer while printing, or while it is a pool job that is assigned, slicing or ready.
    A job sliced directly for a chosen printer (not from the pool) that is not being printed holds nothing."""
    ph = j.get("phase")
    return ph == "printing" or (ph in HOLDING and bool(j.get("pooled")))


def free_printers() -> list[dict[str, Any]]:
    held = {j.get("printer_id") for j in store.jobs.all() if _holds(j)}
    out = []
    for p in store.printers.all():
        st = (p.get("status") or {}).get("state", "OFFLINE")
        if not p.get("enabled", True) or p["id"] in held or bedstate.get(p)["state"] != "clear" or upkeep.unavailable(p):
            continue
        if st in BUSY or st == "ERROR" or (st == "OFFLINE" and not p.get("assume_available")):
            continue
        out.append(p)
    return out


def priority(job: dict[str, Any]) -> tuple:
    risk = bool(job.get("at_risk"))
    return (0 if risk else 1, (job.get("pickup_by") or 0) if risk else 0, job.get("submitted_at") or 0)


def waiting_reason(job: dict[str, Any]) -> str:
    """Why a pooled job has not been given a printer yet, in words for the job's owner."""
    held = {j.get("printer_id") for j in store.jobs.all() if _holds(j)}
    printers = store.printers.all()
    if not printers:
        return "No printers are set up"
    can = {c.printer_id for c in assign.rank([{**p, "assume_available": True} for p in printers], tuple(job["dims"]), job["filament"], job_nozzle(job), job_position(job))
           if c.eligible}
    if not can:
        return "No printer can print this part"
    if job.get("pinned_printer"):                  # lined up for one printer by an operator: only that one matters
        pinned = store.printers.get(job["pinned_printer"])
        if pinned:
            printers = [pinned]
            ahead = [j for j in queue.lane_jobs(pinned["id"]) if queue.sort_key(j) < queue.sort_key(job)]
            prefix = f"Lined up for {pinned['name']}" + (f", {len(ahead)} ahead" if ahead else "") + ": "
            rest = waiting_reason({**job, "pinned_printer": None, "_only": pinned["id"]})
            return prefix + rest
    if job.get("_only"):
        printers = [p for p in printers if p["id"] == job["_only"]]
    why = []
    free_ids = {f["id"] for f in free_printers()}
    for p in printers:
        if p["id"] not in can:
            continue
        if p["id"] in free_ids:
            return plates.hold_reason(job, time.time()) or "Starting soon"
        st = (p.get("status") or {}).get("state", "OFFLINE")
        bed = bedstate.get(p)["state"]
        if not p.get("enabled", True):
            why.append(f"{p['name']} is disabled")
        elif upkeep.unavailable(p):
            why.append(f"{p['name']}: {upkeep.unavailable(p)}")
        elif p["id"] in held:
            h = next((j for j in store.jobs.all() if j.get("printer_id") == p["id"] and _holds(j)), {})
            why.append(f"{p['name']} is held by \"{h.get('name', 'another job')}\" ({h.get('phase')})")
        elif st in BUSY:
            why.append(f"{p['name']} is printing")
        elif st == "ERROR" or (st == "OFFLINE" and not p.get("assume_available")):
            why.append(f"{p['name']} is {'in error' if st == 'ERROR' else 'offline'}")
        elif bed == "occupied":
            why.append(f"{p['name']}: finished print still on the plate; its owner or an operator needs to confirm it is clear")
        elif bed != "clear":
            why.append(f"{p['name']}: plate state unknown; an operator needs to confirm it is clear")
    return "; ".join(why) or plates.hold_reason(job, time.time()) or "Starting soon"


def assign_pool() -> int:
    for j in store.jobs.all():
        if j.get("phase") == "pooled":
            w = waiting_reason(j)
            if w != j.get("waiting_for"):
                store.jobs.update(j["id"], waiting_for=w)
    n = 0
    now = time.time()
    pooled = sorted((j for j in store.jobs.all() if j.get("phase") == "pooled"), key=queue.sort_key)   # staff order first (2.2)
    taken: set[str] = set()                      # jobs that joined another job's plate during this pass
    for job in pooled:
        if job["id"] in taken:
            continue
        free = free_printers()
        if not free:
            break
        if job.get("pinned_printer"):              # lined up for one printer: it waits for that one only
            free = [p for p in free if p["id"] == job["pinned_printer"]]
            if not free:
                continue
        ranked = assign.rank([{**p, "queue_depth": 0} for p in free], tuple(job["dims"]), job["filament"], job_nozzle(job), job_position(job))
        best = next((c for c in ranked if c.eligible), None)
        if best is None:
            continue
        printer = store.printers.get(best.printer_id)
        reason = "; ".join(best.reasons)
        if job.get("allow_batching"):
            partners = plates.find_partners(job, printer, [j for j in pooled if j["id"] not in taken
                                                           and j.get("pinned_printer") in (None, printer["id"])], now)
            if not partners and plates.hold_reason(job, now):
                continue                         # keep the printer for others while this job waits for a partner
            if partners:
                plates.form(job, partners, printer)
                taken.update(p["id"] for p in partners)
                reason += f"; shares the plate with {len(partners)} other job(s)"
        jobstate.transition(job["id"], "assigned", "allocator", printer_id=printer["id"], printer_name=printer["name"],
                            printer_model=best.model, bed_type=printer.get("bed_type"), loaded_slot=best.loaded_slot,
                            candidates=[c.asdict() for c in ranked], reason=reason)
        jobstate.transition(job["id"], "slicing", "allocator", state="slicing")
        pipeline.request_slice(job["id"])
        n += 1
    return n


def expire_holds(now: float) -> int:
    """A sliced job its owner has not started goes back to the pool so the printer is not held for ever."""
    hold = pipelinecfg.get()["ready_hold_min"] * 60
    n = 0
    for j in store.jobs.all():
        if (j.get("phase") == "ready" and j.get("pooled") and not j.get("batch_lead") and not plates.auto_start(j)
                and now - (j.get("phase_at") or now) > hold):
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
            _tell("pickup_at_risk", j, "may miss its pickup time", f"{j['name']} is now expected to finish after the pickup time you asked for.")
        elif not late_by_forecast and j.get("at_risk"):
            store.jobs.update(j["id"], at_risk=False)
            jobstate.event(j["id"], "recovered", "allocator")
        if now > by and not j.get("late_notified"):
            store.jobs.update(j["id"], late_notified=now)
            jobstate.event(j["id"], "late", "allocator", pickup_by=by)
            _tell("pickup_late", j, "is past its pickup time", f"{j['name']} has not finished by the pickup time you asked for.")


def _tell(kind: str, job: dict[str, Any], subject: str, body: str) -> None:
    if job.get("owner_email"):
        notify.emit(kind, f"Your print {subject}", body, owners=[job["owner_email"]], link="/#/jobs", job=job)


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
