"""Plates and batching (A.6). Opt-in jobs from any users can share one build plate.

A plate is a *lead* job plus *member* jobs. The lead carries the plate's slice, printer and print; its `members` lists the
other jobs. Each member has `batch_lead` pointing at the lead and mirrors the lead's phase and print result, so every owner
sees their own job move. A job alone is just a plate of one (no members).

Two jobs may share a plate only if both opted in, use the same material and slicer settings, have compatible colours,
fit together on the chosen printer, and the combined print stays within the plate limits and every pickup time.
The footprint check here is quick and slightly conservative; the slicer's own arrange is the authority, and when it cannot
fit everything the last job added is returned to the pool and the plate is sliced again.
"""
import hashlib
import json
import logging
from typing import Any

from . import assign, forecast, jobstate, pipelinecfg, store
from .catalog import PRINTER_MODELS
from .meshio import load_mesh

log = logging.getLogger("plates")

EDGE_MM = 5          # kept clear around the bed edge
GAP_MM = 8           # between parts (brim and skirt included)
MIRRORED = {"assigned", "slicing", "ready", "printing", "printed", "failed", "unknown"}
# what a member copies from its lead each time the lead moves
MIRROR_KEYS = ("printer_id", "printer_name", "printer_model", "bed_type", "loaded_slot", "gcode", "bundle", "stats",
               "result", "started_at", "remote_name", "printed", "queued", "error")


def lead_of(job: dict[str, Any]) -> dict[str, Any] | None:
    """The job that carries the plate this job is on (itself when it leads or is alone)."""
    if job.get("batch_lead"):
        return store.jobs.get(job["batch_lead"])
    return job


def jobs_on_plate(job: dict[str, Any]) -> list[dict[str, Any]]:
    lead = lead_of(job)
    if lead is None:
        return [job]
    return [lead] + [m for m in (store.jobs.get(i) for i in lead.get("members") or []) if m]


def plate_id(job: dict[str, Any]) -> str | None:
    return job.get("batch_lead") or (job["id"] if job.get("members") else None)


def auto_start(lead: dict[str, Any]) -> bool:
    """A plate starts by itself only if everybody on it asked for that."""
    return all(j.get("auto_start") for j in jobs_on_plate(lead))


def is_owner(user: dict[str, Any], job: dict[str, Any]) -> bool:
    return any(j.get("owner_id") == user["id"] for j in jobs_on_plate(job))


# ------------------------------------------------------------------ compatibility

def fingerprint(job: dict[str, Any]) -> str:
    """Slicer settings apply to the whole plate, so only jobs with identical settings can share one."""
    spec = job.get("spec") or {}
    body = json.dumps([job.get("filament"), spec.get("preset"), spec.get("settings") or {}], sort_keys=True)
    return hashlib.sha1(body.encode()).hexdigest()


def colours_ok(jobs: list[dict[str, Any]]) -> bool:
    named = {(j.get("colour") or "any").lower() for j in jobs} - {"any"}
    return len(named) <= 1


def _shelf(items: list[tuple[float, float]], width: float, depth: float) -> bool:
    x = y = row = 0.0
    for w, h in sorted(items, key=lambda i: -i[1]):
        if w > width:
            return False
        if x + w > width:
            y, x, row = y + row, 0.0, 0.0
        if y + h > depth:
            return False
        x += w
        row = max(row, h)
    return True


def footprints_fit(dims: list[tuple[float, float, float]], bed: tuple[float, float, float]) -> bool:
    """Can these bounding boxes (each free to turn 90 degrees) be laid out on the bed with gaps? A shelf layout tried four ways."""
    width, depth = bed[0] - 2 * EDGE_MM + GAP_MM, bed[1] - 2 * EDGE_MM + GAP_MM
    boxes = [(d[0] + GAP_MM, d[1] + GAP_MM) for d in dims]
    for bw, bd in ((width, depth), (depth, width)):
        for long_along_row in (True, False):
            items = [(max(b), min(b)) if long_along_row else (min(b), max(b)) for b in boxes]
            if _shelf(items, bw, bd):
                return True
    return False


def volume_mm3(job: dict[str, Any]) -> float:
    from .config import JOBS_DIR
    try:
        return abs(float(load_mesh(JOBS_DIR / job["id"] / job["input_file"]).volume)) or 1.0
    except Exception:
        d = job.get("dims") or (1, 1, 1)
        return float(d[0] * d[1] * d[2]) * 0.3


def plate_estimate_s(jobs: list[dict[str, Any]]) -> float:
    return sum(forecast.estimate_s(j) for j in jobs)


def _pickup_ok(jobs: list[dict[str, Any]], now: float) -> bool:
    cfg = pipelinecfg.get()
    finish = now + plate_estimate_s(jobs) * (1 + cfg["estimate_buffer_pct"] / 100) + cfg["cooling_margin_min"] * 60
    return all(not j.get("pickup_by") or finish <= j["pickup_by"] for j in jobs)


def find_partners(lead: dict[str, Any], printer: dict[str, Any], pool: list[dict[str, Any]], now: float) -> list[dict[str, Any]]:
    """Other pooled jobs that can share a plate with `lead` on `printer`, added one at a time in pool order."""
    cfg = pipelinecfg.get()
    bed = PRINTER_MODELS[printer["model"]]["bed"]
    fp = fingerprint(lead)
    chosen: list[dict[str, Any]] = []
    for cand in pool:
        if cand["id"] == lead["id"] or not cand.get("allow_batching") or cand.get("phase") != "pooled":
            continue
        if fingerprint(cand) != fp or cand["id"] in (lead.get("no_batch_with") or []) or lead["id"] in (cand.get("no_batch_with") or []):
            continue
        group = [lead, *chosen, cand]
        if len(group) > cfg["plate_max_parts"] or plate_estimate_s(group) > cfg["plate_max_h"] * 3600:
            continue
        if not colours_ok(group) or not _pickup_ok(group, now):
            continue
        if not assign.evaluate({**printer, "assume_available": True}, tuple(cand["dims"]), cand["filament"]).eligible:
            continue
        if not footprints_fit([tuple(j["dims"]) for j in group], bed):
            continue
        chosen.append(cand)
    return chosen


def hold_reason(job: dict[str, Any], now: float) -> str | None:
    """Why an opted-in job with no partner yet should keep waiting for one (None = go ahead alone)."""
    if not job.get("allow_batching"):
        return None
    cfg = pipelinecfg.get()
    waited = now - (job.get("submitted_at") or now)
    if waited >= cfg["max_hold_h"] * 3600:
        return None
    if job.get("pickup_by"):
        f = forecast.predict(job, now)
        if not f["feasible"] or job["pickup_by"] - f["earliest_finish"] <= cfg["batch_slack_min"] * 60:
            return None
        return "Waiting for another part to share the plate (there is time to spare before your pickup time)"
    return f"Waiting for another part to share the plate (up to {cfg['max_hold_h']} h)"


# ------------------------------------------------------------------ forming and breaking plates

def form(lead: dict[str, Any], members: list[dict[str, Any]]) -> dict[str, Any]:
    """Record the plate on its jobs (shares by part volume) before the lead is assigned; the lead's move then carries the rest."""
    group = [lead, *members]
    _set_shares(group)
    for j in group:
        jobstate.event(j["id"], "batched", "allocator", lead=lead["id"], with_jobs=[g["id"] for g in group if g["id"] != j["id"]])
    return store.jobs.update(lead["id"], members=[m["id"] for m in members])


def _set_shares(group: list[dict[str, Any]]) -> None:
    vols = {j["id"]: volume_mm3(j) for j in group}
    total = sum(vols.values())
    for j in group:
        store.jobs.update(j["id"], plate_share=round(vols[j["id"]] / total, 4) if len(group) > 1 else None)


def after_transition(job: dict[str, Any], to: str, actor: str | None) -> None:
    """Called by jobstate after any job moves. A lead's move is copied to its members; leaving the plate early dissolves it."""
    if job.get("members"):
        if to in MIRRORED:
            _mirror(job, to, actor)
        elif to in ("pooled", "cancelled"):
            dissolve(job["id"], f"the plate's lead job went to {to}", exclude=job["id"] if to == "cancelled" else None)
    elif job.get("batch_lead") and to == "cancelled":
        dissolve(job["batch_lead"], "a job on the plate was cancelled", exclude=job["id"])


def _mirror(lead: dict[str, Any], to: str, actor: str | None) -> None:
    fields = {k: lead[k] for k in MIRROR_KEYS if k in lead}
    for mid in lead.get("members") or []:
        m = store.jobs.get(mid)
        if m is None or m.get("phase") in jobstate.TERMINAL and to not in ("printed", "failed", "unknown"):
            continue
        store.jobs.update(mid, batch_lead=lead["id"], pooled=True)
        extra = {"state": lead["state"]} if to in ("failed",) else {}
        jobstate.transition(mid, to, actor, force=True, **fields, **extra)


def dissolve(lead_id: str, reason: str, exclude: str | None = None) -> None:
    """Break up a plate that has not started printing: every job except `exclude` goes back to the pool on its own."""
    lead = store.jobs.get(lead_id)
    if lead is None:
        return
    ids = [lead_id, *(lead.get("members") or [])]
    store.jobs.update(lead_id, members=[])
    for i in ids:
        j = store.jobs.get(i)
        if j is None:
            continue
        store.jobs.update(i, batch_lead=None, members=[], plate_share=None)
        if i == exclude:
            continue
        if j.get("phase") in ("assigned", "slicing", "ready"):
            jobstate.transition(i, "pooled", "allocator", state="pooled", reason=reason, printer_id=None, printer_name=None)
            jobstate.event(i, "unbatched", "allocator", reason=reason)


def drop_last(lead_id: str, reason: str) -> bool:
    """The slicer could not fit the plate: return the last job added to the pool (never to share with these again)."""
    lead = store.jobs.get(lead_id)
    members = list((lead or {}).get("members") or [])
    if not members:
        return False
    gone = members.pop()
    others = [lead_id, *members]
    store.jobs.update(lead_id, members=members, no_batch_with=sorted(set((lead.get("no_batch_with") or []) + [gone])))
    store.jobs.update(gone, batch_lead=None, plate_share=None, no_batch_with=sorted(set(others)))
    _set_shares([j for j in (store.jobs.get(i) for i in others) if j])
    jobstate.transition(gone, "pooled", "allocator", state="pooled", reason=reason, printer_id=None, printer_name=None)
    jobstate.event(gone, "unbatched", "allocator", reason=reason)
    return True
