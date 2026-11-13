"""The Queue page (2.2): operators and admins see every printer's line and the pool, and rearrange them."""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .. import allocator, permissions, queue
from .common import User

router = APIRouter()


@router.get("/api/queue")
def get_queue(user: dict = User):
    permissions.require(user, "queue.manage", message="Only operators and admins can see the queue")
    return queue.board()


class MoveIn(BaseModel):
    job_id: str
    lane: str                       # "pool" or a printer id
    index: int = Field(0, ge=0)     # position in that lane, from the top


@router.post("/api/queue/move")
def move_job(body: MoveIn, user: dict = User):
    permissions.require(user, "queue.manage", message="Only operators and admins can change the queue")
    try:
        out = queue.move(body.job_id, body.lane, body.index, user)
    except ValueError as e:
        raise HTTPException(409, str(e))
    allocator.tick()                # a job lined up for a free printer can go straight away
    return out


@router.post("/api/queue/reset")
def reset_queue(user: dict = User):
    permissions.require(user, "queue.manage", message="Only operators and admins can change the queue")
    n = queue.reset(user)
    allocator.tick()
    return {"ok": True, "reset": n}
