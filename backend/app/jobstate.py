"""Job state machine and event log (A.1).

Every job has a `phase`. Changes go through `transition()`, which checks the move is allowed, updates the job and appends
an immutable event, so the history of a job (and later all statistics) comes from one place.

  submitted -> previewing -> previewed -> [awaiting_approval ->] pooled -> assigned -> slicing -> ready -> printing -> printed | failed | unknown
  any phase before `printing` can also go to `cancelled`; a preview or slice problem goes to `failed` (or `rejected`).
  `assigned`, `slicing` and `ready` can fall back to `pooled` (printer lost, or the hold on a sliced job expired).

The older `state` field (slicing / done / failed) is kept in step because the web app and file downloads still use it.
"""
import time
import uuid
from typing import Any

from . import store
from .storage import JOBS, files

PHASES = {"submitted", "previewing", "previewed", "awaiting_approval", "pooled", "assigned", "slicing", "ready", "printing",
          "printed", "failed", "unknown", "cancelled", "rejected"}
TERMINAL = {"printed", "failed", "unknown", "cancelled", "rejected"}
ALLOWED: dict[str, set[str]] = {
    "submitted": {"previewing", "failed", "rejected", "cancelled"},
    "previewing": {"previewed", "failed", "cancelled"},
    "previewed": {"pooled", "awaiting_approval", "cancelled", "rejected"},
    "awaiting_approval": {"pooled", "rejected", "cancelled"},          # 3.3: a group operator approves or rejects
    "pooled": {"assigned", "cancelled"},
    "assigned": {"slicing", "pooled", "cancelled"},
    "slicing": {"ready", "failed", "pooled", "cancelled"},
    "ready": {"printing", "pooled", "cancelled"},
    "printing": {"printed", "failed", "unknown"},
}
# what the older `state` field shows for each phase (failed is decided by the caller: a slice failure and a failed print differ)
LEGACY_STATE = {"slicing": "slicing", "ready": "done", "printing": "done", "printed": "done", "unknown": "done",
                "cancelled": "cancelled", "rejected": "failed"}


class InvalidTransition(ValueError):
    pass


def event(job_id: str, kind: str, actor: str | None = None, **data: Any) -> dict[str, Any]:
    ev = {"id": uuid.uuid4().hex[:12], "job_id": job_id, "kind": kind, "at": time.time(), "actor": actor, "data": data}
    store.events.put(ev["id"], ev)
    return ev


def events_for(job_id: str) -> list[dict[str, Any]]:
    return [e for e in store.events.all() if e["job_id"] == job_id]


def transition(job_id: str, to: str, actor: str | None = None, *, force: bool = False, **fields: Any) -> dict[str, Any] | None:
    """Move a job to `to`, record the event, and store any extra fields. A job with no phase yet (created before this
    existed) may move anywhere. `force` is for the few legacy paths that repeat a step (printing again)."""
    if to not in PHASES:
        raise InvalidTransition(f"Unknown phase {to}")
    job = store.jobs.get(job_id)
    if job is None:
        return None
    frm = job.get("phase")
    if frm == to and not force:
        return store.jobs.update(job_id, **fields) if fields else job
    if frm is not None and not force and to not in ALLOWED.get(frm, set()):
        raise InvalidTransition(f"A job cannot go from {frm} to {to}")
    extra = dict(fields)
    reason = extra.pop("reason", None)      # explains the move in the event; not a property of the job
    if "state" not in extra:
        extra["state"] = LEGACY_STATE.get(to, to)
    updated = store.jobs.update(job_id, phase=to, phase_at=time.time(), **extra)
    data = {"from": frm, "to": to, "reason": reason, **{k: v for k, v in fields.items() if k in ("printer_id", "error")}}
    event(job_id, "phase", actor, **data)
    from . import plates   # late import: plates uses this module
    plates.after_transition(updated, to, actor)
    return store.jobs.get(job_id)


def discard(job_id: str) -> None:
    """Remove a draft completely: its record, its history and its files. Drafts are not jobs and leave no trace."""
    for e in events_for(job_id):
        store.events.delete(e["id"])
    store.jobs.delete(job_id)
    files.remove(JOBS, job_id)
