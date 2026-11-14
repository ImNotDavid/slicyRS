"""My Parts (2.17): every model a person has uploaded, kept (owner decision), with folders and search. "Use again" puts a part
back on the Workspace plate."""
import time
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field

from .. import meshio, permissions, store, thumbnail
from ..auth import audit
from ..storage import MODELS, files
from .common import User

router = APIRouter()
THUMB = "thumb.png"


def _mine(model_id: str, user: dict[str, Any]) -> dict[str, Any]:
    rec = store.models.get(model_id)
    if not rec or not permissions.can(user, "model.use", rec) or rec.get("deleted"):
        raise HTTPException(404, "Unknown part")
    return rec


def _uses(model_id: str) -> list[dict[str, Any]]:
    out = []
    for j in store.jobs.all():
        spec = j.get("spec") or {}
        ids = {spec.get("model_id"), j.get("model_id"), *[p.get("model_id") for p in spec.get("parts") or []]}
        if model_id in ids and not j.get("draft"):
            out.append(j)
    return out


def _public(m: dict[str, Any], uses: list[dict[str, Any]]) -> dict[str, Any]:
    last = max(uses, key=lambda j: j.get("created") or 0) if uses else None
    return {"id": m["id"], "name": m.get("name") or "part", "created": m.get("created"), "last_used": m.get("last_used"),
            "dims": m.get("dims"), "folder_id": m.get("folder_id"), "available": not m.get("files_deleted_at") and files.exists(MODELS, m["id"]),
            "size": files.size(MODELS, m["id"]) if files.exists(MODELS, m["id"]) else 0, "jobs": len(uses),
            "printed": sum(1 for j in uses if j.get("phase") == "printed"),
            "last_job": {"id": last["id"], "name": last["name"], "phase": last.get("phase"), "filament": last.get("filament")} if last else None}


@router.get("/api/parts")
def list_parts(q: str = "", folder: str | None = None, user: dict = User):
    """The person's own parts. `folder` is a folder id, "none" for parts in no folder, or empty for all."""
    t = q.strip().lower()
    jobs_by_model: dict[str, list] = {}
    for j in store.jobs.all():
        if j.get("draft") or j.get("owner_id") != user["id"]:
            continue
        spec = j.get("spec") or {}
        for mid in {spec.get("model_id"), j.get("model_id"), *[p.get("model_id") for p in spec.get("parts") or []]} - {None}:
            jobs_by_model.setdefault(mid, []).append(j)
    out = []
    for m in store.models.all():
        if m.get("owner_id") != user["id"] or m.get("deleted"):
            continue
        if folder == "none" and m.get("folder_id"):
            continue
        if folder and folder != "none" and m.get("folder_id") != folder:
            continue
        if t and t not in (m.get("name") or "").lower():
            continue
        out.append(_public(m, jobs_by_model.get(m["id"], [])))
    folders = [f for f in store.folders.all() if f["owner_id"] == user["id"]]
    return {"parts": sorted(out, key=lambda p: -(p["last_used"] or p["created"] or 0)),
            "folders": sorted(({"id": f["id"], "name": f["name"], "count": sum(1 for m in store.models.all()
                                if m.get("folder_id") == f["id"] and not m.get("deleted"))} for f in folders), key=lambda f: f["name"].lower())}


class FolderIn(BaseModel):
    name: str = Field(min_length=1, max_length=60)


@router.post("/api/parts/folders")
def add_folder(body: FolderIn, user: dict = User):
    name = body.name.strip()
    if any(f["owner_id"] == user["id"] and f["name"].lower() == name.lower() for f in store.folders.all()):
        raise HTTPException(409, "You already have a folder with that name")
    fid = uuid.uuid4().hex[:8]
    return store.folders.put(fid, {"id": fid, "owner_id": user["id"], "name": name, "created": time.time()})


def _folder(fid: str, user: dict[str, Any]) -> dict[str, Any]:
    f = store.folders.get(fid)
    if not f or f["owner_id"] != user["id"]:
        raise HTTPException(404, "Unknown folder")
    return f


@router.patch("/api/parts/folders/{fid}")
def rename_folder(fid: str, body: FolderIn, user: dict = User):
    _folder(fid, user)
    return store.folders.update(fid, name=body.name.strip())


@router.delete("/api/parts/folders/{fid}")
def delete_folder(fid: str, user: dict = User):
    """Removes the folder only; its parts move to no folder."""
    _folder(fid, user)
    for m in store.models.all():
        if m.get("folder_id") == fid:
            store.models.update(m["id"], folder_id=None)
    store.folders.delete(fid)
    return {"ok": True}


class PartPatch(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=120)
    folder_id: str | None = None
    clear_folder: bool = False


@router.patch("/api/parts/{model_id}")
def update_part(model_id: str, body: PartPatch, user: dict = User):
    _mine(model_id, user)
    fields: dict[str, Any] = {}
    if body.name:
        fields["name"] = body.name.strip()
    if body.clear_folder:
        fields["folder_id"] = None
    elif body.folder_id:
        _folder(body.folder_id, user)
        fields["folder_id"] = body.folder_id
    rec = store.models.update(model_id, **fields) if fields else store.models.get(model_id)
    return _public(rec, _uses(model_id))  # type: ignore[arg-type]


@router.delete("/api/parts/{model_id}")
def delete_part(model_id: str, user: dict = User):
    """Delete the part's files for good. Jobs made from it keep their own copies until their files expire."""
    rec = _mine(model_id, user)
    if files.exists(MODELS, model_id):
        files.remove(MODELS, model_id)
    store.models.update(model_id, deleted=True, files_deleted_at=time.time())
    audit(user, "part_delete", rec.get("name") or model_id)
    return {"ok": True}


@router.get("/api/parts/{model_id}/thumbnail.png")
def part_thumbnail(model_id: str, user: dict = User):
    _mine(model_id, user)
    if not files.exists(MODELS, model_id):
        raise HTTPException(404, "The file was removed")
    path = files.path(MODELS, model_id, THUMB)
    if not path.exists():
        try:
            mesh = meshio.load_mesh(files.path(MODELS, model_id, "preview.stl"))
            files.write_bytes(MODELS, model_id, THUMB, thumbnail.render(mesh, size=256, colour=(0, 30, 255)))
        except Exception:
            raise HTTPException(404, "No picture for this part")
    return Response(path.read_bytes(), media_type="image/png", headers={"Cache-Control": "private, max-age=86400"})
