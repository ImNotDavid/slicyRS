import logging
import os
import re
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.middleware.sessions import SessionMiddleware

from . import (adapter, allocator, assign, auth, bedstate, crypto, forecast, gateways, jobstate, meshio, outcomes, pipeline, pipelinecfg,
               printing, retention, sessions, slicer, stats, store, taskqueue, worker)
from .adapter import AdapterError
from .auth import audit, current_user, is_admin, require_admin
from .bambu import PrinterError, check_host, links, probe
from .defaults import effective_overrides, ui_defaults
from .catalog import (ALLOWED_SETTING_KEYS, BED_TYPES, DEFAULT_BED_TYPE, FILAMENTS, PRINTER_MODELS, PROCESS_PRESETS,
                      SETTINGS_SCHEMA, preset_defaults)
from .config import (ALLOWED_EMAIL_DOMAINS, COOKIE_SECURE, MAX_UPLOAD_MB, MAX_WORKERS, ORCA_BIN,
                     SECRET_KEY, SESSION_HOURS, SLICER_MODE)
from .storage import JOBS, MODELS, files

app = FastAPI(title="slicyRS")
pool = ThreadPoolExecutor(max_workers=MAX_WORKERS)
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY, https_only=COOKIE_SECURE, same_site="lax",
                   max_age=SESSION_HOURS * 3600, session_cookie="slicyrs_session")
app.include_router(auth.router)
app.include_router(gateways.router)
User = Depends(current_user)


@app.middleware("http")
async def csrf_origin_check(request: Request, call_next):
    """Browsers always send Origin on cross-site writes: it must name this host (SameSite=Lax is the second layer)."""
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        origin = request.headers.get("origin")
        if origin and origin.split("://", 1)[-1] != request.headers.get("host"):
            return JSONResponse({"detail": "Cross-origin request blocked"}, status_code=403)
    return await call_next(request)


CONTENT_SECURITY_POLICY = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; "
                           "font-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'; "
                           "object-src 'none'")


@app.middleware("http")
async def security_headers(request: Request, call_next):
    """Set on every response, so they hold behind any reverse proxy (or none)."""
    response = await call_next(request)
    h = response.headers
    h.setdefault("Content-Security-Policy", CONTENT_SECURITY_POLICY)
    h.setdefault("X-Content-Type-Options", "nosniff")
    h.setdefault("X-Frame-Options", "DENY")
    h.setdefault("Referrer-Policy", "same-origin")
    h.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=()")
    h.setdefault("Cross-Origin-Opener-Policy", "same-origin")
    if COOKIE_SECURE:
        h.setdefault("Strict-Transport-Security", "max-age=31536000")
    if request.url.path.startswith(("/api/", "/auth/")):
        h.setdefault("Cache-Control", "no-store")  # never let a browser or proxy keep account, job or printer data
    return response


# ------------------------------------------------------------------ helpers

def _public_printer(p: dict[str, Any], admin: bool) -> dict[str, Any]:
    """Everyone sees status; connection details are admin-only and the access code is never returned."""
    out = {"id": p["id"], "name": p["name"], "model": p["model"], "status": p.get("status", {}),
           "hardened_nozzle": p.get("hardened_nozzle", False), "enabled": p.get("enabled", True),
           "bed_type": p.get("bed_type") or DEFAULT_BED_TYPE, "bed": bedstate.get(p),
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


@app.on_event("startup")
def _startup() -> None:
    if not store.printers.all() and os.environ.get("SEED_DEMO", "0") == "1":
        for name, model, state, ams in [
            ("Bay 1 – P1S", "P1S", "IDLE", [{"slot": 0, "type": "PLA", "color": "#2f6bff", "remain": 80}]),
            ("Bay 2 – A1 mini", "A1 mini", "IDLE", [{"slot": 0, "type": "PETG", "color": "#ffffff", "remain": 60}]),
            ("Bay 3 – X1C", "X1C", "RUNNING", [{"slot": 0, "type": "ABS", "color": "#111111", "remain": 90}]),
            ("Bay 4 – H2D", "H2D", "IDLE", [{"slot": 0, "type": "PLA", "color": "#e11d48", "remain": 70}]),
        ]:
            pid = uuid.uuid4().hex[:8]
            store.printers.put(pid, {"id": pid, "name": name, "model": model, "demo": True,
                                     "status": {"state": state, "ams": ams}})
    links.sync()
    sessions.purge_expired()
    outcomes.sweep()
    bedstate.reset_all()   # after a restart nothing is known about any build plate
    worker.start_embedded(int(os.environ.get("EMBED_WORKERS", str(MAX_WORKERS))))
    allocator.start_background(float(os.environ.get("ALLOCATOR_INTERVAL_S", "5")))
    retention.start_background()


# ------------------------------------------------------------------- catalog

@app.get("/api/health")
def health():
    return {"ok": True, "slicer_mode": SLICER_MODE}


@app.get("/api/catalog")
def catalog(user: dict = User):
    return {
        "printer_models": {k: {**v, "bed": list(v["bed"])} for k, v in PRINTER_MODELS.items()},
        "filaments": FILAMENTS,
        "process_presets": PROCESS_PRESETS,
        "settings_schema": SETTINGS_SCHEMA,
        "preset_defaults": {p["id"]: ui_defaults(p["id"]) for p in PROCESS_PRESETS},
        "bed_types": list(BED_TYPES),
        "default_bed_type": DEFAULT_BED_TYPE,
        "slicer_mode": SLICER_MODE,
        "engine": ("Mock slicer" if SLICER_MODE == "mock"
                   else os.environ.get("SLICER_NAME") or ("Bambu Studio" if "bambu" in ORCA_BIN.lower() else "OrcaSlicer")),
    }


# ------------------------------------------------------------------- models

@app.post("/api/models")
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
            "triangles": int(len(mesh.faces))}


@app.get("/api/models/{model_id}/preview.stl")
def preview(model_id: str, user: dict = User):
    return FileResponse(files.path(MODELS, _checked_model(model_id, user), "preview.stl"), media_type="model/stl")


class TransformIn(BaseModel):
    rotation: tuple[float, float, float] = (0, 0, 0)
    scale: float = Field(1.0, gt=0, le=100)


class AssignIn(TransformIn):
    model_id: str
    filament: str


@app.post("/api/assign")
def assign_printer(body: AssignIn, user: dict = User):
    if body.filament not in FILAMENTS:
        raise HTTPException(400, "Unknown filament")
    size = meshio.dims(_transformed(body.model_id, body.rotation, body.scale, user))
    ranked = assign.rank(_fleet(), size, body.filament)
    best = ranked[0] if ranked and ranked[0].eligible else None
    return {"dims": size, "chosen": best.printer_id if best else None,
            "candidates": [c.asdict() for c in ranked]}


# ------------------------------------------------------------------ printers

@app.get("/api/printers")
def list_printers(user: dict = User):
    """Fleet status for every signed-in user (connection details are admin-only)."""
    return [_public_printer(p, is_admin(user)) for p in store.printers.all()]


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
    gateway: str | None = None        # id of the remote gateway that reaches this printer; empty = this server connects directly
    status: dict[str, Any] | None = None


def _validate_printer(body: PrinterIn) -> None:
    if body.model not in PRINTER_MODELS:
        raise HTTPException(400, "Unknown printer model")
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


@app.post("/api/admin/printers")
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


@app.put("/api/admin/printers/{printer_id}")
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


@app.delete("/api/admin/printers/{printer_id}")
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


@app.post("/api/admin/printers/test")
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


# --------------------------------------------------------------------- admin

class InviteIn(BaseModel):
    email: str
    name: str = ""
    role: str = "user"


@app.get("/api/admin/users")
def list_users(admin: dict = Depends(require_admin)):
    return [auth.public_user(u) for u in store.users.all()]


@app.post("/api/admin/users")
def invite_user(body: InviteIn, admin: dict = Depends(require_admin)):
    email = auth.norm_email(body.email)
    if not email:
        raise HTTPException(400, "Enter a valid email address")
    if body.role not in ("user", "admin"):
        raise HTTPException(400, "Role must be user or admin")
    if not auth.domain_ok(email):
        raise HTTPException(400, f"Only these email domains can be invited: {', '.join(sorted(ALLOWED_EMAIL_DOMAINS))}")
    if auth.find_user_by_email(email):
        raise HTTPException(409, "That person already has an account")
    user = auth.create_user(email, body.role, body.name.strip(), invited_by=admin["email"])
    audit(admin, "user_invite", f"{email} as {body.role}")
    return auth.public_user(user)


class UserPatch(BaseModel):
    role: str | None = None
    active: bool | None = None
    operator: bool | None = None   # may clear build plates and is told when one waits


def _other_active_admins(exclude_id: str) -> int:
    return sum(1 for u in store.users.all() if u["id"] != exclude_id and u["role"] == "admin" and u.get("active", True))


@app.patch("/api/admin/users/{user_id}")
def patch_user(user_id: str, body: UserPatch, admin: dict = Depends(require_admin)):
    target = store.users.get(user_id)
    if not target:
        raise HTTPException(404, "Unknown user")
    if body.role not in (None, "user", "admin"):
        raise HTTPException(400, "Role must be user or admin")
    losing_admin = target["role"] == "admin" and ((body.role == "user") or (body.active is False))
    if losing_admin and _other_active_admins(user_id) == 0:
        raise HTTPException(409, "There must be at least one active admin")
    fields = {k: v for k, v in body.model_dump().items() if v is not None}
    rec = store.users.update(user_id, **fields)
    audit(admin, "user_update", f"{target['email']}: {fields}")
    if body.active is False:
        sessions.revoke_user(user_id)  # disabling takes effect at once, not when the cookie expires (roles are read live anyway)
    return auth.public_user(rec)  # type: ignore[arg-type]


@app.delete("/api/admin/users/{user_id}")
def delete_user(user_id: str, admin: dict = Depends(require_admin)):
    target = store.users.get(user_id)
    if not target:
        raise HTTPException(404, "Unknown user")
    if target["role"] == "admin" and target.get("active", True) and _other_active_admins(user_id) == 0:
        raise HTTPException(409, "There must be at least one active admin")
    store.users.delete(user_id)
    sessions.revoke_user(user_id)
    audit(admin, "user_delete", target["email"])
    return {"ok": True}


@app.post("/api/admin/email/test")
def email_test(admin: dict = Depends(require_admin)):
    """Send a test message to the admin's own address so SMTP problems show up here, not as silent login failures."""
    from . import mailer
    try:
        mailer.send(admin["email"], f"{auth.APP_NAME} test email", "SMTP is working: login codes will reach people.\n")
    except Exception as e:
        raise HTTPException(502, f"Could not send: {type(e).__name__}: {e}")
    audit(admin, "email_test", admin["email"])
    return {"ok": True, "to": admin["email"]}


@app.delete("/api/admin/users/{user_id}/sessions")
def revoke_user_sessions(user_id: str, admin: dict = Depends(require_admin)):
    target = store.users.get(user_id)
    if not target:
        raise HTTPException(404, "Unknown user")
    n = sessions.revoke_user(user_id)
    audit(admin, "sessions_revoked", f"{target['email']}: {n} session(s)")
    return {"ok": True, "ended": n}


@app.get("/api/admin/storage")
def storage_usage(admin: dict = Depends(require_admin)):
    return retention.usage()


class RetentionIn(BaseModel):
    job_files_days: int | None = Field(None, ge=0, le=3650)
    failed_job_days: int | None = Field(None, ge=0, le=3650)
    model_files_days: int | None = Field(None, ge=0, le=3650)


@app.put("/api/admin/storage/settings")
def storage_settings(body: RetentionIn, admin: dict = Depends(require_admin)):
    new = retention.set_settings({k: v for k, v in body.model_dump().items() if v is not None})
    audit(admin, "storage_settings", str(new))
    return new


@app.post("/api/admin/storage/cleanup")
def storage_cleanup(dry_run: bool = False, admin: dict = Depends(require_admin)):
    report = retention.run(dry_run=dry_run)
    if not dry_run:
        audit(admin, "storage_cleanup_manual", f"{report['jobs_cleaned']} jobs, {report['models_cleaned']} models")
    return report


@app.get("/api/admin/audit")
def audit_log(limit: int = 100, admin: dict = Depends(require_admin)):
    return sorted(store.audit.all(), key=lambda a: -a["at"])[: max(1, min(limit, 500))]


# ---------------------------------------------------------------------- jobs

class SliceIn(TransformIn):
    model_id: str
    filament: str
    preset: str = "0.20mm Standard"
    settings: dict[str, Any] = {}
    printer_id: str | None = None  # manual override; otherwise auto-assigned


# Decided by the server, never by the user: the build plate comes from the assigned printer.
SERVER_MANAGED_SETTINGS = {"curr_bed_type"}


def _discard_slice_drafts(user: dict[str, Any], model_id: str | None, keep: str | None = None) -> None:
    """A slice made to look at a part is a draft, not a job. Sending the same part to the pool replaces it."""
    for j in store.jobs.all():
        if j.get("draft") and j.get("direct") and j.get("owner_id") == user["id"] and j.get("model_id") == model_id and j["id"] != keep:
            jobstate.discard(j["id"])


@app.post("/api/slice")
def start_slice(body: SliceIn, user: dict = User):
    if body.filament not in FILAMENTS:
        raise HTTPException(400, "Unknown filament")
    if body.preset not in {p["id"] for p in PROCESS_PRESETS}:
        raise HTTPException(400, "Unknown process preset")
    bad = set(body.settings) - ALLOWED_SETTING_KEYS - SERVER_MANAGED_SETTINGS
    if bad:
        raise HTTPException(400, f"Unknown settings: {sorted(bad)}")
    body.settings = {k: v for k, v in body.settings.items() if k not in SERVER_MANAGED_SETTINGS}

    mesh = _transformed(body.model_id, body.rotation, body.scale, user)
    size = meshio.dims(mesh)
    ranked = assign.rank(_fleet(), size, body.filament)
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
    untransformed = tuple(body.rotation) == (0, 0, 0) and body.scale == 1.0
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
    })
    jobstate.event(job_id, "phase", user["email"], **{"from": None, "to": "slicing", "reason": "sliced directly for a chosen printer"})
    pool.submit(_do_slice, job_id, workdir, input_path, chosen.model, body, printer.get("bed_type") or DEFAULT_BED_TYPE)
    return store.jobs.get(job_id)


def _do_slice(job_id: str, workdir: Path, input_path: Path, printer_model: str, body: SliceIn, bed_type: str) -> None:
    # only what the user changed goes to the slicer, so the printer's own profile stays in charge of everything else;
    # the plate is fixed by the assigned printer, so the sliced file always matches what is physically installed
    overrides = effective_overrides(body.preset, body.settings, bed_type)
    try:
        result = slicer.run(workdir, input_path, printer_model, body.preset, body.filament, overrides)
        files.publish(JOBS, job_id)
        jobstate.transition(job_id, "ready", None, state="done", stats=result, gcode=result["gcode"], bundle=result.get("bundle"))
    except Exception as e:  # surfaced to the UI as the job error
        jobstate.transition(job_id, "failed", None, force=True, state="failed", error=str(e))


@app.get("/api/jobs")
def list_jobs(user: dict = User):
    """Your jobs; admins see everyone's."""
    jobs = [j for j in store.jobs.all() if not j.get("draft") and (is_admin(user) or j.get("owner_id") == user["id"])]
    return sorted(jobs, key=lambda j: -j["created"])


def _owned_job(job_id: str, user: dict[str, Any]) -> dict[str, Any]:
    job = store.jobs.get(job_id)
    if not job or (job.get("owner_id") != user["id"] and not is_admin(user)):
        raise HTTPException(404, "Unknown job")
    return job


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str, user: dict = User):
    return _owned_job(job_id, user)


def _job_file(job_id: str, key: str, user: dict[str, Any]) -> Path:
    job = _owned_job(job_id, user)
    if job.get("batch_lead"):                       # a part on a shared plate: the plate's files belong to its lead job
        job = store.jobs.get(job["batch_lead"]) or job
        job_id = job["id"]
    if job.get("files_deleted_at"):
        raise HTTPException(410, "The files for this job were deleted to save space. Slice the part again to get new ones.")
    if job["state"] != "done" or not job.get(key):
        raise HTTPException(409, "Not available")
    return files.path(JOBS, job_id, job[key])


@app.get("/api/jobs/{job_id}/gcode")
def job_gcode(job_id: str, user: dict = User):
    job = _owned_job(job_id, user)
    stem = Path(job["name"]).stem
    return FileResponse(_job_file(job_id, "gcode", user), media_type="text/plain", filename=f"{stem}.gcode")


@app.get("/api/jobs/{job_id}/bundle")
def job_bundle(job_id: str, user: dict = User):
    job = _owned_job(job_id, user)
    stem = Path(job["name"]).stem
    return FileResponse(_job_file(job_id, "bundle", user), filename=f"{stem}.gcode.3mf")


@app.post("/api/jobs/{job_id}/queue")
def queue_job(job_id: str, user: dict = User):
    _owned_job(job_id, user)
    return store.jobs.update(job_id, queued=True, draft=False)       # queued on a printer: it is a job now


def _remote_name(job: dict[str, Any]) -> str:
    return printing.remote_name(job)


@app.post("/api/jobs/{job_id}/print")
def print_job(job_id: str, user: dict = User):
    job = _owned_job(job_id, user)
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
    model_id: str
    filament: str
    preset: str = "0.20mm Standard"
    settings: dict[str, Any] = {}


@app.post("/api/jobs/preview")
def create_draft(body: DraftIn, user: dict = User):
    """Save a printer-agnostic job and slice a preview for it (estimate time, filament, layers). Nothing is assigned yet."""
    if body.filament not in FILAMENTS:
        raise HTTPException(400, "Unknown filament")
    if body.preset not in {p["id"] for p in PROCESS_PRESETS}:
        raise HTTPException(400, "Unknown process preset")
    bad = set(body.settings) - ALLOWED_SETTING_KEYS - SERVER_MANAGED_SETTINGS
    if bad:
        raise HTTPException(400, f"Unknown settings: {sorted(bad)}")
    settings = {k: v for k, v in body.settings.items() if k not in SERVER_MANAGED_SETTINGS}
    mesh = _transformed(body.model_id, body.rotation, body.scale, user)
    size = meshio.dims(mesh)
    if not forecast.eligible_printers(size, body.filament):
        why = sorted({b for c in assign.rank(_fleet(), size, body.filament) for b in c.blockers if b != "Printer is offline"})
        raise HTTPException(409, "No printer in the fleet can ever print this part. " + "; ".join(why))
    job_id = uuid.uuid4().hex[:12]
    files.create(JOBS, job_id)
    src = _source_file(body.model_id, user)
    if tuple(body.rotation) == (0, 0, 0) and body.scale == 1.0 and src.suffix == ".3mf":
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
        "spec": {"model_id": body.model_id, "rotation": list(body.rotation), "scale": body.scale, "filament": body.filament,
                 "preset": body.preset, "settings": settings},
    })
    jobstate.event(job_id, "phase", user["email"], **{"from": None, "to": "submitted"})
    pipeline.request_preview(job_id)
    return store.jobs.get(job_id)


@app.get("/api/jobs/{job_id}/forecast")
def job_forecast(job_id: str, pickup_by: float | None = None, user: dict = User):
    job = _owned_job(job_id, user)
    return forecast.pickup_check({**job, "submitted_at": time.time()}, pickup_by)


class SubmitIn(BaseModel):
    pickup_by: float | None = None           # epoch seconds; None = whenever
    colour: str = Field("any", max_length=40)  # recorded for later colour matching
    auto_start: bool = False                 # start printing as soon as it is sliced and the plate is clear
    allow_batching: bool = False             # may share a plate with other people's parts (they wait for a partner while there is time)


@app.post("/api/jobs/{job_id}/submit")
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
                        colour=body.colour, auto_start=body.auto_start, allow_batching=body.allow_batching, at_risk=False)
    _discard_slice_drafts(user, (job.get("spec") or {}).get("model_id"), keep=job_id)
    audit(user, "job_submit", f"{job['name']}" + (f" pickup by {time.strftime('%Y-%m-%d %H:%M', time.localtime(body.pickup_by))}" if body.pickup_by else ""))
    allocator.tick()
    return store.jobs.get(job_id)


@app.delete("/api/jobs/{job_id}")
def discard_draft(job_id: str, user: dict = User):
    """Throw away a draft (a preview or a slice that was never sent anywhere). Real jobs are cancelled, not deleted."""
    job = _owned_job(job_id, user)
    if not job.get("draft"):
        raise HTTPException(409, "Only a draft can be discarded; cancel a job instead")
    jobstate.discard(job_id)
    return {"ok": True}


@app.post("/api/jobs/{job_id}/cancel")
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


@app.get("/api/jobs/{job_id}/events")
def job_events(job_id: str, user: dict = User):
    _owned_job(job_id, user)
    return jobstate.events_for(job_id)


# ------------------------------------------------------------------- bed state (A.3)

class BedClearIn(BaseModel):
    bed_empty: bool = False
    plate_matches: bool = False
    filament_matches: bool = False


def _printer_or_404(printer_id: str) -> dict[str, Any]:
    p = store.printers.get(printer_id)
    if not p:
        raise HTTPException(404, "Unknown printer")
    return p


@app.post("/api/printers/{printer_id}/bed/clear")
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


@app.post("/api/printers/{printer_id}/bed/occupied")
def bed_occupied(printer_id: str, user: dict = User):
    _printer_or_404(printer_id)
    try:
        return bedstate.mark_occupied(printer_id, user)
    except PermissionError as e:
        raise HTTPException(403, str(e))


# ------------------------------------------------------------------- pipeline admin and statistics

@app.get("/api/admin/pipeline")
def pipeline_settings(admin: dict = Depends(require_admin)):
    return pipelinecfg.get()


@app.put("/api/admin/pipeline")
def pipeline_settings_put(body: dict[str, int], admin: dict = Depends(require_admin)):
    try:
        out = pipelinecfg.put(body)
    except ValueError as e:
        raise HTTPException(400, str(e))
    audit(admin, "pipeline_settings", str(out))
    return out


@app.get("/api/admin/stats")
def admin_stats(days: int | None = None, admin: dict = Depends(require_admin)):
    return {**stats.summary(time.time() - days * 86400 if days else None), "tasks": taskqueue.counts()}


# ------------------------------------------------------------------- static

_dist = Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"
if _dist.exists():
    app.mount("/", StaticFiles(directory=_dist, html=True), name="web")
