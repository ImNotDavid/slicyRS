"""Sign-in (Imperial College SSO via OpenID Connect / Microsoft Entra ID), sessions, roles and invites.

Access is invite-only: a user must already exist (invited by an admin, or listed in ADMIN_EMAILS)
before an SSO login is accepted. Imperial identities can differ between the `email` claim
(first.last@imperial.ac.uk) and the UPN (abc123@ic.ac.uk), so any of the token's name claims may match.
"""
import hashlib
import hmac
import re
import secrets
import threading
import time
import uuid
from collections import defaultdict, deque
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from . import mailer, sessions, store
from .config import (ADMIN_EMAILS, ALLOWED_EMAIL_DOMAINS, APP_NAME, CODE_TTL_MIN, DEV_AUTH, EMAIL_LOGIN, OIDC_CLIENT_ID,
                     OIDC_CLIENT_SECRET, OIDC_ISSUER, OIDC_TENANT_ID, PUBLIC_URL, SECRET_KEY, SSO_ENABLED, SSO_NAME)

router = APIRouter()
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


# ------------------------------------------------------------------ users

def norm_email(value: str | None) -> str | None:
    v = (value or "").strip().lower()
    return v if _EMAIL.match(v) else None


def domain_ok(email: str) -> bool:
    return email.rsplit("@", 1)[1] in ALLOWED_EMAIL_DOMAINS


def find_user_by_email(email: str) -> dict[str, Any] | None:
    return next((u for u in store.users.all() if email in u.get("emails", [u["email"]])), None)


def audit(actor: dict[str, Any] | None, action: str, detail: str = "") -> None:
    aid = uuid.uuid4().hex
    store.audit.put(aid, {"id": aid, "at": time.time(), "actor": actor["email"] if actor else "system",
                          "action": action, "detail": detail})


def create_user(email: str, role: str = "user", name: str = "", invited_by: str | None = None) -> dict[str, Any]:
    uid = uuid.uuid4().hex[:12]
    rec = {"id": uid, "email": email, "emails": [email], "name": name, "role": role, "active": True,
           "invited_by": invited_by, "created": time.time(), "last_login": None, "sub": None}
    return store.users.put(uid, rec)


def public_user(u: dict[str, Any]) -> dict[str, Any]:
    return {k: u.get(k) for k in ("id", "email", "name", "role", "active", "operator", "invited_by", "created", "last_login")}


def resolve_login(candidates: list[str | None], sub: str | None, name: str) -> dict[str, Any]:
    """Map identity claims to an existing (invited) user, or bootstrap a listed admin. Raises 403 otherwise."""
    emails = [e for e in (norm_email(c) for c in candidates) if e]
    if not emails:
        raise HTTPException(403, "no_email")
    user = next((u for u in store.users.all() if sub and u.get("sub") == sub), None) \
        or next((u for e in emails if (u := find_user_by_email(e))), None)
    if user is None:
        admin_hit = next((e for e in emails if e in ADMIN_EMAILS), None)
        if not admin_hit:
            raise HTTPException(403, "not_invited")
        user = create_user(admin_hit, "admin", name)
        audit(None, "bootstrap_admin", admin_hit)
    if not user.get("active", True):
        raise HTTPException(403, "disabled")
    if not any(domain_ok(e) for e in emails) and user["email"] not in ADMIN_EMAILS:
        raise HTTPException(403, "domain_not_allowed")
    fields: dict[str, Any] = {"last_login": time.time(), "emails": sorted(set(user.get("emails", []) + emails))}
    if sub and not user.get("sub"):
        fields["sub"] = sub
    if name and not user.get("name"):
        fields["name"] = name
    return store.users.update(user["id"], **fields)  # type: ignore[return-value]


# -------------------------------------------------------------- dependencies

def current_user(request: Request) -> dict[str, Any]:
    sess = sessions.current(request)
    user = store.users.get(sess["user_id"]) if sess else None
    if not user or not user.get("active", True):
        if sess:
            store.sessions.delete(sess["key"])  # a session of a disabled or deleted user is dead for good
        raise HTTPException(401, "Not signed in")
    request.state.session = sess
    return user


def require_admin(user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    from . import permissions
    if not permissions.can(user, "admin"):
        raise HTTPException(403, "Admin only")
    return user


def is_admin(user: dict[str, Any]) -> bool:
    from . import permissions
    return permissions.is_admin(user)


# ------------------------------------------------------------------- routes

_oauth = None


def configure_sso(issuer: str, client_id: str, client_secret: str) -> None:
    """(Re)register the OpenID Connect provider. Called at import when OIDC_* is set; tests point it at a mock provider."""
    global _oauth
    from authlib.integrations.starlette_client import OAuth
    oauth = OAuth()
    oauth.register("sso", server_metadata_url=f"{issuer.rstrip('/')}/.well-known/openid-configuration",
                   client_id=client_id, client_secret=client_secret,
                   client_kwargs={"scope": "openid email profile", "code_challenge_method": "S256"})
    _oauth = oauth


if SSO_ENABLED:
    configure_sso(OIDC_ISSUER, OIDC_CLIENT_ID, OIDC_CLIENT_SECRET)


@router.get("/auth/config")
def auth_config():
    return {"sso": SSO_ENABLED, "sso_name": SSO_NAME, "dev": DEV_AUTH, "email_login": EMAIL_LOGIN}


@router.get("/auth/login")
async def login(request: Request):
    if _oauth is None:
        raise HTTPException(404, "SSO is not configured")
    return await _oauth.sso.authorize_redirect(request, f"{PUBLIC_URL}/auth/callback")


@router.get("/auth/callback")
async def callback(request: Request):
    if _oauth is None:
        raise HTTPException(404, "SSO is not configured")
    try:
        token = await _oauth.sso.authorize_access_token(request)
    except Exception:
        return RedirectResponse("/?login_error=sso_failed")
    claims = dict(token.get("userinfo") or {})
    if OIDC_TENANT_ID and claims.get("tid") != OIDC_TENANT_ID:
        audit(None, "login_rejected", f"wrong tenant for {claims.get('preferred_username')}")
        return RedirectResponse("/?login_error=wrong_tenant")
    try:
        user = resolve_login([claims.get("email"), claims.get("preferred_username"), claims.get("upn")],
                             claims.get("oid") or claims.get("sub"), claims.get("name", ""))
    except HTTPException as e:
        seen = ", ".join(f"{k}={claims[k]}" for k in ("email", "preferred_username", "upn") if claims.get(k))
        audit(None, "login_rejected", f"{seen or 'no name claims'}: {e.detail}")
        return RedirectResponse(f"/?login_error={e.detail}")
    # record which identity claims arrived: invaluable when matching invites to a real tenant for the first time
    seen = ", ".join(f"{k}={claims[k]}" for k in ("email", "preferred_username", "upn") if claims.get(k))
    audit(user, "login", f"sso: {seen or 'no name claims'}")
    redirect = RedirectResponse("/")
    sessions.start(request, redirect, user)
    return redirect


class DevLogin(BaseModel):
    email: str


@router.post("/auth/dev-login")
def dev_login(body: DevLogin, request: Request, response: Response):
    if not DEV_AUTH:
        raise HTTPException(404, "Not found")
    user = resolve_login([body.email], None, "")
    sessions.start(request, response, user)
    audit(user, "login", "dev")
    return public_user(user)


@router.post("/auth/logout")
def logout(request: Request, response: Response):
    sessions.end(request, response)
    request.session.clear()
    return {"ok": True}


@router.post("/auth/logout-all")
def logout_all(request: Request, response: Response, user: dict[str, Any] = Depends(current_user)):
    """Sign out on every device, including this one."""
    n = sessions.revoke_user(user["id"])
    sessions.end(request, response)
    audit(user, "logout_all", f"{n} session(s)")
    return {"ok": True, "ended": n}


@router.get("/api/me/sessions")
def my_sessions(request: Request, user: dict[str, Any] = Depends(current_user)):
    cur = request.state.session["key"]
    return sorted((sessions.public(s, cur) for s in sessions.list_user(user["id"])), key=lambda s: -s["last_seen"])


@router.delete("/api/me/sessions/{sid}")
def end_session(sid: str, user: dict[str, Any] = Depends(current_user)):
    if not sessions.revoke_id(user["id"], sid):
        raise HTTPException(404, "Unknown session")
    return {"ok": True}


@router.get("/api/me")
def me(user: dict[str, Any] = Depends(current_user)):
    return public_user(user)


# ------------------------------------------------- emailed one-time login codes

MAX_CODE_ATTEMPTS = 5
_code_lock = threading.Lock()


def _rate_ok(key: str, limit: int, window_s: int = 900) -> bool:
    """Sliding-window limiter stored in the database, so a restart does not give an attacker a fresh allowance."""
    return store.rate_hit(key, limit, window_s)



def _code_hash(email: str, code: str) -> str:
    return hmac.new(SECRET_KEY.encode(), f"{email}:{code}".encode(), hashlib.sha256).hexdigest()


def eligible_for_email_login(email: str) -> bool:
    """Invited, active and in an allowed domain, or a configured bootstrap admin."""
    if email in ADMIN_EMAILS:
        return True
    user = find_user_by_email(email)
    return bool(user and user.get("active", True) and domain_ok(email))


class EmailRequest(BaseModel):
    email: str


class EmailVerify(BaseModel):
    email: str
    code: str


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


@router.post("/auth/email/request")
def email_request(body: EmailRequest, request: Request):
    """Send a one-time code to an invited address. The response is identical for every address, invited or not."""
    if not EMAIL_LOGIN:
        raise HTTPException(404, "Email sign-in is not enabled")
    email = norm_email(body.email)
    if not email:
        raise HTTPException(400, "Enter a valid email address")
    if not (_rate_ok(f"req-ip:{_client_ip(request)}", 20) and _rate_ok(f"req-email:{email}", 5)):
        raise HTTPException(429, "Too many requests. Please wait a few minutes and try again.")
    if eligible_for_email_login(email):
        code = f"{secrets.randbelow(10 ** 6):06d}"
        with _code_lock:
            store.codes.put(email, {"hash": _code_hash(email, code), "expires": time.time() + CODE_TTL_MIN * 60,
                                    "attempts": 0, "created": time.time()})
        mailer.send_in_background(
            email, f"Your {APP_NAME} sign-in code: {code}",
            f"Your {APP_NAME} sign-in code is {code}\n\nIt expires in {CODE_TTL_MIN} minutes and works once. "
            "If you did not ask for it, ignore this email: nobody can sign in without it.\n",
            on_error=lambda e: audit(None, "login_code_send_failed", f"{email}: {type(e).__name__}"))
        audit(None, "login_code_sent", email)
    return {"ok": True, "ttl_minutes": CODE_TTL_MIN}


@router.post("/auth/email/verify")
def email_verify(body: EmailVerify, request: Request, response: Response):
    if not EMAIL_LOGIN:
        raise HTTPException(404, "Email sign-in is not enabled")
    invalid = HTTPException(401, "That code is wrong or has expired. Request a new one.")
    email = norm_email(body.email)
    code = re.sub(r"\s+", "", body.code or "")
    if not email or not _rate_ok(f"ver-ip:{_client_ip(request)}", 40):
        raise HTTPException(429, "Too many attempts. Please wait a few minutes.") if email else invalid
    with _code_lock:
        rec = store.codes.get(email)
        if not rec or time.time() > rec["expires"] or rec["attempts"] >= MAX_CODE_ATTEMPTS:
            store.codes.delete(email)
            raise invalid
        rec["attempts"] += 1
        good = hmac.compare_digest(rec["hash"], _code_hash(email, code))
        if good or rec["attempts"] >= MAX_CODE_ATTEMPTS:
            store.codes.delete(email)             # single use; too many wrong guesses burns the code
        else:
            store.codes.put(email, rec)
    if not good:
        audit(None, "login_code_wrong", email)
        raise invalid
    try:
        user = resolve_login([email], None, "")
    except HTTPException:
        raise invalid                              # disabled/removed since the code was sent: same generic answer
    sessions.start(request, response, user)
    audit(user, "login", "email code")
    return public_user(user)
