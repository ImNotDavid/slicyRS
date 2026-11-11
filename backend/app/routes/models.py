"""Uploaded models: upload, orientation, preview and printer assignment."""
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .. import assign, meshcheck, meshio, multipart, orient, retention, slicer, store
from ..catalog import DEFAULT_NOZZLE, FILAMENTS, PRINTER_MODELS, PROCESS_PRESETS
from ..config import MAX_UPLOAD_MB
from ..storage import MODELS, files
from .common import User, _fleet, _source_file, _transformed, _checked_model, plate_instances

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


class PartIn(BaseModel):
    """One part of a plate: which uploaded model, how it is turned and scaled, and where its bounding-box centre goes (mm from the middle of the bed)."""
    model_id: str
    rotation: tuple[float, float, float] = (0, 0, 0)
    scale: float = Field(1.0, gt=0, le=100)
    position: tuple[float, float] = (0.0, 0.0)


class TransformIn(BaseModel):
    rotation: tuple[float, float, float] = (0, 0, 0)
    scale: float = Field(1.0, gt=0, le=100)
    position: tuple[float, float] = Field((0.0, 0.0), description="where the part was placed by hand: mm from the middle of the bed; (0, 0) lets the slicer place it")
    parts: list[PartIn] | None = Field(None, max_length=multipart.MAX_PARTS, description="several parts laid out on one plate; leave out for a single part")


def plate_of(body: Any) -> list[PartIn] | None:
    """The parts of a several-part plate, or None for a single part (then `model_id` and the transform on `body` describe it). A plate of
    one part is turned into the single-part form so everything that handles one part keeps working unchanged."""
    if body.parts and len(body.parts) > 1:
        return body.parts
    if body.parts:
        p = body.parts[0]
        body.model_id, body.rotation, body.scale, body.position, body.parts = p.model_id, p.rotation, p.scale, p.position, None
    if not body.model_id:
        raise HTTPException(422, "Give a model_id, or a list of parts")
    return None


class AssignIn(TransformIn):
    model_id: str | None = None
    filament: str
    nozzle_mm: float = Field(DEFAULT_NOZZLE, gt=0, le=2)     # the nozzle size the job will need (its process preset decides)


def _annotate_overflow(ranked, instances) -> None:
    """For each printer a plate does not fit, say which parts are the problem."""
    for c in ranked:
        if not c.eligible and instances:
            bed = PRINTER_MODELS[c.model]["bed"]
            names = multipart.overflow_names(instances, bed, assign.BED_MARGIN_MM)
            if names:
                c.blockers.append("Parts that do not fit: " + ", ".join(names))


@router.post("/api/assign")
def assign_printer(body: AssignIn, user: dict = User):
    if body.filament not in FILAMENTS:
        raise HTTPException(400, "Unknown filament")
    parts = plate_of(body)
    if parts is None:
        size = meshio.dims(_transformed(body.model_id, body.rotation, body.scale, user))
        ranked = assign.rank(_fleet(), size, body.filament, body.nozzle_mm, tuple(body.position))
    else:
        instances = plate_instances(parts, user)
        size, centre = multipart.envelope(instances)
        ranked = assign.rank(_fleet(), size, body.filament, body.nozzle_mm, centre)
        _annotate_overflow(ranked, instances)
    best = ranked[0] if ranked and ranked[0].eligible else None
    return {"dims": size, "chosen": best.printer_id if best else None,
            "candidates": [c.asdict() for c in ranked]}


class ArrangeIn(BaseModel):
    parts: list[PartIn] = Field(max_length=multipart.MAX_PARTS, min_length=1)
    printer_model: str
    filament: str = "PLA"
    preset: str = "0.20mm Standard"


@router.post("/api/plate/arrange")
def arrange_plate(body: ArrangeIn, user: dict = User):
    """The slicer's own arrangement of these parts on a printer model's bed. Returns, per part in order, where its centre goes and an extra
    turn about the vertical axis (degrees) to add to the part's Z rotation; the slicer may turn parts to pack them tighter."""
    if body.printer_model not in PRINTER_MODELS:
        raise HTTPException(400, "Unknown printer model")
    if body.filament not in FILAMENTS or body.preset not in {p["id"] for p in PROCESS_PRESETS}:
        raise HTTPException(400, "Unknown filament or process preset")
    instances = plate_instances(body.parts, user, check=False)
    with tempfile.TemporaryDirectory(prefix="arrange-") as tmp:
        paths = []
        for k, inst in enumerate(instances):
            p = Path(tmp) / f"part{k}.stl"
            meshio.export_stl(inst.mesh, p)
            paths.append(p)
        try:
            placed = slicer.arrange(Path(tmp) / "work", paths, body.printer_model, body.preset, body.filament)
        except slicer.PlateOverflow as e:
            raise HTTPException(409, f"{e}. Try a larger printer or fewer parts.")
        except slicer.SliceError as e:
            raise HTTPException(409, str(e))
    return {"parts": placed}
