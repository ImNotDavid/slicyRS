"""The queue that operators and admins can rearrange (2.2).

Pooled jobs normally go to the first free printer that fits, most urgent first. Staff can change that:
  - put a job ahead of others in the pool (`queue_rank`),
  - line a job up for one printer (`pinned_printer`): it then waits for that printer only, ahead of the pool.
Ranks are positions inside a lane (the pool, or one printer's line). Jobs nobody has moved keep the automatic order and sit
after the ones that were placed by hand. "Let the pool decide" clears every rank and pin.
"""
import time
from typing import Any

from . import assign, jobstate, store, upkeep
from .catalog import FILAMENTS, job_nozzle, job_position

POOL = "pool"


def automatic_key(job: dict[str, Any]) -> tuple:
    """The pool's own order: jobs at risk of missing their pickup first (earliest pickup first), then submission order."""
    risk = bool(job.get("at_risk"))
    return (0 if risk else 1, (job.get("pickup_by") or 0) if risk else 0, job.get("submitted_at") or 0)


def sort_key(job: dict[str, Any]) -> tuple:
    """Order in which the allocator offers jobs to free printers: pinned jobs first (each only to its own printer), then the
    pool; inside each, the hand-placed order and then the automatic one."""
    rank = job.get("queue_rank")
    return (0 if job.get("pinned_printer") else 1, 0 if rank is not None else 1, rank if rank is not None else 0, *automatic_key(job))


def lane_of(job: dict[str, Any]) -> str:
    return job.get("pinned_printer") or POOL


def lane_jobs(lane: str) -> list[dict[str, Any]]:
    return sorted((j for j in store.jobs.all() if j.get("phase") == "pooled" and lane_of(j) == lane), key=sort_key)


def fits(job: dict[str, Any], printer: dict[str, Any]) -> str | None:
    """Why a job could not be lined up for a printer, or None. Being busy or offline is fine (it waits); being unable to print
    the part, or being out of service, is not."""
    from . import groups as G
    if not G.printer_allows(printer, G.job_group(job)):
        return "the printer is only for other groups"
    if (why := upkeep.unavailable(printer)):
        return why[0].lower() + why[1:]
    c = assign.evaluate({**printer, "assume_available": True}, tuple(job["dims"]), job["filament"], job_nozzle(job), job_position(job))
    return None if c.eligible else (c.blockers[0] if c.blockers else "cannot print this job")


def move(job_id: str, lane: str, index: int, user: dict[str, Any]) -> dict[str, Any]:
    job = store.jobs.get(job_id)
    if not job or job.get("phase") != "pooled":
        raise ValueError("Only jobs waiting in the pool can be moved")
    if job.get("batch_lead"):
        raise ValueError("This part shares a plate; move the plate's first job instead")
    if lane != POOL:
        printer = store.printers.get(lane)
        if not printer or not printer.get("enabled", True):
            raise ValueError("Unknown printer")
        if (why := fits(job, printer)):
            raise ValueError(f"{job['name']} cannot go to {printer['name']}: {why}")
    others = [j for j in lane_jobs(lane) if j["id"] != job_id]
    index = max(0, min(index, len(others)))
    ordered = others[:index] + [job] + others[index:]
    for i, j in enumerate(ordered):            # the whole lane becomes hand-placed so the new order is exactly what was shown
        store.jobs.update(j["id"], queue_rank=float(i), pinned_printer=None if lane == POOL else lane)
    where = "the pool" if lane == POOL else store.printers.get(lane)["name"]
    jobstate.event(job_id, "queue_moved", user["email"], lane=lane, position=index + 1)
    from .auth import audit
    audit(user, "queue_move", f"{job['name']} to {where}, position {index + 1}")
    return {"ok": True, "lane": lane, "position": index + 1}


def reset(user: dict[str, Any]) -> int:
    n = 0
    for j in store.jobs.all():
        if j.get("phase") == "pooled" and (j.get("queue_rank") is not None or j.get("pinned_printer")):
            store.jobs.update(j["id"], queue_rank=None, pinned_printer=None)
            n += 1
    from .auth import audit
    audit(user, "queue_reset", f"{n} job(s) back to the automatic order")
    return n


def _card(job: dict[str, Any], printers: list[dict[str, Any]], est) -> dict[str, Any]:
    f = FILAMENTS.get(job.get("filament"), {})
    return {"id": job["id"], "name": job["name"], "owner": job.get("owner_email"), "filament": job.get("filament"), "nozzle_mm": job_nozzle(job),
            "enclosed": bool(f.get("enclosed")), "estimate_s": est(job), "pickup_by": job.get("pickup_by"), "at_risk": bool(job.get("at_risk")),
            "shared": bool(job.get("members") or job.get("allow_batching")), "phase": job.get("phase"), "waiting_for": job.get("waiting_for"),
            "hand_placed": job.get("queue_rank") is not None,
            "fits": {p["id"]: fits(job, p) for p in printers} if job.get("phase") == "pooled" else {}}


def board(user: dict[str, Any] | None = None) -> dict[str, Any]:
    """Everything the Queue page shows: the pool and every printer's line, with what is printing and what is on its way.
    With `user`, waiting jobs are limited to the groups where they may manage the queue (3.0)."""
    from .forecast import estimate_s
    from .routes.common import live_progress
    printers = [p for p in store.printers.all() if p.get("enabled", True)]
    jobs = store.jobs.all()
    from . import groups as G
    allowed = G.groups_with(user, "queue") if user else None
    ok = (lambda j: True) if allowed is None else (lambda j: G.job_group(j) in allowed)
    lanes = [{"id": POOL, "jobs": [_card(j, printers, estimate_s) for j in lane_jobs(POOL) if not j.get("batch_lead") and ok(j)]}]
    for p in printers:
        mine = [j for j in jobs if j.get("printer_id") == p["id"] and not j.get("batch_lead")]
        now = next((j for j in mine if j.get("phase") == "printing"), None)
        coming = sorted((j for j in mine if j.get("phase") in ("assigned", "slicing", "ready") and j.get("pooled")), key=lambda j: j.get("phase_at") or 0)
        st = p.get("status") or {}
        lanes.append({
            "id": p["id"], "name": p["name"], "model": p["model"], "nozzle_mm": assign.nozzle_of(p), "state": st.get("state"),
            "unavailable": upkeep.unavailable(p), "wait_s": upkeep.wait_s(p), "bed": (p.get("bed") or {}).get("state", "unknown"),
            "now": ({**_card(now, printers, estimate_s), "live": live_progress(now)} if now else None),
            "coming": [_card(j, printers, estimate_s) for j in coming],           # already assigned: on their way, not movable
            "jobs": [_card(j, printers, estimate_s) for j in lane_jobs(p["id"]) if not j.get("batch_lead") and ok(j)],
        })
    return {"lanes": lanes, "at": time.time()}
