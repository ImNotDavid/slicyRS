"""Approval workflow (3.3): the list of prints waiting for approval, and approve or reject."""
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .. import approvals, groups as G, permissions, store
from ..auth import audit
from .common import User

router = APIRouter()


def _card(j: dict[str, Any]) -> dict[str, Any]:
    owner = store.users.get(j.get("owner_id") or "") or {}
    est = j.get("preview") or j.get("stats") or {}
    g = store.groups.get(G.job_group(j) or "") or {}
    return {"id": j["id"], "name": j["name"], "owner": owner.get("name") or owner.get("email"), "owner_email": owner.get("email"),
            "group": g.get("name"), "group_id": g.get("id"), "filament": j.get("filament"), "nozzle_mm": j.get("nozzle_mm"),
            "time_s": est.get("time_s"), "filament_g": est.get("filament_g"), "submitted_at": j.get("submitted_at"),
            "pickup_by": j.get("pickup_by"), "reasons": (j.get("approval") or {}).get("reasons") or []}


@router.get("/api/approvals")
def waiting(user: dict = User):
    if not G.any_right(user, "approve"):
        raise HTTPException(403, "Only people who can approve prints see this")
    return [_card(j) for j in approvals.waiting_for(user)]


class DecideIn(BaseModel):
    note: str = Field("", max_length=300)


def _decide(job_id: str, approve: bool, body: DecideIn, user: dict[str, Any]) -> dict[str, Any]:
    job = store.jobs.get(job_id)
    permissions.require(user, "job.approve", job, hide=not job, message="Only the group's operators can approve its prints")
    if not approve and not body.note.strip():
        raise HTTPException(400, "Say why, so the owner knows what to change")
    try:
        out = approvals.decide(job, approve, user, body.note)
    except ValueError as e:
        raise HTTPException(409, str(e))
    audit(user, "job_approve" if approve else "job_reject", job["name"] + (f": {body.note.strip()}" if body.note.strip() else ""))
    return out


@router.post("/api/jobs/{job_id}/approve")
def approve(job_id: str, body: DecideIn | None = None, user: dict = User):
    return _decide(job_id, True, body or DecideIn(), user)


@router.post("/api/jobs/{job_id}/reject")
def reject(job_id: str, body: DecideIn, user: dict = User):
    return _decide(job_id, False, body, user)
