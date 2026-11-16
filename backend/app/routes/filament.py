"""Filament inventory pages (2.10): operators and admins."""
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .. import filament, permissions, store
from ..auth import audit
from .common import User

router = APIRouter()


def _staff(user: dict[str, Any]) -> None:
    permissions.require(user, "filament.manage", message="Only operators and admins manage filament")


class SpoolIn(BaseModel):
    material: str
    colour_name: str = Field("", max_length=40)
    colour_hex: str = Field("#888888", pattern="^#[0-9a-fA-F]{6}$")
    brand: str = Field("", max_length=60)
    start_g: float = Field(1000, gt=0)
    remaining_g: float | None = Field(None, ge=0)
    price: float | None = Field(None, ge=0)        # what the spool cost (any currency; shown as entered)
    notes: str = Field("", max_length=200)
    printer_id: str | None = None                   # loaded on this printer


def _check_printer(pid: str | None) -> None:
    if pid and not store.printers.get(pid):
        raise HTTPException(400, "Unknown printer")


@router.get("/api/filament")
def list_spools(user: dict = User):
    _staff(user)
    spools = sorted(store.spools.all(), key=lambda s: (float(s.get("remaining_g") or 0) <= 0, s.get("material"), -(s.get("updated") or 0)))
    return {"spools": [filament.public(s) for s in spools], "totals": filament.totals()}


@router.get("/api/filament/loaded")
def loaded_now(user: dict = User):
    """Materials and colours loaded in the pool's printers right now (everyone: the Workspace offers only these)."""
    return filament.loaded(user)


@router.post("/api/filament/spools")
def add_spool(body: SpoolIn, user: dict = User):
    _staff(user)
    _check_printer(body.printer_id)
    try:
        s = filament.add(body.model_dump(), user)
    except ValueError as e:
        raise HTTPException(400, str(e))
    audit(user, "spool_add", f"{s['material']} {s.get('colour_name') or ''} {s['start_g']:.0f} g")
    return filament.public(s)


@router.put("/api/filament/spools/{sid}")
def update_spool(sid: str, body: SpoolIn, user: dict = User):
    _staff(user)
    if not store.spools.get(sid):
        raise HTTPException(404, "Unknown spool")
    _check_printer(body.printer_id)
    fields = body.model_dump()
    if fields["remaining_g"] is None:
        fields.pop("remaining_g")
    try:
        filament.check(fields["material"], fields["start_g"], fields.get("remaining_g"))
    except ValueError as e:
        raise HTTPException(400, str(e))
    import time
    s = store.spools.update(sid, **fields, updated=time.time())
    return filament.public(s)  # type: ignore[arg-type]


@router.delete("/api/filament/spools/{sid}")
def delete_spool(sid: str, user: dict = User):
    _staff(user)
    s = store.spools.get(sid)
    if not s:
        raise HTTPException(404, "Unknown spool")
    store.spools.delete(sid)
    audit(user, "spool_delete", f"{s['material']} {s.get('colour_name') or ''}")
    return {"ok": True}
