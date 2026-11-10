"""Admin: users, e-mail test, storage and retention, audit log, pipeline settings and statistics."""
import time

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .. import auth, pipelinecfg, retention, sessions, stats, store, taskqueue
from ..auth import audit, require_admin
from ..config import ALLOWED_EMAIL_DOMAINS

router = APIRouter()


class InviteIn(BaseModel):
    email: str
    name: str = ""
    role: str = "user"


@router.get("/api/admin/users")
def list_users(admin: dict = Depends(require_admin)):
    return [auth.public_user(u) for u in store.users.all()]


@router.post("/api/admin/users")
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


@router.patch("/api/admin/users/{user_id}")
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


@router.delete("/api/admin/users/{user_id}")
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


@router.post("/api/admin/email/test")
def email_test(admin: dict = Depends(require_admin)):
    """Send a test message to the admin's own address so SMTP problems show up here, not as silent login failures."""
    from .. import mailer
    try:
        mailer.send(admin["email"], f"{auth.APP_NAME} test email", "SMTP is working: login codes will reach people.\n")
    except Exception as e:
        raise HTTPException(502, f"Could not send: {type(e).__name__}: {e}")
    audit(admin, "email_test", admin["email"])
    return {"ok": True, "to": admin["email"]}


@router.delete("/api/admin/users/{user_id}/sessions")
def revoke_user_sessions(user_id: str, admin: dict = Depends(require_admin)):
    target = store.users.get(user_id)
    if not target:
        raise HTTPException(404, "Unknown user")
    n = sessions.revoke_user(user_id)
    audit(admin, "sessions_revoked", f"{target['email']}: {n} session(s)")
    return {"ok": True, "ended": n}


@router.get("/api/admin/storage")
def storage_usage(admin: dict = Depends(require_admin)):
    return retention.usage()


class RetentionIn(BaseModel):
    job_files_days: int | None = Field(None, ge=0, le=3650)
    failed_job_days: int | None = Field(None, ge=0, le=3650)
    model_files_days: int | None = Field(None, ge=0, le=3650)


@router.put("/api/admin/storage/settings")
def storage_settings(body: RetentionIn, admin: dict = Depends(require_admin)):
    new = retention.set_settings({k: v for k, v in body.model_dump().items() if v is not None})
    audit(admin, "storage_settings", str(new))
    return new


@router.post("/api/admin/storage/cleanup")
def storage_cleanup(dry_run: bool = False, admin: dict = Depends(require_admin)):
    report = retention.run(dry_run=dry_run)
    if not dry_run:
        audit(admin, "storage_cleanup_manual", f"{report['jobs_cleaned']} jobs, {report['models_cleaned']} models")
    return report


@router.get("/api/admin/audit")
def audit_log(limit: int = 100, admin: dict = Depends(require_admin)):
    return sorted(store.audit.all(), key=lambda a: -a["at"])[: max(1, min(limit, 500))]



@router.get("/api/admin/pipeline")
def pipeline_settings(admin: dict = Depends(require_admin)):
    return pipelinecfg.get()


@router.put("/api/admin/pipeline")
def pipeline_settings_put(body: dict[str, int], admin: dict = Depends(require_admin)):
    try:
        out = pipelinecfg.put(body)
    except ValueError as e:
        raise HTTPException(400, str(e))
    audit(admin, "pipeline_settings", str(out))
    return out


@router.get("/api/admin/stats")
def admin_stats(days: int | None = None, admin: dict = Depends(require_admin)):
    return {**stats.summary(time.time() - days * 86400 if days else None), "tasks": taskqueue.counts()}
