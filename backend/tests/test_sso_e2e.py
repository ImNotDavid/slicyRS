"""The real OpenID Connect code flow (authlib discovery, PKCE, state, nonce, JWKS signature checks) against a mock Entra."""
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from fastapi.testclient import TestClient

from app import auth, store
from app.main import app
from conftest import ADMIN
from mock_idp import CLIENT_ID, CLIENT_SECRET, TENANT, MockIdP


@pytest.fixture(scope="module")
def idp():
    p = MockIdP()
    p.start()
    yield p
    p.stop()


@pytest.fixture()
def sso(idp, monkeypatch):
    idp.reset()
    old = auth._oauth
    auth.configure_sso(idp.issuer, CLIENT_ID, CLIENT_SECRET)
    monkeypatch.setattr(auth, "OIDC_TENANT_ID", TENANT)
    yield idp
    auth._oauth = old


def sign_in(idp: MockIdP, *, tamper_state=False):
    """Drive the whole redirect dance like a browser: app -> provider -> app callback. Returns (client, callback response)."""
    c = TestClient(app, follow_redirects=False)
    r = c.get("/auth/login")
    assert r.status_code in (302, 307), r.text
    authorize_url = r.headers["location"]
    q = parse_qs(urlparse(authorize_url).query)
    assert q["client_id"] == [CLIENT_ID] and q["response_type"] == ["code"]
    assert q["redirect_uri"] == ["http://testserver/auth/callback"]
    assert q["code_challenge_method"] == ["S256"] and q["nonce"] and q["state"]          # PKCE + nonce + state in use
    back = httpx.get(authorize_url, follow_redirects=False)                                # the user "logs in" at the provider
    assert back.status_code in (302, 307), back.text
    callback = urlparse(back.headers["location"])
    path = callback.path + "?" + callback.query
    if tamper_state:
        path = path.replace("state=", "state=x")
    return c, c.get(path)


def invite(client, email):
    r = client.post("/api/admin/users", json={"email": email})
    assert r.status_code in (200, 409)


def test_full_flow_signs_in_an_invited_user(client, sso):
    invite(client, "test.person@imperial.ac.uk")
    c, r = sign_in(sso)
    assert r.status_code in (302, 307) and r.headers["location"] == "/"
    me = c.get("/api/me").json()
    assert me["email"] == "test.person@imperial.ac.uk" and me["role"] == "user" and me["name"] == "Test Person"
    audit = client.get("/api/admin/audit?limit=5").json()
    assert any("sso:" in a["detail"] and "tp123@ic.ac.uk" in a["detail"] for a in audit)   # claims are logged for debugging


def test_entra_without_email_claim_matches_on_upn(client, sso):
    """Entra only sends `email` if the optional claim is configured; the UPN (preferred_username) must still work."""
    invite(client, "np123@ic.ac.uk")
    sso.claims.pop("email")
    sso.claims.update(preferred_username="np123@ic.ac.uk", oid="oid-np", name="No Email")
    c, r = sign_in(sso)
    assert r.headers["location"] == "/" and c.get("/api/me").json()["email"] == "np123@ic.ac.uk"


def test_user_found_again_by_object_id_after_email_change(client, sso):
    invite(client, "moved@imperial.ac.uk")
    sso.claims.update(email="moved@imperial.ac.uk", preferred_username="mv1@ic.ac.uk", oid="oid-moved")
    sign_in(sso)
    sso.claims.update(email="renamed@imperial.ac.uk", preferred_username="renamed@ic.ac.uk")   # same oid, new addresses
    c, r = sign_in(sso)
    assert r.headers["location"] == "/" and c.get("/api/me").json()["email"] == "moved@imperial.ac.uk"


def test_admin_bootstrap_through_real_flow(client, sso):
    sso.claims.update(email=ADMIN, preferred_username=ADMIN, oid="oid-admin")
    c, r = sign_in(sso)
    assert r.headers["location"] == "/" and c.get("/api/me").json()["role"] == "admin"


def test_uninvited_person_is_refused(sso):
    sso.claims.update(email="nobody.here@imperial.ac.uk", preferred_username="nh9@ic.ac.uk", oid="oid-nobody")
    c, r = sign_in(sso)
    assert r.headers["location"] == "/?login_error=not_invited" and c.get("/api/me").status_code == 401


def test_other_tenant_is_refused(client, sso):
    invite(client, "test.person@imperial.ac.uk")
    sso.claims["tid"] = "99999999-aaaa-bbbb-cccc-dddddddddddd"
    c, r = sign_in(sso)
    assert r.headers["location"] == "/?login_error=wrong_tenant" and c.get("/api/me").status_code == 401


def test_no_name_claims_at_all(sso):
    for k in ("email", "preferred_username"):
        sso.claims.pop(k)
    c, r = sign_in(sso)
    assert r.headers["location"] == "/?login_error=no_email"


def test_disabled_user_is_refused(client, sso):
    email = "disabled.person@imperial.ac.uk"
    uid = client.post("/api/admin/users", json={"email": email}).json()["id"]
    client.patch(f"/api/admin/users/{uid}", json={"active": False})
    sso.claims.update(email=email, preferred_username="dp1@ic.ac.uk", oid="oid-disabled")
    c, r = sign_in(sso)
    assert r.headers["location"] == "/?login_error=disabled"


# --------------------------------------------- attacks the OIDC library must stop

@pytest.mark.parametrize("mutate", [
    lambda i: setattr(i, "sign_with_wrong_key", True),      # forged signature
    lambda i: setattr(i, "audience", "someone-elses-app"),  # token minted for another client
    lambda i: setattr(i, "expires_in", -3600),              # already expired
    lambda i: setattr(i, "override_nonce", "replayed"),     # nonce does not match the login request (replay)
], ids=["bad-signature", "wrong-audience", "expired", "nonce-mismatch"])
def test_invalid_id_tokens_are_rejected(client, sso, mutate):
    invite(client, "test.person@imperial.ac.uk")
    mutate(sso)
    c, r = sign_in(sso)
    assert r.headers["location"] == "/?login_error=sso_failed"
    assert c.get("/api/me").status_code == 401


def test_tampered_state_is_rejected(client, sso):
    invite(client, "test.person@imperial.ac.uk")
    c, r = sign_in(sso, tamper_state=True)
    assert r.headers["location"] == "/?login_error=sso_failed" and c.get("/api/me").status_code == 401


def test_callback_without_a_login_attempt_is_rejected(sso):
    c = TestClient(app, follow_redirects=False)                 # no prior /auth/login, so no state in the session
    r = c.get("/auth/callback?code=abc&state=def")
    assert r.headers["location"] == "/?login_error=sso_failed"


def test_authorization_code_cannot_be_replayed(client, sso):
    invite(client, "test.person@imperial.ac.uk")
    c = TestClient(app, follow_redirects=False)
    q = parse_qs(urlparse(c.get("/auth/login").headers["location"]).query)
    loc = httpx.get(c.get("/auth/login").headers["location"], follow_redirects=False).headers["location"]
    path = urlparse(loc).path + "?" + urlparse(loc).query
    first = c.get(path)
    assert first.headers["location"] in ("/", "/?login_error=sso_failed")
    second = TestClient(app, follow_redirects=False)
    second.get("/auth/login")
    assert second.get(path).headers["location"] == "/?login_error=sso_failed"      # code already used / state mismatch


def test_identity_provider_down_fails_closed(client, sso, monkeypatch):
    auth.configure_sso("http://127.0.0.1:9/" + TENANT + "/v2.0", CLIENT_ID, CLIENT_SECRET)   # nothing listens there
    c = TestClient(app, follow_redirects=False, raise_server_exceptions=False)
    r = c.get("/auth/login")
    assert r.status_code >= 500 or r.status_code in (302, 307)
    assert c.get("/api/me").status_code == 401


# ------------------------------------------------------- scripts/check_sso.py

def test_check_script_findings(idp):
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
    import check_sso
    good = {"OIDC_ISSUER": idp.issuer, "OIDC_CLIENT_ID": CLIENT_ID, "OIDC_CLIENT_SECRET": CLIENT_SECRET,
            "OIDC_TENANT_ID": TENANT, "PUBLIC_URL": "http://localhost:8000"}
    levels = lambda env: [lvl for lvl, _ in check_sso.check(env)]
    assert "fail" not in levels(good)
    assert any("redirect URI" in m and "/auth/callback" in m for _, m in check_sso.check(good))
    assert "fail" in levels({**good, "OIDC_CLIENT_SECRET": ""})
    assert "fail" in levels({**good, "OIDC_ISSUER": "https://login.microsoftonline.com/common/v2.0"})
    assert "fail" in levels({**good, "OIDC_TENANT_ID": "99999999-aaaa-bbbb-cccc-dddddddddddd",
                             "OIDC_ISSUER": f"https://login.microsoftonline.com/{TENANT}/v2.0"})
    assert "fail" in levels({**good, "PUBLIC_URL": "http://slicer.example.ac.uk"})
    assert "fail" in levels({**good, "OIDC_ISSUER": "http://127.0.0.1:9/x/v2.0"})        # unreachable provider
    assert "warn" in levels({**good, "OIDC_TENANT_ID": ""})
