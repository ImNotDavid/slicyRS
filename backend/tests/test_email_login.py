"""Emailed one-time login codes: invite-only, single use, expiring, rate limited, no account enumeration."""
import asyncore
import re
import smtpd
import threading
import time
import uuid

import pytest
from fastapi.testclient import TestClient

from app import auth, config, mailer, store
from app.main import app
from conftest import ADMIN


@pytest.fixture(autouse=True)
def clean():
    auth._hits.clear()
    mailer.outbox.clear()
    yield


def new_email(prefix="e"):
    return f"{prefix}{uuid.uuid4().hex[:6]}@imperial.ac.uk"


def invite(client, email):
    assert client.post("/api/admin/users", json={"email": email}).status_code in (200, 409)


def code_for(email, timeout=3.0):
    end = time.time() + timeout
    while time.time() < end:
        mails = [m for m in mailer.outbox if m["to"] == email]
        if mails:
            return re.search(r"\b(\d{6})\b", mails[-1]["body"]).group(1)
        time.sleep(0.05)
    return None


@pytest.fixture()
def anon():
    return TestClient(app)


def request_code(anon, email):
    return anon.post("/auth/email/request", json={"email": email})


def verify(anon, email, code):
    return anon.post("/auth/email/verify", json={"email": email, "code": code})


# ------------------------------------------------------------------ happy path

def test_config_advertises_email_login(anon):
    assert anon.get("/auth/config").json()["email_login"] is True


def test_invited_user_signs_in_with_emailed_code(client, anon):
    email = new_email()
    invite(client, email)
    assert request_code(anon, email).status_code == 200
    code = code_for(email)
    assert code and len(code) == 6
    r = verify(anon, email, code)
    assert r.status_code == 200 and r.json()["email"] == email
    assert anon.get("/api/me").json()["email"] == email
    assert any(a["action"] == "login" and a["detail"] == "email code" for a in client.get("/api/admin/audit?limit=10").json())


def test_email_is_case_insensitive_and_code_tolerates_spaces(client, anon):
    email = new_email("mixed")
    invite(client, email)
    request_code(anon, f"  {email.upper()} ")
    code = code_for(email)
    assert verify(anon, email.upper(), f"{code[:3]} {code[3:]}").status_code == 200


def test_bootstrap_admin_can_use_email_codes(anon):
    request_code(anon, ADMIN)
    r = verify(anon, ADMIN, code_for(ADMIN))
    assert r.status_code == 200 and r.json()["role"] == "admin"


def test_email_content(client, anon):
    email = new_email()
    invite(client, email)
    request_code(anon, email)
    code = code_for(email)
    m = mailer.outbox[-1]
    assert code in m["subject"] and "expires in 10 minutes" in m["body"] and "ignore this email" in m["body"]


# ------------------------------------------------------------------- code rules

def test_code_is_single_use(client, anon):
    email = new_email()
    invite(client, email)
    request_code(anon, email)
    code = code_for(email)
    assert verify(anon, email, code).status_code == 200
    assert verify(TestClient(app), email, code).status_code == 401


def test_wrong_code_rejected_and_five_misses_burn_the_code(client, anon):
    email = new_email()
    invite(client, email)
    request_code(anon, email)
    code = code_for(email)
    wrong = "000000" if code != "000000" else "111111"
    for _ in range(5):
        assert verify(anon, email, wrong).status_code == 401
    assert verify(anon, email, code).status_code == 401                 # even the right code is dead now
    assert anon.get("/api/me").status_code == 401


def test_code_expires(client, anon):
    email = new_email()
    invite(client, email)
    request_code(anon, email)
    code = code_for(email)
    rec = store.codes.get(email)
    store.codes.put(email, {**rec, "expires": time.time() - 1})
    assert verify(anon, email, code).status_code == 401


def test_new_request_replaces_the_old_code(client, anon):
    email = new_email()
    invite(client, email)
    request_code(anon, email)
    first = code_for(email)
    mailer.outbox.clear()
    request_code(anon, email)
    second = code_for(email)
    if first != second:
        assert verify(anon, email, first).status_code == 401
    assert verify(anon, email, second).status_code == 200


def test_code_is_bound_to_its_email(client, anon):
    a, b = new_email("a"), new_email("b")
    invite(client, a)
    invite(client, b)
    request_code(anon, a)
    code = code_for(a)
    assert verify(anon, b, code).status_code == 401
    assert anon.get("/api/me").status_code == 401


def test_code_is_stored_hashed(client, anon):
    email = new_email()
    invite(client, email)
    request_code(anon, email)
    code = code_for(email)
    rec = store.codes.get(email)
    assert code not in str(rec) and len(rec["hash"]) == 64


def test_verify_without_a_request_fails(anon):
    assert verify(anon, new_email(), "123456").status_code == 401


# ---------------------------------------------- invite-only and no enumeration

def test_uninvited_address_gets_the_same_response_and_no_email(client, anon):
    invited, stranger = new_email("in"), new_email("out")
    invite(client, invited)
    a = request_code(anon, invited)
    mailer.outbox.clear()
    b = request_code(anon, stranger)
    assert (a.status_code, a.json()) == (b.status_code, b.json())
    time.sleep(0.3)
    assert not [m for m in mailer.outbox if m["to"] == stranger]
    assert store.codes.get(stranger) is None
    assert verify(anon, stranger, "123456").status_code == 401


def test_disallowed_domain_never_gets_a_code(anon):
    request_code(anon, "someone@gmail.com")
    time.sleep(0.3)
    assert not [m for m in mailer.outbox if m["to"] == "someone@gmail.com"]


def test_disabled_user_gets_no_code(client, anon):
    email = new_email()
    uid = client.post("/api/admin/users", json={"email": email}).json()["id"]
    client.patch(f"/api/admin/users/{uid}", json={"active": False})
    request_code(anon, email)
    time.sleep(0.3)
    assert not [m for m in mailer.outbox if m["to"] == email]


def test_user_disabled_after_code_sent_cannot_use_it(client, anon):
    email = new_email()
    uid = client.post("/api/admin/users", json={"email": email}).json()["id"]
    request_code(anon, email)
    code = code_for(email)
    client.patch(f"/api/admin/users/{uid}", json={"active": False})
    assert verify(anon, email, code).status_code == 401


def test_invalid_email_format(anon):
    assert request_code(anon, "not-an-email").status_code == 400


# ---------------------------------------------------------------- rate limits

def test_request_rate_limit_per_email(client, anon):
    email = new_email()
    invite(client, email)
    codes = [request_code(anon, email).status_code for _ in range(7)]
    assert codes[:5] == [200] * 5 and codes[5:] == [429, 429]


def test_rate_limit_applies_to_unknown_addresses_too(anon):
    email = new_email("ghost")
    statuses = [request_code(anon, email).status_code for _ in range(7)]
    assert statuses[:5] == [200] * 5 and 429 in statuses               # no difference that would reveal it is unknown


def test_request_rate_limit_per_ip(anon):
    results = [request_code(anon, new_email("ip")).status_code for _ in range(23)]
    assert results[:20] == [200] * 20 and results[20:] == [429] * 3


def test_verify_rate_limit_per_ip(anon):
    email = new_email()
    results = [verify(anon, email, "000000").status_code for _ in range(42)]
    assert 429 in results and results[0] == 401


# -------------------------------------------------------------- feature switch

def test_disabled_feature_is_404(anon, monkeypatch):
    monkeypatch.setattr(auth, "EMAIL_LOGIN", False)
    assert request_code(anon, ADMIN).status_code == 404
    assert verify(anon, ADMIN, "123456").status_code == 404
    assert anon.get("/auth/config").json()["email_login"] is False


# ------------------------------------------------------------------ SMTP path

class FakeSMTP(smtpd.SMTPServer):
    def __init__(self):
        super().__init__(("127.0.0.1", 0), None, decode_data=True)
        self.port = self.socket.getsockname()[1]
        self.messages = []
        self.thread = threading.Thread(target=lambda: asyncore.loop(timeout=0.1, use_poll=True), daemon=True)
        self.thread.start()

    def process_message(self, peer, mailfrom, rcpttos, data, **kw):
        self.messages.append({"from": mailfrom, "to": rcpttos, "data": data})

    def stop(self):
        self.close()


def _dead_port():
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture()
def smtp(monkeypatch):
    srv = FakeSMTP()
    monkeypatch.setattr(config, "EMAIL_CONSOLE", False)
    monkeypatch.setattr(config, "SMTP_HOST", "127.0.0.1")
    monkeypatch.setattr(config, "SMTP_PORT", srv.port)
    monkeypatch.setattr(config, "SMTP_SECURITY", "none")
    monkeypatch.setattr(config, "SMTP_FROM", "slicyRS <slicyrs@example.ac.uk>")
    yield srv
    srv.stop()


def test_code_is_delivered_over_smtp(client, anon, smtp):
    email = new_email()
    invite(client, email)
    assert request_code(anon, email).status_code == 200
    for _ in range(80):
        if smtp.messages:
            break
        time.sleep(0.05)
    msg = smtp.messages[0]
    assert msg["to"] == [email] and msg["from"] == "slicyrs@example.ac.uk"
    code = re.search(r"sign-in code is (\d{6})", msg["data"]).group(1)
    assert f"Subject: Your slicyRS sign-in code: {code}" in msg["data"]
    assert verify(anon, email, code).status_code == 200


def test_smtp_failure_is_invisible_to_the_user_but_audited(client, anon, monkeypatch):
    monkeypatch.setattr(config, "EMAIL_CONSOLE", False)
    monkeypatch.setattr(config, "SMTP_HOST", "127.0.0.1")
    monkeypatch.setattr(config, "SMTP_PORT", _dead_port())
    monkeypatch.setattr(config, "SMTP_SECURITY", "none")
    email = new_email()
    invite(client, email)
    assert request_code(anon, email).status_code == 200                # same answer; nothing leaks
    for _ in range(80):
        log = client.get("/api/admin/audit?limit=20").json()
        if any(a["action"] == "login_code_send_failed" and email in a["detail"] for a in log):
            break
        time.sleep(0.1)
    else:
        pytest.fail("send failure was not audited")


def test_admin_test_email_endpoint(client, smtp):
    r = client.post("/api/admin/email/test")
    assert r.status_code == 200 and r.json()["to"] == ADMIN
    assert smtp.messages and smtp.messages[0]["to"] == [ADMIN]


def test_admin_test_email_is_admin_only(client, anon):
    email = new_email("plain")
    invite(client, email)
    request_code(anon, email)
    assert verify(anon, email, code_for(email)).status_code == 200
    assert anon.post("/api/admin/email/test").status_code == 403


def test_admin_test_email_reports_smtp_errors(client, monkeypatch):
    monkeypatch.setattr(config, "EMAIL_CONSOLE", False)
    monkeypatch.setattr(config, "SMTP_HOST", "127.0.0.1")
    monkeypatch.setattr(config, "SMTP_PORT", _dead_port())
    monkeypatch.setattr(config, "SMTP_SECURITY", "none")
    r = client.post("/api/admin/email/test")
    assert r.status_code == 502 and "Could not send" in r.json()["detail"]
