"""Uploaded models: upload, orientation, preview and printer assignment."""
import time
import uuid
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .. import assign, meshcheck, meshio, orient, retention, store
from ..catalog import DEFAULT_NOZZLE, FILAMENTS
from ..config import MAX_UPLOAD_MB
from ..storage import MODELS, files
from .common import User, _fleet, _source_file, _transformed, _checked_model

router = APIRouter()


@router.post("/api/models")
async def upload_model(file: UploadFile = File(...), user: dict = User):
    ext = Path(file.filename or "").suffix.lower()
    if ext not in meshio.SUPPORTED:
        raise HTTPException(400, f"Unsupported file type {ext!r}; use STL, 3MF, OBJ or STEP")
    if not retention.has_room_for_upload():
        raise HTTPException(507, "The server is almost out of disk space. Please tell an administrator.")
    data = await file.read()
    if len(data) > MAX_UPLOAD_MB * 1024 * 1024:
        raise HTTPException(413, f"File larger than {MAX_UPLOAD_MB} MB")
    model_id = uuid.uuid4().hex[:12]
    files.create(MODELS, model_id)
    files.write_bytes(MODELS, model_id, f"source{ext}", data)
    files.write_text(MODELS, model_id, "name.txt", file.filename or "model")
    try:
        mesh = meshio.load_mesh(files.path(MODELS, model_id, f"source{ext}"))
    except Exception as e:
        files.remove(MODELS, model_id)
        raise HTTPException(400, f"Could not read model: {e}")
    meshio.export_stl(mesh, files.path(MODELS, model_id, "preview.stl"))
    files.publish(MODELS, model_id)
    store.models.put(model_id, {"id": model_id, "owner_id": user["id"], "name": file.filename, "created": time.time()})
    return {"id": model_id, "name": file.filename, "dims": meshio.dims(mesh),
            "triangles": int(len(mesh.faces)), "checks": meshcheck.report(mesh)}


class OrientIn(BaseModel):
    mode: str = Field("auto", pattern="^(flat|auto|face)$")
    normal: list[float] | None = Field(None, min_length=3, max_length=3)   # mode "face": outward normal of the face to lay on the bed


@router.post("/api/models/{model_id}/orient")
def orient_model(model_id: str, body: OrientIn, user: dict = User):
    """Rotation (degrees) that lays the part on its largest flat face ("flat"), on the face the user picked ("face"), or needs the least
    support ("auto")."""
    mesh = meshio.load_mesh(_source_file(model_id, user))
    if body.mode == "face":
        try:
            return orient.face_down(mesh, body.normal or [])
        except ValueError as e:
            raise HTTPException(422, str(e))
    return orient.suggest(mesh, body.mode)


@router.get("/api/models/{model_id}/preview.stl")
def preview(model_id: str, user: dict = User):
    return FileResponse(files.path(MODELS, _checked_model(model_id, user), "preview.stl"), media_type="model/stl")


class TransformIn(BaseModel):
    rotation: tuple[float, float, float] = (0, 0, 0)
    scale: float = Field(1.0, gt=0, le=100)


class AssignIn(TransformIn):
    model_id: str
    filament: str
    nozzle_mm: float = Field(DEFAULT_NOZZLE, gt=0, le=2)     # the nozzle size the job will need (its process preset decides)


@router.post("/api/assign")
def assign_printer(body: AssignIn, user: dict = User):
    if body.filament not in FILAMENTS:
        raise HTTPException(400, "Unknown filament")
    size = meshio.dims(_transformed(body.model_id, body.rotation, body.scale, user))
    ranked = assign.rank(_fleet(), size, body.filament, body.nozzle_mm)
    best = ranked[0] if ranked and ranked[0].eligible else None
    return {"dims": size, "chosen": best.printer_id if best else None,
            "candidates": [c.asdict() for c in ranked]}
