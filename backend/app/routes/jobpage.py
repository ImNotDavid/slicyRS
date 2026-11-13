"""The job page (2.9): everything about one job in one answer, shaped for reading rather than for the pipeline."""
from typing import Any

import threading

import trimesh
from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from .. import bedstate, goals, jobstate, meshio, permissions, plates, store, thumbnail
from ..catalog import FIELDS, job_nozzle
from ..defaults import ui_defaults
from ..storage import JOBS, MODELS, files
from .common import User, live_progress
from .jobs import _viewable_job

router = APIRouter()
FINISHED = ("printed", "failed", "unknown")


def _settings(spec: dict[str, Any], preset: str) -> list[dict[str, Any]]:
    """What the person changed from the preset, labelled as in the settings panel. The full list is built in the browser from the
    catalogue plus these values, so it is not repeated here."""
    try:
        defaults = ui_defaults(preset)
    except Exception:          # a preset that no longer exists: show the values without a comparison
        defaults = {}
    out = []
    for k, v in (spec.get("settings") or {}).items():
        if k in FIELDS and v != defaults.get(k):
            f = FIELDS[k]
            out.append({"key": k, "label": f["label"], "value": v, "default": defaults.get(k), "unit": f.get("unit")})
    return out


def _model(model_id: str | None, name: str | None, user: dict[str, Any]) -> dict[str, Any]:
    rec = store.models.get(model_id) if model_id else None
    available = bool(rec and not rec.get("files_deleted_at") and files.exists(MODELS, model_id)) if model_id else False
    return {"model_id": model_id if permissions.can(user, "model.use", rec) else None, "name": name or (rec or {}).get("name") or "part",
            "available": available and permissions.can(user, "model.use", rec)}


def _parts(job: dict[str, Any], user: dict[str, Any]) -> list[dict[str, Any]]:
    spec = job.get("spec") or {}
    if spec.get("parts"):
        return [{**_model(p.get("model_id"), p.get("name"), user), "rotation": p.get("rotation"), "scale": p.get("scale"), "position": p.get("position")}
                for p in spec["parts"]]
    mid = spec.get("model_id") or job.get("model_id")
    return [{**_model(mid, job.get("name"), user), "rotation": spec.get("rotation"), "scale": spec.get("scale"), "position": spec.get("position")}]


@router.get("/api/jobs/{job_id}/page")
def job_page(job_id: str, user: dict = User):
    job = _viewable_job(job_id, user)
    spec = job.get("spec") or {}
    preset = spec.get("preset") or job.get("preset")
    staff = permissions.is_operator(user)
    printer = store.printers.get(job["printer_id"]) if job.get("printer_id") else None
    goal = next((g for g in goals.GOALS if g["id"] == spec.get("goal")), None)
    # a shared plate: the other parts by name; owners only for operators and admins (owner decision)
    plate = [j for j in plates.jobs_on_plate(job) if j["id"] != job["id"]] if plates.plate_id(job) else []
    lead = plates.lead_of(job) or job
    stats = job.get("stats") or {}
    preview = job.get("preview") or {}
    result = job.get("result") or None
    bed = bedstate.get(printer) if printer else {}
    return {
        "job": job,
        "mine": job.get("owner_id") == user["id"],
        "can_manage": permissions.can(user, "job.manage", job),
        "can_pause": job.get("phase") == "printing" and permissions.can(user, "print.pause", job),
        "can_stop": job.get("phase") == "printing" and permissions.can(user, "print.stop", job),
        "owner": job.get("owner_email") if staff or job.get("owner_id") == user["id"] else None,
        "goal": {"id": goal["id"], "name": goal["name"]} if goal else None,
        "preset": preset,
        "filament": spec.get("filament") or job.get("filament"),
        "nozzle_mm": job_nozzle(job),
        "changed_settings": _settings(spec, preset),
        "settings": spec.get("settings") or {},
        "parts": _parts(job, user),
        "shared_with": [{"name": j["name"], **({"owner": j.get("owner_email")} if staff else {})} for j in plate],
        "printer": {"id": printer["id"], "name": printer["name"], "model": printer["model"], "bed_type": job.get("bed_type") or printer.get("bed_type")}
                   if printer else None,
        "estimate_s": stats.get("time_s") or preview.get("time_s"),
        "actual_s": (result or {}).get("duration_s"),
        "filament_g": stats.get("filament_g") or preview.get("filament_g"),
        "layers": stats.get("layers") or preview.get("layers"),
        "result": result,
        "gcode": bool(lead.get("gcode")) and lead.get("state") == "done" and not lead.get("files_deleted_at"),
        "files_deleted": bool(lead.get("files_deleted_at")),
        "plate_to_clear": bool(printer and job.get("phase") in FINISHED and bed.get("state") == "occupied"
                               and any(j["id"] == job["id"] for j in bedstate.last_plate(printer["id"]))),
        "live": live_progress(job),         # percent, time left, layer and temperatures while it prints (2.1)
        "photo": None,                      # a camera picture of the finished plate (2.7) lands here
        "events": jobstate.events_for(job["id"]),
    }


THUMB = "thumb.png"
THUMB_COLOUR = (0, 30, 255)              # the brand's dark blue; the job does not record the spool colour
_thumb_lock = threading.Lock()


def _plate_mesh(job: dict[str, Any]) -> trimesh.Trimesh | None:
    """The job's parts as they sit on the plate, from the files kept with the job (or the model, for a single part)."""
    spec = job.get("spec") or {}
    if spec.get("parts"):
        meshes = []
        for p in spec["parts"]:
            if not p.get("file") or not files.path(JOBS, job["id"], p["file"]).exists():
                return None
            m = meshio.load_mesh(files.path(JOBS, job["id"], p["file"]))
            x, y = p.get("position") or (0, 0)
            m.apply_translation([x - m.bounds.mean(axis=0)[0], y - m.bounds.mean(axis=0)[1], 0])
            meshes.append(m)
        return trimesh.util.concatenate(meshes) if meshes else None
    for name in (job.get("input_file"), "input.stl", "input.3mf"):
        if name and files.path(JOBS, job["id"], name).exists():
            return meshio.load_mesh(files.path(JOBS, job["id"], name))
    mid = spec.get("model_id") or job.get("model_id")
    if mid and files.exists(MODELS, mid) and files.path(MODELS, mid, "preview.stl").exists():
        return meshio.load_mesh(files.path(MODELS, mid, "preview.stl"))
    return None


@router.get("/api/jobs/{job_id}/thumbnail.png")
def job_thumbnail(job_id: str, user: dict = User):
    """A small picture of the job's part(s) for the job cards, drawn once on the server and kept with the job's files."""
    job = _viewable_job(job_id, user)
    if job.get("batch_lead"):
        job = store.jobs.get(job["batch_lead"]) or job      # a part on a shared plate shows the whole plate
    if job.get("files_deleted_at") or not files.exists(JOBS, job["id"]):
        raise HTTPException(404, "No picture: the job's files were removed")
    path = files.path(JOBS, job["id"], THUMB)
    with _thumb_lock:
        if not path.exists():
            try:
                mesh = _plate_mesh(job)
            except Exception:
                mesh = None
            if mesh is None or not len(mesh.faces):
                raise HTTPException(404, "No picture for this job")
            files.write_bytes(JOBS, job["id"], THUMB, thumbnail.render(mesh, size=256, colour=THUMB_COLOUR))
    return Response(path.read_bytes(), media_type="image/png", headers={"Cache-Control": "private, max-age=86400"})
