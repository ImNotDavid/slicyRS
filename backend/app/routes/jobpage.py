"""The job page (2.9): everything about one job in one answer, shaped for reading rather than for the pipeline."""
from typing import Any

from fastapi import APIRouter

from .. import bedstate, goals, jobstate, permissions, plates, store
from ..catalog import FIELDS, job_nozzle
from ..defaults import ui_defaults
from ..storage import MODELS, files
from .common import User
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
        "photo": None,                      # a camera picture of the finished plate (2.7) lands here
        "events": jobstate.events_for(job["id"]),
    }
