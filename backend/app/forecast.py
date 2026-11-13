"""Submit-time forecast (A.2): when would this job finish if it were submitted now? Used to refuse a pickup time that is
unlikely, to offer the earliest likely one, and later to spot jobs that are slipping.

It is a plain list-scheduling simulation, not a promise: each printer becomes free when its current print and the jobs
already holding it are done; jobs waiting in the pool ahead of this one (submission order) take the earliest eligible
printer; then this job takes the earliest eligible one. A cooling margin is added at the end, and the slicer's estimate
for this job is padded by an admin-set percentage because estimates run short.
"""
import time
from typing import Any

from . import assign, pipelinecfg, store
from .bedstate import BUSY
from .catalog import job_nozzle, job_position

HOLDING = {"assigned", "slicing", "ready"}
DEFAULT_ESTIMATE_S = 1800  # when a job has no estimate yet


def _same_plate(a: dict[str, Any], b: dict[str, Any]) -> bool:
    pa = a.get("batch_lead") or (a["id"] if a.get("members") else None)
    return pa is not None and pa == (b.get("batch_lead") or (b["id"] if b.get("members") else None))


def estimate_s(job: dict[str, Any]) -> float:
    if job.get("members") and not job.get("stats"):       # a plate not sliced yet: its parts print one plate's worth together
        return own_estimate_s(job) + sum(own_estimate_s(store.jobs.get(i) or {}) for i in job["members"])
    return own_estimate_s(job)


def own_estimate_s(job: dict[str, Any]) -> float:
    return float((job.get("stats") or {}).get("time_s") or (job.get("preview") or {}).get("time_s") or DEFAULT_ESTIMATE_S)


def _fleet() -> list[dict[str, Any]]:
    return [p for p in store.printers.all() if p.get("enabled", True)]


def eligible_printers(dims, filament: str, nozzle: float = 0.4, offset: tuple[float, float] = (0.0, 0.0)) -> list[dict[str, Any]]:
    """Printers that could ever print this part: every hard rule except being online right now."""
    out = []
    for p in _fleet():
        c = assign.evaluate({**p, "assume_available": True}, tuple(dims), filament, nozzle, offset)
        if c.eligible:
            out.append(p)
    return out


def predict(job: dict[str, Any], now: float | None = None) -> dict[str, Any]:
    now = now or time.time()
    cfg = pipelinecfg.get()
    margin = cfg["cooling_margin_min"] * 60
    printers = eligible_printers(job["dims"], job["filament"], job_nozzle(job), job_position(job))
    if job.get("pinned_printer"):                       # lined up for one printer by staff (2.2)
        printers = [p for p in printers if p["id"] == job["pinned_printer"]] or printers
    if not printers:
        return {"feasible": False, "earliest_finish": None, "jobs_ahead": 0, "printer_id": None, "printer_name": None}

    jobs = store.jobs.all()
    free_at: dict[str, float] = {}
    for p in printers:
        st = p.get("status") or {}
        t = now
        if st.get("state") in BUSY:
            t += float(st.get("remaining_min") or 0) * 60
        for j in jobs:                                   # work already committed to this printer
            if (j.get("printer_id") == p["id"] and j.get("phase") in HOLDING and j["id"] != job["id"]
                    and not _same_plate(j, job) and not j.get("batch_lead")):
                t += estimate_s(j) + margin
        free_at[p["id"]] = t

    mine = job.get("submitted_at") or now
    ahead = sorted((j for j in jobs if j.get("phase") == "pooled" and j["id"] != job["id"] and (j.get("submitted_at") or 0) <= mine),
                   key=lambda j: j.get("submitted_at") or 0)
    counted = 0
    for j in ahead:                                      # jobs in front take the earliest printer that can run them
        ok = [p for p in eligible_printers(j["dims"], j["filament"], job_nozzle(j), job_position(j)) if p["id"] in free_at]
        if ok:
            best = min(ok, key=lambda p: free_at[p["id"]])
            free_at[best["id"]] += estimate_s(j) + margin
            counted += 1

    best = min(printers, key=lambda p: free_at[p["id"]])
    padded = estimate_s(job) * (1 + cfg["estimate_buffer_pct"] / 100)
    finish = free_at[best["id"]] + padded + margin
    return {"feasible": True, "earliest_finish": finish, "jobs_ahead": counted, "printer_id": best["id"],
            "printer_name": best["name"], "estimate_s": estimate_s(job)}


def pickup_check(job: dict[str, Any], pickup_by: float | None, now: float | None = None) -> dict[str, Any]:
    f = predict(job, now)
    f["pickup_by"] = pickup_by
    f["likely"] = bool(f["feasible"]) and (pickup_by is None or f["earliest_finish"] <= pickup_by)
    return f
