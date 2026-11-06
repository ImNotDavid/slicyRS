"""Server-side sessions: opaque cookie, hashed at rest, revocable, idle and absolute timeouts; rate limits that persist."""
import hashlib
import time
import uuid

import pytest
from fastapi.testclient import TestClient

from app import main as app_main, sessions, store
from app.main import app
from conftest import ADMIN, make_user


def email(prefix="s"):
    return f"{prefix}{uuid.uuid4().hex[:6]}@imperial.ac.uk"


@pytest.fixture()
def user_client(client):
    e = email()
    return e, make_user(client, e)


# ---------------------------------------------------------------- the cookie

def test_cookie_is_opaque_http_only_and_not_a_signed_payload(client):
    e = email()
    client.post("/api/admin/users", json={"email": e})
    c = TestClient(app)
    r = c.post("/auth/dev-login", json={"email": e})
    cookie = r.headers["set-cookie"]
    assert cookie.startswith("slicyrs_sid=") and "httponly" in cookie.lower() and "samesite=lax" in cookie.lower()
    token = c.cookies.get(sessions.COOKIE)
    assert len(token) >= 40 and "." not in token and e not in token and "uid" not in token      # no readable content at all


def test_only_a_hash_of_the_token_is_stored(user_client):
    _, c = user_client
    token = c.cookies.get(sessions.COOKIE)
    assert store.sessions.get(hashlib.sha256(token.encode()).hexdigest())
    assert token not in str(store.sessions.all())


def test_cookie_is_secure_over_https(client, monkeypatch):
    monkeypatch.setattr(sessions, "COOKIE_SECURE", True)
    e = email()
    client.post("/api/admin/users", json={"email": e})
    assert "secure" in TestClient(app).post("/auth/dev-login", json={"email": e}).headers["set-cookie"].lower()


def test_a_forged_or_old_style_cookie_is_useless():
    c = TestClient(app)
    c.cookies.set(sessions.COOKIE, "A" * 43)
    assert c.get("/api/me").status_code == 401
    c.cookies.set("slicyrs_session", "eyJ1aWQiOiAiZmFrZSJ9.xxxx.yyyy")       # the previous signed-cookie format
    assert c.get("/api/me").status_code == 401


# ------------------------------------------------------------------- lifetime

def test_logout_kills_the_session_server_side(user_client):
    _, c = user_client
    stolen = c.cookies.get(sessions.COOKIE)
    assert c.get("/api/me").status_code == 200
    c.post("/auth/logout")
    thief = TestClient(app)
    thief.cookies.set(sessions.COOKIE, stolen)                                  # a copy of the cookie taken before logout
    assert thief.get("/api/me").status_code == 401


def test_idle_timeout(user_client):
    _, c = user_client
    key = hashlib.sha256(c.cookies.get(sessions.COOKIE).encode()).hexdigest()
    store.sessions.update(key, last_seen=time.time() - 9 * 3600)                 # idle limit is 8 hours
    assert c.get("/api/me").status_code == 401
    assert store.sessions.get(key) is None


def test_absolute_lifetime(user_client):
    _, c = user_client
    key = hashlib.sha256(c.cookies.get(sessions.COOKIE).encode()).hexdigest()
    store.sessions.update(key, expires=time.time() - 1)
    assert c.get("/api/me").status_code == 401


def test_activity_keeps_a_session_alive(user_client):
    _, c = user_client
    key = hashlib.sha256(c.cookies.get(sessions.COOKIE).encode()).hexdigest()
    store.sessions.update(key, last_seen=time.time() - 7 * 3600)
    assert c.get("/api/me").status_code == 200
    assert time.time() - store.sessions.get(key)["last_seen"] < 5


def test_new_login_replaces_the_browsers_old_session(client):
    e = email()
    client.post("/api/admin/users", json={"email": e})
    c = TestClient(app)
    c.post("/auth/dev-login", json={"email": e})
    first = c.cookies.get(sessions.COOKIE)
    c.post("/auth/dev-login", json={"email": e})
    assert c.cookies.get(sessions.COOKIE) != first                              # new token at every login (no fixation)
    old = TestClient(app)
    old.cookies.set(sessions.COOKIE, first)
    assert old.get("/api/me").status_code == 401


def test_a_user_keeps_at_most_twenty_sessions(client):
    e = email()
    client.post("/api/admin/users", json={"email": e})
    for _ in range(sessions.MAX_PER_USER + 5):
        TestClient(app).post("/auth/dev-login", json={"email": e})
    uid = next(u["id"] for u in client.get("/api/admin/users").json() if u["email"] == e)
    assert len(sessions.list_user(uid)) == sessions.MAX_PER_USER


# ------------------------------------------------------------------ revocation

def test_disabling_a_user_ends_their_session_immediately_and_for_good(client, user_client):
    e, c = user_client
    uid = next(u["id"] for u in client.get("/api/admin/users").json() if u["email"] == e)
    assert c.get("/api/me").status_code == 200
    client.patch(f"/api/admin/users/{uid}", json={"active": False})
    assert c.get("/api/me").status_code == 401
    client.patch(f"/api/admin/users/{uid}", json={"active": True})
    assert c.get("/api/me").status_code == 401                                   # re-enabling does not resurrect the old session
    assert TestClient(app).post("/auth/dev-login", json={"email": e}).status_code == 200


def test_deleting_a_user_removes_their_sessions(client, user_client):
    e, c = user_client
    uid = next(u["id"] for u in client.get("/api/admin/users").json() if u["email"] == e)
    client.delete(f"/api/admin/users/{uid}")
    assert c.get("/api/me").status_code == 401 and not sessions.list_user(uid)


def test_sign_out_everywhere(user_client):
    e, laptop = user_client
    phone = TestClient(app)
    phone.post("/auth/dev-login", json={"email": e})
    assert len(laptop.get("/api/me/sessions").json()) == 2
    r = laptop.post("/auth/logout-all")
    assert r.json()["ended"] == 2
    assert laptop.get("/api/me").status_code == 401 and phone.get("/api/me").status_code == 401


def test_list_and_end_a_single_session(user_client):
    e, laptop = user_client
    phone = TestClient(app)
    phone.post("/auth/dev-login", json={"email": e}, headers={"User-Agent": "PhoneBrowser/1"})
    listing = laptop.get("/api/me/sessions").json()
    assert len(listing) == 2 and sum(s["current"] for s in listing) == 1
    other = next(s for s in listing if not s["current"])
    assert other["agent"] == "PhoneBrowser/1" and "key" not in other                # the secret hash is never exposed
    assert laptop.delete(f"/api/me/sessions/{other['id']}").status_code == 200
    assert phone.get("/api/me").status_code == 401 and laptop.get("/api/me").status_code == 200
    assert laptop.delete(f"/api/me/sessions/{other['id']}").status_code == 404


def test_users_cannot_end_each_others_sessions(client, user_client):
    _, mine = user_client
    make_user(client, email("o"))
    sid = client.get("/api/me/sessions").json()[0]["id"]                         # an admin session id
    assert mine.delete(f"/api/me/sessions/{sid}").status_code == 404


def test_admin_can_sign_a_user_out(client, user_client):
    e, c = user_client
    uid = next(u["id"] for u in client.get("/api/admin/users").json() if u["email"] == e)
    assert client.delete(f"/api/admin/users/{uid}/sessions").json()["ended"] >= 1
    assert c.get("/api/me").status_code == 401
    assert c.delete(f"/api/admin/users/{uid}/sessions").status_code in (401, 403)
    assert client.delete("/api/admin/users/nope/sessions").status_code == 404
    assert any(a["action"] == "sessions_revoked" for a in client.get("/api/admin/audit?limit=20").json())


def test_expired_sessions_are_purged():
    uid = "purge-test"
    now = time.time()
    store.sessions.put("a" * 64, {"key": "a" * 64, "id": "a" * 16, "user_id": uid, "created": now, "last_seen": now, "expires": now - 10, "ip": "", "agent": ""})
    store.sessions.put("b" * 64, {"key": "b" * 64, "id": "b" * 16, "user_id": uid, "created": now, "last_seen": now, "expires": now + 3600, "ip": "", "agent": ""})
    assert sessions.purge_expired() >= 1
    assert store.sessions.get("a" * 64) is None and store.sessions.get("b" * 64)
    store.sessions.delete("b" * 64)


# -------------------------------------------------------- rate limits persist

def test_rate_limit_counts_survive_a_restart():
    store.rate_reset()
    assert all(store.rate_hit("k", 3, 60) for _ in range(3)) and not store.rate_hit("k", 3, 60)
    # a restart re-opens the database: the counters live there, not in memory
    import sqlite3
    from app.store import _DB
    assert sqlite3.connect(_DB).execute("SELECT COUNT(*) FROM ratelimit WHERE key='k'").fetchone()[0] == 3
    assert not store.rate_hit("k", 3, 60)


def test_rate_limit_window_expires_and_keys_are_independent():
    store.rate_reset()
    assert store.rate_hit("w", 1, 1) and not store.rate_hit("w", 1, 1)
    time.sleep(1.1)
    assert store.rate_hit("w", 1, 1)
    assert store.rate_hit("other", 1, 60)


def test_email_code_limit_still_holds_through_the_database(client):
    store.rate_reset()
    anon = TestClient(app)
    e = email("lim")
    codes = [anon.post("/auth/email/request", json={"email": e}).status_code for _ in range(7)]
    assert codes[:5] == [200] * 5 and codes[5:] == [429, 429]
