"""The Home page (2.18): what a person needs at a glance when they sign in. Owner decisions: everyone lands here; members see
their own jobs, how busy the pool is and notices from admins; operators and admins also see fleet numbers."""
import time
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .. import bedstate, forecast, permissions, store, upkeep
from ..auth import audit, require_admin
from .common import User, live_progress
from .jobs import _with_plate_flag

router = APIRouter()
ACTIVE = ("submitted", "previewing", "previewed", "pooled", "assigned", "slicing", "ready", "printing")
TYPICAL = {"id": "_typical", "dims": [60, 60, 30], "filament": "PLA", "preset": "0.20mm Standard", "stats": {"time_s": 3600},
           "spec": {"preset": "0.20mm Standard"}}     # "a typical part": one hour of PLA on a 0.4 mm nozzle


def _mine(user: dict[str, Any]) -> dict[str, Any]:
    jobs = [_with_plate_flag(j) for j in store.jobs.all() if j.get("owner_id") == user["id"] and not j.get("draft")]
    printing = [{"id": j["id"], "name": j["name"], "printer_name": j.get("printer_name"), "live": live_progress(j)}
                for j in jobs if j.get("phase") == "printing"]
    waiting = [{"id": j["id"], "name": j["name"], "waiting_for": j.get("waiting_for"), "phase": j.get("phase")}
               for j in jobs if j.get("phase") in ACTIVE and j.get("phase") != "printing"]
    collect = [{"id": j["id"], "name": j["name"], "printer_name": j.get("printer_name")} for j in jobs if j.get("plate_to_clear")]
    recent = sorted((j for j in jobs if j.get("phase") in ("printed", "failed", "unknown") and not j.get("plate_to_clear")),
                    key=lambda j: -(j.get("phase_at") or j.get("created") or 0))[:4]
    return {"printing": printing, "waiting": waiting, "to_collect": collect,
            "recent": [{"id": j["id"], "name": j["name"], "phase": j.get("phase")} for j in recent]}


def _pool() -> dict[str, Any]:
    printers = [p for p in store.printers.all() if p.get("enabled", True)]
    from .. import allocator
    free = len([p for p in allocator.free_printers() if p.get("enabled", True)])   # idle, plate confirmed clear, in service
    waiting = sum(1 for j in store.jobs.all() if j.get("phase") == "pooled")
    f = forecast.predict(TYPICAL)
    # the wait is until a printer could start it: the estimate's safety buffer and cooling margin belong to the print, not the wait
    typical_wait = 0.0 if free and not waiting else (max(0.0, f["earliest_start"] - time.time()) if f.get("feasible") else None)
    return {"jobs_waiting": waiting, "printers": len(printers), "printers_free": free, "typical_wait_s": typical_wait,
            "printers_out": sum(1 for p in printers if upkeep.unavailable(p))}


def _fleet() -> dict[str, Any]:
    printers = [p for p in store.printers.all() if p.get("enabled", True)]
    states: dict[str, int] = {}
    for p in printers:
        st = (p.get("status") or {}).get("state") or "UNKNOWN"
        states[st] = states.get(st, 0) + 1
    return {"by_state": states,
            "needs_attention": [{"id": p["id"], "name": p["name"], "why": upkeep.unavailable(p)} for p in printers if p.get("attention")],
            "maintenance": [{"id": p["id"], "name": p["name"], "note": (p.get("maintenance") or {}).get("note")} for p in printers
                            if (p.get("maintenance") or {}).get("on")],
            "plates_to_clear": [{"id": p["id"], "name": p["name"]} for p in printers if bedstate.get(p).get("state") == "occupied"
                                and (p.get("status") or {}).get("state") not in bedstate.BUSY],
            "at_risk": sum(1 for j in store.jobs.all() if j.get("at_risk") and j.get("phase") in ACTIVE)}


def _notices() -> list[dict[str, Any]]:
    now = time.time()
    live = [n for n in store.notices.all() if not n.get("until") or n["until"] > now]
    return sorted(live, key=lambda n: -n["created"])


@router.get("/api/home")
def home(user: dict = User):
    out = {"mine": _mine(user), "pool": _pool(), "notices": _notices()}
    if permissions.is_operator(user):
        from .. import failures
        out["fleet"] = {**_fleet(), "flags": failures.patterns()["flags"][:5]}     # 2.13: staff only
    return out


class NoticeIn(BaseModel):
    text: str = Field(min_length=1, max_length=500)
    level: str = Field("info", pattern="^(info|warning)$")
    until: float | None = None          # hidden after this time (epoch seconds); none = until removed


@router.post("/api/admin/notices")
def add_notice(body: NoticeIn, admin: dict = Depends(require_admin)):
    nid = uuid.uuid4().hex[:8]
    n = store.notices.put(nid, {"id": nid, "text": body.text.strip(), "level": body.level, "until": body.until,
                                "created": time.time(), "by": admin["email"]})
    audit(admin, "notice_add", body.text.strip()[:80])
    return n


@router.delete("/api/admin/notices/{nid}")
def delete_notice(nid: str, admin: dict = Depends(require_admin)):
    n = store.notices.get(nid)
    if not n:
        raise HTTPException(404, "Unknown notice")
    store.notices.delete(nid)
    audit(admin, "notice_delete", n["text"][:80])
    return {"ok": True}
