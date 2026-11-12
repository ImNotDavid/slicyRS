"""Helpers shared by the route modules."""
from concurrent.futures import ThreadPoolExecutor

from pathlib import Path
from typing import Any

from fastapi import HTTPException

from .. import assign, bedstate, meshio, multipart, retention, store
from ..auth import find_user_by_email, is_admin
from ..catalog import DEFAULT_BED_TYPE
from ..storage import MODELS, files
from fastapi import Depends
from ..auth import current_user
from ..config import MAX_WORKERS

pool = ThreadPoolExecutor(max_workers=MAX_WORKERS)
User = Depends(current_user)



def _public_printer(p: dict[str, Any], admin: bool, people: bool = False) -> dict[str, Any]:
    """Everyone sees status; connection details are admin-only and the access code is never returned."""
    out = {"id": p["id"], "name": p["name"], "model": p["model"], "status": p.get("status", {}),
           "hardened_nozzle": p.get("hardened_nozzle", False), "enabled": p.get("enabled", True),
           "bed_type": p.get("bed_type") or DEFAULT_BED_TYPE, "bed": bedstate.get(p), "nozzle_mm": assign.nozzle_of(p),
           "queue_depth": _queue_depth(p["id"]), "live": bool(p.get("host"))}
    if people or admin:
        out.update(printer_people(p))
    if admin:
        out.update(host=p.get("host"), port=p.get("port") or 8883, serial=p.get("serial"),
                   has_access_code=bool(p.get("access_code")), demo=p.get("demo", False), gateway=p.get("gateway"))
    return out


ACTIVE_PHASES = {"assigned", "slicing", "ready", "printing"}


def printer_people(printer: dict[str, Any]) -> dict[str, Any]:
    """Who is using a printer, for operators and admins only: the owners of the jobs on it now (a shared plate
    can have several), and whoever used it last. A finished job counts as in use until the plate is cleared."""
    jobs = [j for j in store.jobs.all() if j.get("printer_id") == printer["id"] and not j.get("draft")]
    bed = bedstate.get(printer) or {}
    now_phases = ACTIVE_PHASES | ({"printed", "failed", "unknown"} if bed.get("state") == "occupied" else set())
    current = [j for j in jobs if j.get("phase") in now_phases]
    finished = sorted((j for j in jobs if j.get("phase") in {"printed", "failed", "unknown"}), key=lambda j: j.get("phase_at") or j.get("created") or 0)

    def who(j: dict[str, Any]) -> dict[str, Any]:
        u = find_user_by_email(j.get("owner_email") or "")
        return {"email": j.get("owner_email"), "name": (u or {}).get("name") or j.get("owner_email"),
                "job_id": j["id"], "job_name": j.get("name"), "phase": j.get("phase")}
    seen: set[str] = set()
    users = []
    for j in current:
        if j.get("owner_email") not in seen:
            seen.add(j.get("owner_email"))
            users.append(who(j))
    last = who(finished[-1]) if finished and not users else None
    return {"users": users, "last_user": last}


def _queue_depth(printer_id: str) -> int:
    return sum(1 for j in store.jobs.all()
               if j.get("printer_id") == printer_id and j.get("queued") and not j.get("printed"))


def _fleet() -> list[dict[str, Any]]:
    return [{**p, "queue_depth": _queue_depth(p["id"])} for p in store.printers.all() if p.get("enabled", True)]


def _checked_model(model_id: str, user: dict[str, Any]) -> str:
    """Models belong to their uploader; other users get the same 404 as a missing model."""
    rec = store.models.get(model_id)
    if rec and rec.get("files_deleted_at") and (rec["owner_id"] == user["id"] or is_admin(user)):
        raise HTTPException(410, "This model was removed after not being used for a while. Please upload it again.")
    if not rec or not files.exists(MODELS, model_id) or (rec["owner_id"] != user["id"] and not is_admin(user)):
        raise HTTPException(404, "Unknown model")
    retention.touch_model(model_id)
    return model_id


def _source_file(model_id: str, user: dict[str, Any]) -> Path:
    _checked_model(model_id, user)
    return files.path(MODELS, model_id, next(n for n in files.names(MODELS, model_id) if n.startswith("source")))


def _transformed(model_id: str, rotation, scale, user: dict[str, Any]):
    return meshio.apply_transform(meshio.load_mesh(_source_file(model_id, user)), rotation, scale)


def plate_instances(parts, user: dict[str, Any], check: bool = True):
    """The parts of a plate as meshes with outlines, after checking the person may use each model and (unless `check` is off, for a request
    that asks the slicer to lay the parts out) that the layout makes sense."""
    try:
        inst = multipart.build([p.model_dump() for p in parts], lambda mid, rot, sc: _transformed(mid, rot, sc, user),
                               lambda mid: files.read_text(MODELS, mid, "name.txt"))
        if check:
            multipart.check_layout(inst)
    except multipart.PlateError as e:
        raise HTTPException(400, str(e))
    return inst
