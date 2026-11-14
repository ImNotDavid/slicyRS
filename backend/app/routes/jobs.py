"""Jobs: slicing, previews, the pool, printing, files and history."""
import time
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from .. import allocator, assign, forecast, jobstate, meshio, multipart, pipeline, printing, slicer, store, thumbnail
from .. import bedstate, permissions
from ..auth import audit
from ..defaults import effective_overrides
from ..catalog import ALLOWED_SETTING_KEYS, DEFAULT_BED_TYPE, FILAMENTS, PROCESS_PRESETS, preset_nozzle, validate_settings
from ..storage import JOBS, MODELS, files
from .common import User, _fleet, _source_file, _transformed, live_progress, plate_instances, pool
from .models import TransformIn, _annotate_overflow, plate_of

router = APIRouter()


class SliceIn(TransformIn):
    model_id: str | None = None
    filament: str
    preset: str = "0.20mm Standard"
    settings: dict[str, Any] = {}
    goal: str | None = None        # the one-click goal the settings came from, kept for the job page
    printer_id: str | None = None  # manual override; otherwise auto-assigned


# Decided by the server, never by the user: the build plate comes from the assigned printer.
SERVER_MANAGED_SETTINGS = {"curr_bed_type"}


def _discard_slice_drafts(user: dict[str, Any], model_id: str | None, keep: str | None = None) -> None:
    """A slice made to look at a part is a draft, not a job. Sending the same part to the pool replaces it."""
    for j in store.jobs.all():
        if j.get("draft") and j.get("direct") and j.get("owner_id") == user["id"] and j.get("model_id") == model_id and j["id"] != keep:
            jobstate.discard(j["id"])


@router.post("/api/slice")
def start_slice(body: SliceIn, user: dict = User):
    if body.filament not in FILAMENTS:
        raise HTTPException(400, "Unknown filament")
    if body.preset not in {p["id"] for p in PROCESS_PRESETS}:
        raise HTTPException(400, "Unknown process preset")
    bad = set(body.settings) - ALLOWED_SETTING_KEYS - SERVER_MANAGED_SETTINGS
    if bad:
        raise HTTPException(400, f"Unknown settings: {sorted(bad)}")
    if (why := validate_settings({k: v for k, v in body.settings.items() if k not in SERVER_MANAGED_SETTINGS})):
        raise HTTPException(400, why)
    body.settings = {k: v for k, v in body.settings.items() if k not in SERVER_MANAGED_SETTINGS}

    if (plate := plate_of(body)):
        return _slice_plate(body, plate, user)
    mesh = _transformed(body.model_id, body.rotation, body.scale, user)
    size = meshio.dims(mesh)
    ranked = assign.rank(_fleet(), size, body.filament, preset_nozzle(body.preset), tuple(body.position))
    if body.printer_id:
        chosen = next((c for c in ranked if c.printer_id == body.printer_id), None)
        if not chosen or not chosen.eligible:
            why = "; ".join(chosen.blockers) if chosen else "Unknown printer"
            raise HTTPException(409, f"Selected printer cannot print this part: {why}")
    else:
        chosen = ranked[0] if ranked and ranked[0].eligible else None
        if not chosen:
            why = sorted({b for c in ranked for b in c.blockers})
            raise HTTPException(409, "No printer in the fleet can print this part. " + "; ".join(why))

    job_id = uuid.uuid4().hex[:12]
    workdir = files.create(JOBS, job_id)
    src = _source_file(body.model_id, user)
    untransformed = tuple(body.rotation) == (0, 0, 0) and body.scale == 1.0 and not any(body.position)
    if untransformed and src.suffix == ".3mf":
        files.write_bytes(JOBS, job_id, "input.3mf", src.read_bytes())
        input_path = files.path(JOBS, job_id, "input.3mf")
    else:
        input_path = files.path(JOBS, job_id, "input.stl")
        meshio.export_stl(mesh, input_path)
    files.publish(JOBS, job_id)

    printer = store.printers.get(chosen.printer_id) or {}
    name = files.read_text(MODELS, body.model_id, "name.txt")
    store.jobs.put(job_id, {
        "id": job_id, "name": name, "model_id": body.model_id, "draft": True, "direct": True, "state": "slicing", "created": time.time(), "owner_id": user["id"],
        "owner_email": user["email"],
        "printer_id": chosen.printer_id, "printer_name": printer.get("name"), "printer_model": chosen.model,
        "filament": body.filament, "preset": body.preset, "dims": size, "candidates": [c.asdict() for c in ranked],
        "loaded_slot": chosen.loaded_slot, "queued": False, "printed": False,
        "bed_type": printer.get("bed_type") or DEFAULT_BED_TYPE, "phase": "slicing", "phase_at": time.time(),
        "submitted_at": time.time(),
        "spec": {"model_id": body.model_id, "rotation": list(body.rotation), "scale": body.scale, "position": list(body.position),
                 "filament": body.filament, "preset": body.preset, "settings": body.settings, "goal": body.goal},
    })
    jobstate.event(job_id, "phase", user["email"], **{"from": None, "to": "slicing", "reason": "sliced directly for a chosen printer"})
    pool.submit(_do_slice, job_id, workdir, input_path, chosen.model, body, printer.get("bed_type") or DEFAULT_BED_TYPE)
    return store.jobs.get(job_id)


def _do_slice(job_id: str, workdir: Path, input_path: Path, printer_model: str, body: SliceIn, bed_type: str,
              placements: list[tuple[Path, tuple[float, float]]] | None = None) -> None:
    # only what the user changed goes to the slicer, so the printer's own profile stays in charge of everything else;
    # the plate is fixed by the assigned printer, so the sliced file always matches what is physically installed
    overrides = effective_overrides(body.preset, body.settings, bed_type)
    try:
        result = slicer.run(workdir, input_path, printer_model, body.preset, body.filament, overrides,
                            offset=tuple(body.position) if any(body.position) and not placements else None, placements=placements)
        if result.get("bundle") and not placements:
            thumbnail.maybe_add(files.path(JOBS, job_id, result["bundle"]), input_path)
        files.publish(JOBS, job_id)
        jobstate.transition(job_id, "ready", None, state="done", stats=result, gcode=result["gcode"], bundle=result.get("bundle"))
    except Exception as e:  # surfaced to the UI as the job error
        jobstate.transition(job_id, "failed", None, force=True, state="failed", error=str(e))


@router.get("/api/jobs")
def list_jobs(scope: str = "mine", user: dict = User):
    """My jobs (everyone), or with `scope=all` every job (operators and admins: the All jobs page)."""
    if scope not in ("mine", "all"):
        raise HTTPException(400, "scope must be mine or all")
    if scope == "all":
        permissions.require(user, "job.see_all", message="Only operators and admins can see everyone's jobs")
    jobs = [j for j in store.jobs.all() if not j.get("draft") and (scope == "all" or j.get("owner_id") == user["id"])]
    return sorted((_with_live(_with_plate_flag(j)) for j in jobs), key=lambda j: -j["created"])


def _with_plate_flag(job: dict[str, Any]) -> dict[str, Any]:
    """`plate_to_clear`: the job's print is over but its plate is still on the printer, so its owner can confirm it is clear from
    Jobs (members do not see the Fleet page)."""
    pid = job.get("printer_id")
    if not pid or job.get("phase") not in ("printed", "failed", "unknown"):
        return job
    p = store.printers.get(pid)
    on_plate = p and bedstate.get(p).get("state") == "occupied" and any(j["id"] == job["id"] for j in bedstate.last_plate(pid))
    return {**job, "plate_to_clear": True} if on_plate else job


def _with_live(job: dict[str, Any]) -> dict[str, Any]:
    live = live_progress(job)
    return {**job, "live": live} if live else job


def _owned_job(job_id: str, user: dict[str, Any]) -> dict[str, Any]:
    job = store.jobs.get(job_id)
    permissions.require(user, "job.manage", job, hide=True, message="Unknown job")
    return job  # type: ignore[return-value]


def _viewable_job(job_id: str, user: dict[str, Any]) -> dict[str, Any]:
    job = store.jobs.get(job_id)
    permissions.require(user, "job.view", job, hide=True, message="Unknown job")
    return job  # type: ignore[return-value]


@router.get("/api/jobs/{job_id}")
def get_job(job_id: str, user: dict = User):
    return _owned_job(job_id, user)


def _job_file(job_id: str, key: str, user: dict[str, Any], view: bool = False) -> Path:
    job = (_viewable_job if view else _owned_job)(job_id, user)
    if job.get("batch_lead"):                       # a part on a shared plate: the plate's files belong to its lead job
        job = store.jobs.get(job["batch_lead"]) or job
        job_id = job["id"]
    if job.get("files_deleted_at"):
        raise HTTPException(410, "The files for this job were deleted to save space. Slice the part again to get new ones.")
    if job["state"] != "done" or not job.get(key):
        raise HTTPException(409, "Not available")
    return files.path(JOBS, job_id, job[key])


@router.get("/api/jobs/{job_id}/gcode")
def job_gcode(job_id: str, user: dict = User):
    job = _viewable_job(job_id, user)          # operators may look at the toolpaths of a job on their printer
    stem = Path(job["name"]).stem
    return FileResponse(_job_file(job_id, "gcode", user, view=True), media_type="text/plain", filename=f"{stem}.gcode")


@router.get("/api/jobs/{job_id}/bundle")
def job_bundle(job_id: str, user: dict = User):
    job = _owned_job(job_id, user)
    stem = Path(job["name"]).stem
    return FileResponse(_job_file(job_id, "bundle", user), filename=f"{stem}.gcode.3mf")


def _remote_name(job: dict[str, Any]) -> str:
    return printing.remote_name(job)


@router.post("/api/jobs/{job_id}/print")
def print_job(job_id: str, timelapse: bool = False, user: dict = User):
    job = _owned_job(job_id, user)
    if timelapse:                                   # 2.8: the owner asked for a timelapse of this print
        job = store.jobs.update(job_id, timelapse=True) or job
    bundle = _job_file(job_id, "bundle", user)
    if job.get("batch_lead"):                       # any owner on a shared plate may start it
        job = store.jobs.get(job["batch_lead"]) or job
    try:
        out = printing.start(job, bundle, user)
    except printing.PrintBlocked as e:
        raise HTTPException(e.status, e.message)
    if job.get("draft"):                            # a job exists once it has gone to a printer
        store.jobs.update(job["id"], draft=False)
    return out


# ------------------------------------------------------------------- job pipeline (A.1, A.2)

class DraftIn(TransformIn):
    model_id: str | None = None
    filament: str
    preset: str = "0.20mm Standard"
    settings: dict[str, Any] = {}
    goal: str | None = None


@router.post("/api/jobs/preview")
def create_draft(body: DraftIn, user: dict = User):
    """Save a printer-agnostic job and slice a preview for it (estimate time, filament, layers). Nothing is assigned yet."""
    if body.filament not in FILAMENTS:
        raise HTTPException(400, "Unknown filament")
    if body.preset not in {p["id"] for p in PROCESS_PRESETS}:
        raise HTTPException(400, "Unknown process preset")
    bad = set(body.settings) - ALLOWED_SETTING_KEYS - SERVER_MANAGED_SETTINGS
    if bad:
        raise HTTPException(400, f"Unknown settings: {sorted(bad)}")
    if (why := validate_settings({k: v for k, v in body.settings.items() if k not in SERVER_MANAGED_SETTINGS})):
        raise HTTPException(400, why)
    settings = {k: v for k, v in body.settings.items() if k not in SERVER_MANAGED_SETTINGS}
    if (plate := plate_of(body)):
        return _draft_plate(body, plate, settings, user)
    mesh = _transformed(body.model_id, body.rotation, body.scale, user)
    size = meshio.dims(mesh)
    nozzle = preset_nozzle(body.preset)
    if not forecast.eligible_printers(size, body.filament, nozzle, tuple(body.position)):
        why = sorted({b for c in assign.rank(_fleet(), size, body.filament, nozzle, tuple(body.position)) for b in c.blockers if b != "Printer is offline"})
        raise HTTPException(409, "No printer in the fleet can ever print this part. " + "; ".join(why))
    job_id = uuid.uuid4().hex[:12]
    files.create(JOBS, job_id)
    src = _source_file(body.model_id, user)
    if tuple(body.rotation) == (0, 0, 0) and body.scale == 1.0 and not any(body.position) and src.suffix == ".3mf":
        input_file = "input.3mf"
        files.write_bytes(JOBS, job_id, input_file, src.read_bytes())
    else:
        input_file = "input.stl"
        meshio.export_stl(mesh, files.path(JOBS, job_id, input_file))
    files.publish(JOBS, job_id)
    name = files.read_text(MODELS, body.model_id, "name.txt")
    store.jobs.put(job_id, {
        "id": job_id, "name": name, "draft": True, "state": "submitted", "phase": "submitted", "phase_at": time.time(), "created": time.time(),
        "owner_id": user["id"], "owner_email": user["email"], "printer_id": None, "printer_name": None, "printer_model": None,
        "filament": body.filament, "preset": body.preset, "dims": size, "queued": False, "printed": False, "pooled": False,
        "input_file": input_file, "pickup_by": None, "submitted_at": None,
        "spec": {"model_id": body.model_id, "rotation": list(body.rotation), "scale": body.scale, "position": list(body.position), "filament": body.filament,
                 "preset": body.preset, "settings": settings, "goal": body.goal},
    })
    jobstate.event(job_id, "phase", user["email"], **{"from": None, "to": "submitted"})
    pipeline.request_preview(job_id)
    return store.jobs.get(job_id)


def _write_plate_files(job_id: str, instances) -> list[dict[str, Any]]:
    """One input file per distinct (model, turn, scale); copies share a file. Returns what the job remembers about each part."""
    names: dict[tuple, str] = {}
    out = []
    for inst in instances:
        if inst.key() not in names:
            names[inst.key()] = f"input_{len(names)}.stl"
            meshio.export_stl(inst.mesh, files.path(JOBS, job_id, names[inst.key()]))
        out.append({"model_id": inst.model_id, "name": inst.name, "rotation": list(inst.rotation), "scale": inst.scale,
                    "position": list(inst.position), "file": names[inst.key()]})
    files.publish(JOBS, job_id)
    return out


def _plate_blockers(ranked, instances, ignore_offline: bool = False) -> str:
    _annotate_overflow(ranked, instances)
    return "; ".join(sorted({b for c in ranked for b in c.blockers if not (ignore_offline and b == "Printer is offline")}))


def _draft_plate(body: DraftIn, plate, settings: dict[str, Any], user: dict[str, Any]):
    instances = plate_instances(plate, user)
    size, centre = multipart.envelope(instances)
    nozzle = preset_nozzle(body.preset)
    if not forecast.eligible_printers(size, body.filament, nozzle, centre):
        ranked = assign.rank(_fleet(), size, body.filament, nozzle, centre)
        raise HTTPException(409, "No printer in the fleet can ever print this plate. " + _plate_blockers(ranked, instances, True))
    job_id = uuid.uuid4().hex[:12]
    files.create(JOBS, job_id)
    parts = _write_plate_files(job_id, instances)
    store.jobs.put(job_id, {
        "id": job_id, "name": multipart.plate_name(instances), "draft": True, "state": "submitted", "phase": "submitted", "phase_at": time.time(),
        "created": time.time(), "owner_id": user["id"], "owner_email": user["email"], "printer_id": None, "printer_name": None, "printer_model": None,
        "filament": body.filament, "preset": body.preset, "dims": size, "queued": False, "printed": False, "pooled": False,
        "input_file": parts[0]["file"], "pickup_by": None, "submitted_at": None,
        "spec": {"parts": parts, "position": list(centre), "filament": body.filament, "preset": body.preset, "settings": settings, "goal": body.goal},
    })
    jobstate.event(job_id, "phase", user["email"], **{"from": None, "to": "submitted"})
    pipeline.request_preview(job_id)
    return store.jobs.get(job_id)


def _slice_plate(body: SliceIn, plate, user: dict[str, Any]):
    instances = plate_instances(plate, user)
    size, centre = multipart.envelope(instances)
    ranked = assign.rank(_fleet(), size, body.filament, preset_nozzle(body.preset), centre)
    if body.printer_id:
        chosen = next((c for c in ranked if c.printer_id == body.printer_id), None)
        if not chosen or not chosen.eligible:
            why = "; ".join((chosen.blockers if chosen else []) or ["Unknown printer"])
            raise HTTPException(409, f"Selected printer cannot print this plate: {why}")
    else:
        chosen = ranked[0] if ranked and ranked[0].eligible else None
        if not chosen:
            raise HTTPException(409, "No printer in the fleet can print this plate. " + _plate_blockers(ranked, instances))
    job_id = uuid.uuid4().hex[:12]
    workdir = files.create(JOBS, job_id)
    parts = _write_plate_files(job_id, instances)
    printer = store.printers.get(chosen.printer_id) or {}
    store.jobs.put(job_id, {
        "id": job_id, "name": multipart.plate_name(instances), "model_id": plate[0].model_id, "draft": True, "direct": True, "state": "slicing",
        "created": time.time(), "owner_id": user["id"], "owner_email": user["email"],
        "printer_id": chosen.printer_id, "printer_name": printer.get("name"), "printer_model": chosen.model,
        "filament": body.filament, "preset": body.preset, "dims": size, "candidates": [c.asdict() for c in ranked],
        "loaded_slot": chosen.loaded_slot, "queued": False, "printed": False,
        "bed_type": printer.get("bed_type") or DEFAULT_BED_TYPE, "phase": "slicing", "phase_at": time.time(), "submitted_at": time.time(),
        "spec": {"parts": parts, "position": list(centre), "filament": body.filament, "preset": body.preset, "settings": body.settings, "goal": body.goal},
    })
    jobstate.event(job_id, "phase", user["email"], **{"from": None, "to": "slicing", "reason": "plate sliced directly for a chosen printer"})
    placements = [(files.path(JOBS, job_id, q["file"]), tuple(q["position"])) for q in parts]
    pool.submit(_do_slice, job_id, workdir, placements[0][0], chosen.model, body, printer.get("bed_type") or DEFAULT_BED_TYPE, placements)
    return store.jobs.get(job_id)


@router.get("/api/jobs/{job_id}/forecast")
def job_forecast(job_id: str, pickup_by: float | None = None, user: dict = User):
    job = _owned_job(job_id, user)
    return forecast.pickup_check({**job, "submitted_at": time.time()}, pickup_by)


class SubmitIn(BaseModel):
    pickup_by: float | None = None           # epoch seconds; None = whenever
    colour: str = Field("any", max_length=40)  # recorded for later colour matching
    auto_start: bool = True                  # ignored: pool jobs always start by themselves now (owner decision); kept for old clients
    allow_batching: bool = False             # may share a plate with other people's parts (they wait for a partner while there is time)
    timelapse: bool = False                  # record a timelapse of the print (2.8)


@router.post("/api/jobs/{job_id}/submit")
def submit_job(job_id: str, body: SubmitIn, user: dict = User):
    """Put a previewed job in the shared pool. A pickup time the forecast calls unlikely is refused with the earliest likely one."""
    job = _owned_job(job_id, user)
    if job.get("phase") != "previewed":
        raise HTTPException(409, "The preview is not ready yet" if job.get("phase") in ("submitted", "previewing") else
                            f"This job cannot be submitted (it is {job.get('phase')})")
    now = time.time()
    if body.pickup_by is not None and body.pickup_by <= now:
        raise HTTPException(400, "The pickup time must be in the future")
    check = forecast.pickup_check({**job, "submitted_at": now}, body.pickup_by, now)
    if not check["feasible"]:
        raise HTTPException(409, "No printer in the fleet can print this part")
    if not check["likely"]:
        jobstate.event(job_id, "pickup_refused", user["email"], pickup_by=body.pickup_by, earliest=check["earliest_finish"])
        return JSONResponse({"detail": "That pickup time is unlikely: the earliest the part is expected to be ready is shown. "
                                       "Choose a later time or submit without one.",
                             "earliest_pickup": check["earliest_finish"]}, status_code=409)
    jobstate.transition(job_id, "pooled", user["email"], state="pooled", pooled=True, draft=False, submitted_at=now, pickup_by=body.pickup_by,
                        colour=body.colour, auto_start=True, allow_batching=body.allow_batching, at_risk=False,
                        timelapse=body.timelapse)
    _discard_slice_drafts(user, (job.get("spec") or {}).get("model_id"), keep=job_id)
    audit(user, "job_submit", f"{job['name']}" + (f" pickup by {time.strftime('%Y-%m-%d %H:%M', time.localtime(body.pickup_by))}" if body.pickup_by else ""))
    allocator.tick()
    return store.jobs.get(job_id)


@router.delete("/api/jobs/{job_id}")
def discard_draft(job_id: str, user: dict = User):
    """Throw away a draft (a preview or a slice that was never sent anywhere). Real jobs are cancelled, not deleted."""
    job = _owned_job(job_id, user)
    if not job.get("draft"):
        raise HTTPException(409, "Only a draft can be discarded; cancel a job instead")
    jobstate.discard(job_id)
    return {"ok": True}


@router.post("/api/jobs/{job_id}/cancel")
def cancel_job(job_id: str, user: dict = User):
    job = _owned_job(job_id, user)
    if job.get("draft"):
        jobstate.discard(job_id)
        return {**job, "phase": "cancelled", "state": "cancelled"}
    try:
        out = jobstate.transition(job_id, "cancelled", user["email"], state="cancelled")
    except jobstate.InvalidTransition:
        raise HTTPException(409, f"A job that is {job.get('phase')} cannot be cancelled")
    audit(user, "job_cancel", job["name"])
    return out


class CauseIn(BaseModel):
    cause: str
    note: str = Field("", max_length=300)


@router.put("/api/jobs/{job_id}/cause")
def explain_failure(job_id: str, body: CauseIn, user: dict = User):
    """Why a print failed (2.13): its owner or an operator says, and either can change it."""
    from .. import failures
    job = _viewable_job(job_id, user)
    permissions.require(user, "failures.explain", job, message="Only the job's owner or an operator can say why it failed")
    try:
        return failures.set_cause(job, body.cause, body.note, user)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/api/jobs/{job_id}/{action}")
def control_print(job_id: str, action: str, user: dict = User):
    """Pause, resume or stop a running print (2.4). Owners, operators and admins may pause and resume; a shared plate can only
    be stopped by an operator or admin."""
    from .. import printcontrol
    if action not in ("pause", "resume", "stop"):
        raise HTTPException(404, "Not found")
    job = _viewable_job(job_id, user)
    permissions.require(user, "print.stop" if action == "stop" else "print.pause", job,
                        message="A shared plate can only be stopped by an operator or admin" if action == "stop" and permissions.can(user, "print.pause", job)
                        else "You cannot control this print")
    try:
        return printcontrol.run(job, action, user)
    except printcontrol.ControlError as e:
        raise HTTPException(e.status, e.message)


@router.get("/api/jobs/{job_id}/events")
def job_events(job_id: str, user: dict = User):
    _viewable_job(job_id, user)
    return jobstate.events_for(job_id)
