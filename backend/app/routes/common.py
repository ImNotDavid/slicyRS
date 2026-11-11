"""Helpers shared by the route modules."""
from concurrent.futures import ThreadPoolExecutor

from pathlib import Path
from typing import Any

from fastapi import HTTPException

from .. import assign, bedstate, meshio, retention, store
from ..auth import is_admin
from ..catalog import DEFAULT_BED_TYPE
from ..storage import MODELS, files
from fastapi import Depends
from ..auth import current_user
from ..config import MAX_WORKERS

pool = ThreadPoolExecutor(max_workers=MAX_WORKERS)
User = Depends(current_user)



def _public_printer(p: dict[str, Any], admin: bool) -> dict[str, Any]:
    """Everyone sees status; connection details are admin-only and the access code is never returned."""
    out = {"id": p["id"], "name": p["name"], "model": p["model"], "status": p.get("status", {}),
           "hardened_nozzle": p.get("hardened_nozzle", False), "enabled": p.get("enabled", True),
           "bed_type": p.get("bed_type") or DEFAULT_BED_TYPE, "bed": bedstate.get(p), "nozzle_mm": assign.nozzle_of(p),
           "queue_depth": _queue_depth(p["id"]), "live": bool(p.get("host"))}
    if admin:
        out.update(host=p.get("host"), port=p.get("port") or 8883, serial=p.get("serial"),
                   has_access_code=bool(p.get("access_code")), demo=p.get("demo", False), gateway=p.get("gateway"))
    return out


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
