"""Printers (fleet status, admin set-up, connection test) and the build-plate state of each."""
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .. import allocator, bedstate, crypto, gateways, permissions, store
from ..adapter import AdapterError
from ..auth import audit, is_admin, require_admin
from ..bambu import check_host, links, probe
from ..catalog import BED_TYPES, DEFAULT_BED_TYPE, NOZZLES, PRINTER_MODELS
from .common import User, _public_printer

router = APIRouter()


@router.get("/api/printers")
def list_printers(user: dict = User):
    """Fleet status for every signed-in user (connection details are admin-only)."""
    people, connection = permissions.can(user, "printer.see_people"), permissions.can(user, "printer.see_connection")
    return [_public_printer(p, connection, people) for p in store.printers.all()]


class PrinterIn(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    model: str
    host: str | None = None
    port: int = Field(8883, ge=1, le=65535)
    serial: str | None = None
    access_code: str | None = None
    hardened_nozzle: bool = False
    enabled: bool = True
    bed_type: str = DEFAULT_BED_TYPE  # the build plate fitted to this printer (admin-managed)
    nozzle_mm: float = 0.4            # nozzle fitted (admin-set; a connected printer reports its own, which wins)
    gateway: str | None = None        # id of the remote gateway that reaches this printer; empty = this server connects directly
    status: dict[str, Any] | None = None


def _validate_printer(body: PrinterIn) -> None:
    if body.model not in PRINTER_MODELS:
        raise HTTPException(400, "Unknown printer model")
    if round(body.nozzle_mm, 2) not in NOZZLES:
        raise HTTPException(400, f"Unknown nozzle size. Choose one of: {', '.join(f'{n:g}' for n in NOZZLES)} mm")
    if body.bed_type not in BED_TYPES:
        raise HTTPException(400, f"Unknown build plate. Choose one of: {', '.join(BED_TYPES)}")
    body.gateway = body.gateway or None
    if body.gateway and not store.gateways.get(body.gateway):
        raise HTTPException(400, "Unknown gateway")
    if body.host:
        try:
            check_host(body.host, resolve=not body.gateway)   # a host name behind a gateway only resolves on the gateway's network
        except ValueError as e:
            raise HTTPException(400, str(e))


@router.post("/api/admin/printers")
def add_printer(body: PrinterIn, admin: dict = Depends(require_admin)):
    _validate_printer(body)
    pid = uuid.uuid4().hex[:8]
    live = bool(body.host and body.serial and body.access_code)
    rec = {**body.model_dump(exclude={"status", "access_code"}), "id": pid,
           "access_code": crypto.seal(body.access_code),
           "status": body.status or ({"state": "OFFLINE"} if live else {"state": "IDLE", "ams": []})}
    store.printers.put(pid, rec)
    audit(admin, "printer_add", f"{body.name} ({body.model})")
    links.sync()
    return _public_printer(rec, True)


@router.put("/api/admin/printers/{printer_id}")
def update_printer(printer_id: str, body: PrinterIn, admin: dict = Depends(require_admin)):
    cur = store.printers.get(printer_id)
    if not cur:
        raise HTTPException(404, "Unknown printer")
    _validate_printer(body)
    fields = body.model_dump(exclude={"status", "access_code"})
    # a blank access code keeps the stored one
    fields["access_code"] = crypto.seal(body.access_code) if body.access_code else cur.get("access_code")
    if body.status is not None and not body.host:
        fields["status"] = body.status
    rec = store.printers.update(printer_id, **fields)
    audit(admin, "printer_update", f"{body.name} ({body.model})")
    links.sync()
    return _public_printer(rec, True)  # type: ignore[arg-type]


@router.delete("/api/admin/printers/{printer_id}")
def delete_printer(printer_id: str, admin: dict = Depends(require_admin)):
    cur = store.printers.get(printer_id)
    if not cur or not store.printers.delete(printer_id):
        raise HTTPException(404, "Unknown printer")
    audit(admin, "printer_delete", cur["name"])
    links.sync()
    return {"ok": True}


class ProbeIn(BaseModel):
    host: str
    port: int = Field(8883, ge=1, le=65535)
    serial: str = Field(min_length=1)
    access_code: str | None = None
    printer_id: str | None = None  # use the stored access code of this printer when blank
    gateway: str | None = None     # test through this gateway instead of from the server


@router.post("/api/admin/printers/test")
def test_printer(body: ProbeIn, admin: dict = Depends(require_admin)):
    code = body.access_code
    if not code and body.printer_id:
        code = crypto.unseal((store.printers.get(body.printer_id) or {}).get("access_code"))
    if not code:
        raise HTTPException(400, "An access code is required")
    try:
        if body.gateway:
            if not store.gateways.get(body.gateway):
                raise HTTPException(400, "Unknown gateway")
            check_host(body.host, resolve=False)
            result = gateways.probe(body.gateway, body.host, body.port, body.serial, code)
        else:
            result = probe(body.host, body.port, body.serial, code)
    except (ValueError, AdapterError) as e:
        raise HTTPException(400 if isinstance(e, ValueError) else 502, str(e))
    audit(admin, "printer_test", f"{body.host}: {'ok' if result['ok'] else result['error']}")
    return result



class BedClearIn(BaseModel):
    bed_empty: bool = False
    plate_matches: bool = False
    filament_matches: bool = False


def _printer_or_404(printer_id: str) -> dict[str, Any]:
    p = store.printers.get(printer_id)
    if not p:
        raise HTTPException(404, "Unknown printer")
    return p


@router.post("/api/printers/{printer_id}/bed/clear")
def bed_clear(printer_id: str, body: BedClearIn, user: dict = User):
    _printer_or_404(printer_id)
    try:
        bed = bedstate.confirm_clear(printer_id, user, body.model_dump())
    except PermissionError as e:
        raise HTTPException(403, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))
    allocator.tick()
    return bed


@router.post("/api/printers/{printer_id}/bed/occupied")
def bed_occupied(printer_id: str, user: dict = User):
    _printer_or_404(printer_id)
    try:
        return bedstate.mark_occupied(printer_id, user)
    except PermissionError as e:
        raise HTTPException(403, str(e))
