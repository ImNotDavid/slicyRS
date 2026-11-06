"""Server-side sessions. The browser holds only a random token; the server stores its hash and decides if it is still valid.

Compared with a signed cookie this lets signing out, disabling a user and "sign out everywhere" take effect at once, gives
an idle timeout as well as a maximum lifetime, and means a copied database is useless for impersonating anyone.
"""
import hashlib
import secrets
import time
from typing import Any

from fastapi import Request, Response

from . import store
from .config import COOKIE_SECURE, SESSION_HOURS, SESSION_IDLE_HOURS

COOKIE = "slicyrs_sid"
MAX_PER_USER = 20          # oldest sessions of a user are dropped beyond this
TOUCH_EVERY_S = 60         # last_seen is written at most once a minute


def _key(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _client(request: Request) -> tuple[str, str]:
    return (request.client.host if request.client else "unknown"), request.headers.get("user-agent", "")[:200]


def start(request: Request, response: Response, user: dict[str, Any]) -> None:
    """Begin a new session for `user` (any session the browser already had is ended: no session fixation)."""
    old = request.cookies.get(COOKIE)
    if old:
        store.sessions.delete(_key(old))
    token = secrets.token_urlsafe(32)
    now = time.time()
    ip, agent = _client(request)
    key = _key(token)
    store.sessions.put(key, {"key": key, "id": key[:16], "user_id": user["id"], "created": now, "last_seen": now,
                             "expires": now + SESSION_HOURS * 3600, "ip": ip, "agent": agent})
    response.set_cookie(COOKIE, token, max_age=SESSION_HOURS * 3600, httponly=True, secure=COOKIE_SECURE, samesite="lax", path="/")
    request.session.clear()  # the OIDC login state cookie has done its job
    _trim(user["id"])


def _trim(user_id: str) -> None:
    mine = sorted(list_user(user_id), key=lambda s: s["last_seen"])
    for s in mine[:-MAX_PER_USER]:
        store.sessions.delete(s["key"])


def current(request: Request) -> dict[str, Any] | None:
    token = request.cookies.get(COOKIE)
    if not token:
        return None
    s = store.sessions.get(_key(token))
    if not s:
        return None
    now = time.time()
    if now > s["expires"] or now - s["last_seen"] > SESSION_IDLE_HOURS * 3600:
        store.sessions.delete(s["key"])
        return None
    if now - s["last_seen"] > TOUCH_EVERY_S:
        s = store.sessions.update(s["key"], last_seen=now) or s
    return s


def end(request: Request, response: Response) -> None:
    token = request.cookies.get(COOKIE)
    if token:
        store.sessions.delete(_key(token))
    response.delete_cookie(COOKIE, path="/")


def list_user(user_id: str) -> list[dict[str, Any]]:
    now = time.time()
    return [s for s in store.sessions.all() if s["user_id"] == user_id and now <= s["expires"]]


def revoke_user(user_id: str, except_key: str | None = None) -> int:
    n = 0
    for s in store.sessions.all():
        if s["user_id"] == user_id and s["key"] != except_key:
            n += store.sessions.delete(s["key"])
    return n


def revoke_id(user_id: str, sid: str) -> bool:
    for s in list_user(user_id):
        if s["id"] == sid:
            return store.sessions.delete(s["key"])
    return False


def public(s: dict[str, Any], current_key: str | None) -> dict[str, Any]:
    return {"id": s["id"], "created": s["created"], "last_seen": s["last_seen"], "ip": s["ip"], "agent": s["agent"],
            "current": s["key"] == current_key}


def purge_expired() -> int:
    now = time.time()
    return sum(store.sessions.delete(s["key"]) for s in store.sessions.all()
               if now > s["expires"] or now - s["last_seen"] > SESSION_IDLE_HOURS * 3600)
