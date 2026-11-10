"""Sign-in, invites, roles, data isolation and admin-only printer configuration."""
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app import auth, crypto, main as app_main, store
from app.main import app
from conftest import ADMIN, VALID, make_user, upload


@pytest.fixture()
def anon():
    return TestClient(app)


@pytest.fixture()
def alice(client):
    return make_user(client, "alice@imperial.ac.uk")


@pytest.fixture()
def bob(client):
    return make_user(client, "bob@ic.ac.uk")


def new_email(prefix):
    import uuid
    return f"{prefix}{uuid.uuid4().hex[:6]}@imperial.ac.uk"


# ------------------------------------------------------------ unauthenticated

PROTECTED = [("get", "/api/catalog"), ("get", "/api/printers"), ("get", "/api/jobs"), ("get", "/api/me"),
             ("get", "/api/jobs/x"), ("get", "/api/models/x/preview.stl"), ("post", "/api/slice"),
             ("post", "/api/assign"), ("post", "/api/models"), ("get", "/api/admin/users"),
             ("get", "/api/admin/audit"), ("post", "/api/admin/printers"), ("post", "/api/admin/users")]


@pytest.mark.parametrize("method,path", PROTECTED)
def test_requires_sign_in(anon, method, path):
    assert getattr(anon, method)(path).status_code == 401


def test_public_endpoints(anon):
    assert anon.get("/api/health").status_code == 200
    cfg = anon.get("/auth/config").json()
    assert set(cfg) == {"sso", "sso_name", "dev", "email_login"} and cfg["dev"] is True


# ------------------------------------------------------------------- sign in

def test_uninvited_email_rejected(anon):
    r = anon.post("/auth/dev-login", json={"email": new_email("stranger")})
    assert r.status_code == 403 and r.json()["detail"] == "not_invited"
    assert anon.get("/api/me").status_code == 401


def test_invited_user_signs_in_and_out(client, anon):
    email = new_email("carol")
    assert client.post("/api/admin/users", json={"email": email, "name": "Carol"}).status_code == 200
    assert anon.post("/auth/dev-login", json={"email": email.upper()}).status_code == 200  # case-insensitive
    me = anon.get("/api/me").json()
    assert me["email"] == email and me["role"] == "user" and me["last_login"]
    anon.post("/auth/logout")
    assert anon.get("/api/me").status_code == 401


def test_bootstrap_admin_from_env(anon):
    r = anon.post("/auth/dev-login", json={"email": ADMIN})
    assert r.status_code == 200 and r.json()["role"] == "admin"


def test_disabled_user_cannot_sign_in_or_keep_session(client):
    email = new_email("dave")
    uid = client.post("/api/admin/users", json={"email": email}).json()["id"]
    c = TestClient(app)
    assert c.post("/auth/dev-login", json={"email": email}).status_code == 200
    assert client.patch(f"/api/admin/users/{uid}", json={"active": False}).status_code == 200
    assert c.get("/api/me").status_code == 401                       # live session is cut off
    assert TestClient(app).post("/auth/dev-login", json={"email": email}).status_code == 403
    client.patch(f"/api/admin/users/{uid}", json={"active": True})
    assert c.post("/auth/dev-login", json={"email": email}).status_code == 200


def test_dev_login_disabled_when_flag_off(anon, monkeypatch):
    monkeypatch.setattr(auth, "DEV_AUTH", False)
    assert anon.post("/auth/dev-login", json={"email": ADMIN}).status_code == 404


# ------------------------------------------------------------ invites & roles

def test_invite_validation(client):
    bad = lambda **kw: client.post("/api/admin/users", json=kw)
    assert bad(email="not-an-email").status_code == 400
    assert bad(email="someone@gmail.com").status_code == 400          # outside allowed domains
    assert bad(email=new_email("x"), role="superuser").status_code == 400
    dup = new_email("dup")
    assert bad(email=dup).status_code == 200 and bad(email=dup).status_code == 409


def test_non_admin_cannot_use_admin_api(alice):
    assert alice.get("/api/admin/users").status_code == 403
    assert alice.post("/api/admin/users", json={"email": new_email("e")}).status_code == 403
    assert alice.get("/api/admin/audit").status_code == 403
    assert alice.post("/api/admin/printers", json={"name": "p", "model": "P1S"}).status_code == 403
    assert alice.put("/api/admin/printers/x", json={"name": "p", "model": "P1S"}).status_code == 403
    assert alice.delete("/api/admin/printers/x").status_code == 403
    assert alice.post("/api/admin/printers/test", json={"host": "10.0.0.5", "serial": "S", "access_code": "1"}).status_code == 403
    assert alice.patch("/api/admin/users/x", json={"role": "admin"}).status_code == 403


def test_cannot_remove_last_admin(client):
    me = next(u for u in client.get("/api/admin/users").json() if u["email"] == ADMIN)
    others = [u for u in client.get("/api/admin/users").json() if u["role"] == "admin" and u["active"] and u["id"] != me["id"]]
    for u in others:  # make this the only active admin
        client.patch(f"/api/admin/users/{u['id']}", json={"role": "user"})
    assert client.patch(f"/api/admin/users/{me['id']}", json={"role": "user"}).status_code == 409
    assert client.patch(f"/api/admin/users/{me['id']}", json={"active": False}).status_code == 409
    assert client.delete(f"/api/admin/users/{me['id']}").status_code == 409


def test_promote_and_demote(client, alice):
    uid = next(u["id"] for u in client.get("/api/admin/users").json() if u["email"] == "alice@imperial.ac.uk")
    assert client.patch(f"/api/admin/users/{uid}", json={"role": "admin"}).json()["role"] == "admin"
    assert alice.get("/api/admin/users").status_code == 200
    assert client.patch(f"/api/admin/users/{uid}", json={"role": "user"}).json()["role"] == "user"
    assert alice.get("/api/admin/users").status_code == 403


def test_delete_user(client):
    email = new_email("gone")
    uid = client.post("/api/admin/users", json={"email": email}).json()["id"]
    assert client.delete(f"/api/admin/users/{uid}").status_code == 200
    assert client.delete(f"/api/admin/users/{uid}").status_code == 404
    assert TestClient(app).post("/auth/dev-login", json={"email": email}).status_code == 403


# ------------------------------------------------------------ data isolation

def test_users_cannot_see_each_others_models_and_jobs(client, fleet, alice, bob):
    m = upload(alice, VALID / "cube_binary.stl").json()
    assert alice.get(f"/api/models/{m['id']}/preview.stl").status_code == 200
    assert bob.get(f"/api/models/{m['id']}/preview.stl").status_code == 404
    assert bob.post("/api/assign", json={"model_id": m["id"], "filament": "PLA"}).status_code == 404
    assert bob.post("/api/slice", json={"model_id": m["id"], "filament": "PLA"}).status_code == 404

    job = alice.post("/api/slice", json={"model_id": m["id"], "filament": "PLA"}).json()
    from conftest import wait_job
    wait_job(alice, job["id"])
    for path in (f"/api/jobs/{job['id']}", f"/api/jobs/{job['id']}/gcode", f"/api/jobs/{job['id']}/bundle"):
        assert alice.get(path).status_code in (200, 409)
        assert bob.get(path).status_code == 404, path
    assert bob.post(f"/api/jobs/{job['id']}/queue").status_code == 404
    assert bob.post(f"/api/jobs/{job['id']}/print").status_code == 404
    assert alice.get("/api/jobs").json() == []                                       # a slice is only a draft until it is sent somewhere
    assert alice.post(f"/api/jobs/{job['id']}/queue").status_code == 200              # queueing it on a printer makes it a job
    assert [j["id"] for j in alice.get("/api/jobs").json()] == [job["id"]]
    assert bob.get("/api/jobs").json() == []
    assert job["id"] in [j["id"] for j in client.get("/api/jobs").json()]       # admin sees all
    assert client.get(f"/api/jobs/{job['id']}/gcode").status_code == 200
    assert client.get(f"/api/models/{m['id']}/preview.stl").status_code == 200


def test_failed_upload_leaves_no_record(alice):
    before = len(store.models.all())
    from conftest import INVALID
    assert upload(alice, INVALID / "garbage.stl").status_code == 400
    assert len(store.models.all()) == before


# ---------------------------------------------------- printer config & secrets

def test_printer_details_are_admin_only_and_code_is_encrypted(client, fleet, alice):
    r = client.post("/api/admin/printers", json={"name": "Lab P1S", "model": "P1S", "host": "192.168.50.20", "port": 8883,
                                                  "serial": "01P00A000000001", "access_code": "12345678"})
    assert r.status_code == 200
    created = r.json()
    assert created["host"] == "192.168.50.20" and created["has_access_code"] is True and "access_code" not in created
    stored = store.printers.get(created["id"])["access_code"]
    assert stored.startswith("enc:") and "12345678" not in stored
    assert crypto.unseal(stored) == "12345678"

    admin_view = next(p for p in client.get("/api/printers").json() if p["id"] == created["id"])
    user_view = next(p for p in alice.get("/api/printers").json() if p["id"] == created["id"])
    assert admin_view["serial"] == "01P00A000000001" and "access_code" not in admin_view
    for secret in ("host", "serial", "port", "access_code", "has_access_code"):
        assert secret not in user_view, secret
    assert user_view["name"] == "Lab P1S" and user_view["live"] is True
    assert "12345678" not in alice.get("/api/printers").text + client.get("/api/printers").text


def test_blank_access_code_keeps_stored_one(client, fleet):
    p = client.post("/api/admin/printers", json={"name": "P", "model": "P1S", "host": "192.168.50.21", "serial": "S1",
                                                  "access_code": "AAAA1111"}).json()
    client.put(f"/api/admin/printers/{p['id']}", json={"name": "P2", "model": "P1S", "host": "192.168.50.21", "serial": "S1"})
    assert crypto.unseal(store.printers.get(p["id"])["access_code"]) == "AAAA1111"
    client.put(f"/api/admin/printers/{p['id']}", json={"name": "P2", "model": "P1S", "host": "192.168.50.21", "serial": "S1",
                                                       "access_code": "BBBB2222"})
    assert crypto.unseal(store.printers.get(p["id"])["access_code"]) == "BBBB2222"


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "169.254.169.254", "0.0.0.0", "224.0.0.1"])
def test_printer_host_blocklist(client, fleet, host):
    r = client.post("/api/admin/printers", json={"name": "bad", "model": "P1S", "host": host, "serial": "S", "access_code": "1"})
    assert r.status_code == 400
    assert client.post("/api/admin/printers/test", json={"host": host, "serial": "S", "access_code": "1"}).status_code == 400


def test_printer_validation(client, fleet):
    assert client.post("/api/admin/printers", json={"name": "", "model": "P1S"}).status_code == 422
    assert client.post("/api/admin/printers", json={"name": "x", "model": "P1S", "port": 70000}).status_code == 422
    assert client.post("/api/admin/printers", json={"name": "x", "model": "Prusa"}).status_code == 400


def test_disabled_printer_is_not_assigned(client, fleet):
    m = upload(client, VALID / "cube_binary.stl").json()
    first = client.post("/api/assign", json={"model_id": m["id"], "filament": "PLA"}).json()["chosen"]
    p = store.printers.get(first)
    client.put(f"/api/admin/printers/{first}", json={"name": p["name"], "model": p["model"], "enabled": False})
    after = client.post("/api/assign", json={"model_id": m["id"], "filament": "PLA"}).json()
    assert first not in [c["printer_id"] for c in after["candidates"]]


def test_connection_test_endpoint(client, fleet, monkeypatch):
    seen = {}

    def fake_probe(host, port, serial, code, timeout=6.0):
        seen.update(host=host, port=port, serial=serial, code=code)
        return {"ok": True, "error": None, "state": "IDLE", "ams": []}

    monkeypatch.setattr(app_main.printers, "probe", fake_probe)
    r = client.post("/api/admin/printers/test", json={"host": "192.168.1.9", "serial": "SN", "access_code": "11112222"})
    assert r.json()["ok"] is True and seen["code"] == "11112222"
    p = client.post("/api/admin/printers", json={"name": "P", "model": "P1S", "host": "192.168.1.9", "serial": "SN", "access_code": "STORED99"}).json()
    client.post("/api/admin/printers/test", json={"host": "192.168.1.9", "serial": "SN", "printer_id": p["id"]})
    assert seen["code"] == "STORED99"                                   # falls back to the stored (decrypted) code
    assert client.post("/api/admin/printers/test", json={"host": "192.168.1.9", "serial": "SN"}).status_code == 400


# ---------------------------------------------------------- CSRF / audit log

def test_cross_origin_writes_blocked(client):
    r = client.post("/api/admin/users", json={"email": new_email("csrf")}, headers={"Origin": "https://evil.example"})
    assert r.status_code == 403
    ok = client.post("/api/admin/users", json={"email": new_email("csrf2")}, headers={"Origin": "http://testserver"})
    assert ok.status_code == 200
    assert client.get("/api/me", headers={"Origin": "https://evil.example"}).status_code == 200  # reads unaffected


def test_audit_log_records_admin_actions(client):
    email = new_email("audited")
    client.post("/api/admin/users", json={"email": email})
    log = client.get("/api/admin/audit?limit=20").json()
    assert any(a["action"] == "user_invite" and email in a["detail"] and a["actor"] == ADMIN for a in log)
    assert log == sorted(log, key=lambda a: -a["at"])


# ------------------------------------------------------------ SSO (Entra ID)

class FakeSSO:
    def __init__(self, claims=None, fail=False):
        self.claims, self.fail = claims, fail
        self.sso = SimpleNamespace(authorize_access_token=self._token, authorize_redirect=self._redirect)

    async def _token(self, request):
        if self.fail:
            raise RuntimeError("bad state")
        return {"userinfo": self.claims}

    async def _redirect(self, request, uri):
        from fastapi.responses import RedirectResponse
        return RedirectResponse("https://login.microsoftonline.com/tenant/oauth2/v2.0/authorize?redirect_uri=" + uri)


def sso(monkeypatch, claims=None, fail=False, tenant=""):
    monkeypatch.setattr(auth, "_oauth", FakeSSO(claims, fail))
    monkeypatch.setattr(auth, "OIDC_TENANT_ID", tenant)
    return TestClient(app, follow_redirects=False)


def test_sso_login_redirects_to_provider(monkeypatch):
    c = sso(monkeypatch)
    r = c.get("/auth/login")
    assert r.status_code in (302, 307) and "login.microsoftonline.com" in r.headers["location"]
    assert "redirect_uri=http://testserver/auth/callback" in r.headers["location"]


def test_sso_not_configured_is_404(anon, monkeypatch):
    monkeypatch.setattr(auth, "_oauth", None)
    assert anon.get("/auth/login").status_code == 404 and anon.get("/auth/callback").status_code == 404


def test_sso_invited_user_matches_on_upn_or_email(client, monkeypatch):
    email = new_email("grace")
    client.post("/api/admin/users", json={"email": email})
    # Imperial: email claim is name@imperial.ac.uk but the UPN is a short login on ic.ac.uk
    c = sso(monkeypatch, {"email": email, "preferred_username": "gr123@ic.ac.uk", "oid": "oid-grace", "name": "Grace H"})
    r = c.get("/auth/callback")
    assert r.status_code in (302, 307) and r.headers["location"] == "/"
    assert c.get("/api/me").json()["email"] == email
    # same person later arrives with only the UPN: matched by the stored object id
    c2 = sso(monkeypatch, {"preferred_username": "gr123@ic.ac.uk", "oid": "oid-grace"})
    c2.get("/auth/callback")
    assert c2.get("/api/me").json()["email"] == email
    # and by the UPN alone, since it was recorded at first login
    c3 = sso(monkeypatch, {"preferred_username": "gr123@ic.ac.uk", "oid": "other-oid"})
    c3.get("/auth/callback")
    assert c3.get("/api/me").status_code == 200


def test_sso_rejects_uninvited_and_wrong_tenant_and_failures(monkeypatch):
    c = sso(monkeypatch, {"email": new_email("nobody"), "preferred_username": "nb1@ic.ac.uk"})
    r = c.get("/auth/callback")
    assert r.headers["location"] == "/?login_error=not_invited" and c.get("/api/me").status_code == 401
    c = sso(monkeypatch, {"email": ADMIN, "tid": "someone-elses-tenant"}, tenant="imperial-tenant")
    assert c.get("/auth/callback").headers["location"] == "/?login_error=wrong_tenant"
    assert c.get("/api/me").status_code == 401
    c = sso(monkeypatch, {"email": ADMIN, "tid": "imperial-tenant"}, tenant="imperial-tenant")
    c.get("/auth/callback")
    assert c.get("/api/me").status_code == 200
    c = sso(monkeypatch, fail=True)
    assert c.get("/auth/callback").headers["location"] == "/?login_error=sso_failed"
    c = sso(monkeypatch, {"name": "No Email"})
    assert c.get("/auth/callback").headers["location"].startswith("/?login_error=")


def test_sso_domain_restriction(client, monkeypatch):
    uid = auth.create_user("outsider@example.com")["id"]            # e.g. created before the allow-list changed
    c = sso(monkeypatch, {"email": "outsider@example.com"})
    assert c.get("/auth/callback").headers["location"] == "/?login_error=domain_not_allowed"
    store.users.delete(uid)


# ------------------------------------------------------- production guard rails

@pytest.mark.parametrize("env,expect_fail", [
    ({"PUBLIC_URL": "https://x.test", "DEV_AUTH": "1", "SECRET_KEY": "k"}, True),     # dev login on a public host
    ({"PUBLIC_URL": "https://x.test"}, True),                                          # no secret key
    ({"PUBLIC_URL": "https://x.test", "SECRET_KEY": "k"}, True),                       # no SSO configured
    ({"PUBLIC_URL": "https://x.test", "SECRET_KEY": "k", "OIDC_ISSUER": "https://i", "OIDC_CLIENT_ID": "a", "OIDC_CLIENT_SECRET": "b"}, False),
    ({"PUBLIC_URL": "http://localhost:8000", "DEV_AUTH": "1"}, False),                 # local dev is allowed
    ({"PUBLIC_URL": "https://x.test", "SECRET_KEY": "k", "EMAIL_CONSOLE": "1", "SMTP_HOST": "smtp.x.test", "SMTP_FROM": "a@x.test"}, True),  # codes in the log
    ({"PUBLIC_URL": "https://x.test", "SECRET_KEY": "k", "SMTP_HOST": "smtp.x.test"}, True),                # no sender address
    ({"PUBLIC_URL": "https://x.test", "SECRET_KEY": "k", "SMTP_HOST": "smtp.x.test", "SMTP_FROM": "slicyRS <a@x.test>"}, False),  # email codes only
])
def test_public_deployment_refuses_unsafe_config(tmp_path, env, expect_fail):
    import os
    import subprocess
    import sys
    base = {k: v for k, v in os.environ.items()
            if k not in ("DEV_AUTH", "SECRET_KEY", "OIDC_ISSUER", "OIDC_CLIENT_ID", "OIDC_CLIENT_SECRET", "PUBLIC_URL", "ADMIN_EMAILS",
                        "EMAIL_CONSOLE", "SMTP_HOST", "SMTP_FROM", "EMAIL_LOGIN")}
    r = subprocess.run([sys.executable, "-c", "import app.config"], capture_output=True, text=True,
                       cwd=str(__import__("pathlib").Path(__file__).resolve().parent.parent),
                       env={**base, "SLICY_DATA_DIR": str(tmp_path), **env})
    assert (r.returncode != 0) == expect_fail, r.stderr[-300:]
