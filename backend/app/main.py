import logging
import os
import re
import shutil
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

from . import assign, auth, crypto, meshio, outcomes, retention, sessions, slicer, store
from .auth import audit, current_user, is_admin, require_admin
from .bambu import PrinterError, check_host, links, probe
from .defaults import effective_overrides, ui_defaults
from .catalog import (ALLOWED_SETTING_KEYS, BED_TYPES, DEFAULT_BED_TYPE, FILAMENTS, PRINTER_MODELS, PROCESS_PRESETS,
                      SETTINGS_SCHEMA, preset_defaults)
from .config import (ALLOWED_EMAIL_DOMAINS, COOKIE_SECURE, JOBS_DIR, MAX_UPLOAD_MB, MAX_WORKERS, MODELS_DIR, ORCA_BIN,
                     SECRET_KEY, SESSION_HOURS, SLICER_MODE)

app = FastAPI(title="slicyRS")
pool = ThreadPoolExecutor(max_workers=MAX_WORKERS)
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY, https_only=COOKIE_SECURE, same_site="lax",
                   max_age=SESSION_HOURS * 3600, session_cookie="slicyrs_session")
app.include_router(auth.router)
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
           "bed_type": p.get("bed_type") or DEFAULT_BED_TYPE,
           "queue_depth": _queue_depth(p["id"]), "live": bool(p.get("host"))}
    if admin:
        out.update(host=p.get("host"), port=p.get("port") or 8883, serial=p.get("serial"),
                   has_access_code=bool(p.get("access_code")), demo=p.get("demo", False))
    return out


def _queue_depth(printer_id: str) -> int:
    return sum(1 for j in store.jobs.all()
               if j.get("printer_id") == printer_id and j.get("queued") and not j.get("printed"))


def _fleet() -> list[dict[str, Any]]:
    return [{**p, "queue_depth": _queue_depth(p["id"])} for p in store.printers.all() if p.get("enabled", True)]


def _model_dir(model_id: str, user: dict[str, Any]) -> Path:
    """Models belong to their uploader; other users get the same 404 as a missing model."""
    rec = store.models.get(model_id)
    d = MODELS_DIR / model_id
    if rec and rec.get("files_deleted_at") and (rec["owner_id"] == user["id"] or is_admin(user)):
        raise HTTPException(410, "This model was removed after not being used for a while. Please upload it again.")
    if not rec or not d.is_dir() or (rec["owner_id"] != user["id"] and not is_admin(user)):
        raise HTTPException(404, "Unknown model")
    retention.touch_model(model_id)
    return d


def _source_file(model_id: str, user: dict[str, Any]) -> Path:
    d = _model_dir(model_id, user)
    return next(f for f in d.iterdir() if f.name.startswith("source"))


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
    d = MODELS_DIR / model_id
    d.mkdir()
    src = d / f"source{ext}"
    src.write_bytes(data)
    (d / "name.txt").write_text(file.filename or "model", encoding="utf-8")
    try:
        mesh = meshio.load_mesh(src)
    except Exception as e:
        shutil.rmtree(d, ignore_errors=True)
        raise HTTPException(400, f"Could not read model: {e}")
    meshio.export_stl(mesh, d / "preview.stl")
    store.models.put(model_id, {"id": model_id, "owner_id": user["id"], "name": file.filename, "created": time.time()})
    return {"id": model_id, "name": file.filename, "dims": meshio.dims(mesh),
            "triangles": int(len(mesh.faces))}


@app.get("/api/models/{model_id}/preview.stl")
def preview(model_id: str, user: dict = User):
    return FileResponse(_model_dir(model_id, user) / "preview.stl", media_type="model/stl")


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
    status: dict[str, Any] | None = None


def _validate_printer(body: PrinterIn) -> None:
    if body.model not in PRINTER_MODELS:
        raise HTTPException(400, "Unknown printer model")
    if body.bed_type not in BED_TYPES:
        raise HTTPException(400, f"Unknown build plate. Choose one of: {', '.join(BED_TYPES)}")
    if body.host:
        try:
            check_host(body.host)
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


@app.post("/api/admin/printers/test")
def test_printer(body: ProbeIn, admin: dict = Depends(require_admin)):
    code = body.access_code
    if not code and body.printer_id:
        code = crypto.unseal((store.printers.get(body.printer_id) or {}).get("access_code"))
    if not code:
        raise HTTPException(400, "An access code is required")
    try:
        result = probe(body.host, body.port, body.serial, code)
    except ValueError as e:
        raise HTTPException(400, str(e))
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
    workdir = JOBS_DIR / job_id
    workdir.mkdir()
    src = _source_file(body.model_id, user)
    untransformed = tuple(body.rotation) == (0, 0, 0) and body.scale == 1.0
    if untransformed and src.suffix == ".3mf":
        input_path = workdir / "input.3mf"
        input_path.write_bytes(src.read_bytes())
    else:
        input_path = workdir / "input.stl"
        meshio.export_stl(mesh, input_path)

    printer = store.printers.get(chosen.printer_id) or {}
    name = (MODELS_DIR / body.model_id / "name.txt").read_text(encoding="utf-8")
    store.jobs.put(job_id, {
        "id": job_id, "name": name, "state": "slicing", "created": time.time(), "owner_id": user["id"],
        "owner_email": user["email"],
        "printer_id": chosen.printer_id, "printer_name": printer.get("name"), "printer_model": chosen.model,
        "filament": body.filament, "preset": body.preset, "dims": size, "candidates": [c.asdict() for c in ranked],
        "loaded_slot": chosen.loaded_slot, "queued": False, "printed": False,
        "bed_type": printer.get("bed_type") or DEFAULT_BED_TYPE,
    })
    pool.submit(_do_slice, job_id, workdir, input_path, chosen.model, body, printer.get("bed_type") or DEFAULT_BED_TYPE)
    return store.jobs.get(job_id)


def _do_slice(job_id: str, workdir: Path, input_path: Path, printer_model: str, body: SliceIn, bed_type: str) -> None:
    # only what the user changed goes to the slicer, so the printer's own profile stays in charge of everything else;
    # the plate is fixed by the assigned printer, so the sliced file always matches what is physically installed
    overrides = effective_overrides(body.preset, body.settings, bed_type)
    try:
        result = slicer.run(workdir, input_path, printer_model, body.preset, body.filament, overrides)
        store.jobs.update(job_id, state="done", stats=result, **{"gcode": result["gcode"], "bundle": result.get("bundle")})
    except Exception as e:  # surfaced to the UI as the job error
        store.jobs.update(job_id, state="failed", error=str(e))


@app.get("/api/jobs")
def list_jobs(user: dict = User):
    """Your jobs; admins see everyone's."""
    jobs = [j for j in store.jobs.all() if is_admin(user) or j.get("owner_id") == user["id"]]
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
    if job.get("files_deleted_at"):
        raise HTTPException(410, "The files for this job were deleted to save space. Slice the part again to get new ones.")
    if job["state"] != "done" or not job.get(key):
        raise HTTPException(409, "Not available")
    return JOBS_DIR / job_id / job[key]


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
    return store.jobs.update(job_id, queued=True)


def _remote_name(job: dict[str, Any]) -> str:
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(job["name"]).stem).strip("._")[:40] or "part"
    return f"{stem}-{job['id'][:6]}.gcode.3mf"  # unique per job so prints never overwrite each other on the SD card


@app.post("/api/jobs/{job_id}/print")
def print_job(job_id: str, user: dict = User):
    job = _owned_job(job_id, user)
    bundle = _job_file(job_id, "bundle", user)
    printer = store.printers.get(job["printer_id"])
    if not printer:
        raise HTTPException(409, "That printer no longer exists")
    if not printer.get("enabled", True):
        raise HTTPException(409, "That printer has been disabled by an admin")
    link = links.get(job["printer_id"])
    if link is None:
        raise HTTPException(409, "This printer has no live connection (an admin must add its IP, serial and access code); "
                                 "download the file and print from SD instead")
    try:
        remote = _remote_name(job)
        started = link.send_print(bundle, remote)
    except PrinterError as e:
        audit(user, "print_failed", f"{job['name']} -> {job['printer_name']}: {e}")
        raise HTTPException(502, str(e))
    except Exception as e:  # never leak a bare 500 for a printer problem
        logging.getLogger("slicyrs").exception("print failed")
        audit(user, "print_failed", f"{job['name']} -> {job['printer_name']}: {type(e).__name__}")
        raise HTTPException(502, f"Unexpected error talking to the printer: {type(e).__name__}: {e}")
    store.jobs.update(job_id, queued=True, printed=True, remote_name=remote, started_at=time.time(), result=None)
    audit(user, "print", f"{job['name']} -> {job['printer_name']}")
    return {"ok": True, "started": started, "file": remote}


# ------------------------------------------------------------------- static

_dist = Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"
if _dist.exists():
    app.mount("/", StaticFiles(directory=_dist, html=True), name="web")
