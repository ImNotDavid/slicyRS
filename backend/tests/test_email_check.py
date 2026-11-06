"""scripts/check_email.py: configuration rules, DNS evaluation, SMTP diagnosis (no real provider needed)."""
import socket
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import check_email as ce  # noqa: E402
from test_email_login import FakeSMTP  # noqa: E402

BREVO = {"SMTP_HOST": "smtp-relay.brevo.com", "SMTP_PORT": "587", "SMTP_SECURITY": "starttls", "SMTP_USER": "login@example.com",
         "SMTP_PASSWORD": "xsmtpsib-abc", "SMTP_FROM": "slicyRS <slicyrs@your-domain.ac.uk>"}


def levels(findings):
    return [lvl for lvl, _ in findings]


def text(findings):
    return " | ".join(m for _, m in findings)


# -------------------------------------------------------------------- config

def test_brevo_settings_pass():
    assert "fail" not in levels(ce.check_config(BREVO))


def test_missing_host():
    assert levels(ce.check_config({})) == ["fail"]


@pytest.mark.parametrize("port,sec", [("587", "ssl"), ("465", "starttls"), ("587", "none")])
def test_port_and_security_must_match(port, sec):
    assert "fail" in levels(ce.check_config({**BREVO, "SMTP_PORT": port, "SMTP_SECURITY": sec}))


def test_api_key_instead_of_smtp_key_is_caught():
    f = ce.check_config({**BREVO, "SMTP_PASSWORD": "xkeysib-1234"})
    assert "fail" in levels(f) and "API key" in text(f)


def test_free_mail_sender_warns():
    f = ce.check_config({**BREVO, "SMTP_FROM": "slicyRS <someone@gmail.com>"})
    assert "warn" in levels(f) and "DMARC" in text(f)


def test_sender_without_address_fails():
    assert "fail" in levels(ce.check_config({**BREVO, "SMTP_FROM": "slicyRS"}))


def test_missing_password_fails_and_plain_text_warns():
    assert "fail" in levels(ce.check_config({**BREVO, "SMTP_PASSWORD": ""}))
    assert "warn" in levels(ce.check_config({**BREVO, "SMTP_SECURITY": "none", "SMTP_PORT": "25"}))


# ------------------------------------------------------------------------ DNS

def fake_dns(records):
    return lambda name, rtype: records.get((name, rtype), [])


GOOD = {("d.ac.uk", "TXT"): ["v=spf1 include:spf.example.net ~all"],
        ("brevo1._domainkey.d.ac.uk", "CNAME"): ["b1.d-ac-uk.dkim.example.net."],
        ("_dmarc.d.ac.uk", "TXT"): ["v=DMARC1; p=quarantine; rua=mailto:a@d.ac.uk"]}


def test_dns_all_present():
    f = ce.check_dns("d.ac.uk", fake_dns(GOOD))
    assert levels(f) == ["ok", "ok", "ok"] and "brevo1" in text(f) and "quarantine" in text(f)


def test_dns_nothing_present_warns_but_names_the_next_step():
    f = ce.check_dns("d.ac.uk", fake_dns({}))
    assert "fail" not in levels(f) and "--dkim-selector" in text(f) and "DMARC" in text(f)


def test_dns_explicit_selector_missing_is_a_failure():
    f = ce.check_dns("d.ac.uk", fake_dns({}), ["s1"])
    assert "fail" in levels(f) and "s1._domainkey.d.ac.uk" in text(f)


def test_dns_explicit_selector_found():
    recs = {("s1._domainkey.d.ac.uk", "TXT"): ["v=DKIM1; k=rsa; p=ABC"]}
    assert "ok" in levels(ce.check_dns("d.ac.uk", fake_dns(recs), ["s1"]))


def test_two_spf_records_fail():
    recs = {("d.ac.uk", "TXT"): ["v=spf1 -all", "v=spf1 include:x ~all"]}
    assert "fail" in levels(ce.check_dns("d.ac.uk", fake_dns(recs)))


def test_dns_skipped_without_resolver():
    assert levels(ce.check_dns("d.ac.uk", None)) == ["warn"]


# ---------------------------------------------------------------- diagnosis

@pytest.mark.parametrize("code,msg,expected", [
    (535, "5.7.8 Authentication failed", "SMTP key"),
    (525, "5.7.1 Unauthorized IP address", "IP"),
    (553, "Sender is not valid", "sender"),
    (451, "temporary rate limit", "rate limiting"),
    (554, "message blocked as spam", "blocked"),
    (None, "STARTTLS extension not supported", "TLS"),
    (999, "???", "Unrecognised"),
])
def test_error_messages_are_actionable(code, msg, expected):
    assert expected.lower() in ce.diagnose_smtp_error(code, msg).lower()


# -------------------------------------------------------------- live SMTP

def _env(port, **kw):
    return {"SMTP_HOST": "127.0.0.1", "SMTP_PORT": str(port), "SMTP_SECURITY": "none", "SMTP_FROM": "slicyRS <a@d.ac.uk>", **kw}


def _dead_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def test_connects_without_sending():
    srv = FakeSMTP()
    try:
        f = ce.check_smtp(_env(srv.port))
        assert levels(f) == ["ok"] and not srv.messages
    finally:
        srv.stop()


def test_sends_a_real_message_when_asked():
    srv = FakeSMTP()
    try:
        f = ce.check_smtp(_env(srv.port), send_to="me@example.com")
        assert "fail" not in levels(f) and "accepted by the relay" in text(f)
        import time
        for _ in range(60):
            if srv.messages:
                break
            time.sleep(0.05)
        assert srv.messages[0]["to"] == ["me@example.com"] and "SPF, DKIM and DMARC" in srv.messages[0]["data"]
    finally:
        srv.stop()


def test_login_against_server_without_auth_is_a_clear_failure():
    srv = FakeSMTP()
    try:
        f = ce.check_smtp(_env(srv.port, SMTP_USER="u", SMTP_PASSWORD="p"))
        assert "fail" in levels(f)
    finally:
        srv.stop()


def test_closed_port_fails_with_a_hint():
    f = ce.check_smtp(_env(_dead_port()), timeout=3)
    assert "fail" in levels(f) and ("Cannot connect" in text(f) or "Timed out" in text(f))


def test_full_run_combines_all_stages():
    srv = FakeSMTP()
    try:
        f = ce.run(_env(srv.port), resolver=fake_dns(GOOD))
        assert "fail" not in levels(f) and levels(f).count("ok") >= 5
    finally:
        srv.stop()


def test_password_is_never_printed(capsys, monkeypatch, tmp_path):
    srv = FakeSMTP()
    env = tmp_path / ".env"
    env.write_text(f"SMTP_HOST=127.0.0.1\nSMTP_PORT={srv.port}\nSMTP_SECURITY=none\nSMTP_FROM=slicyRS <a@gmail.com>\nSMTP_USER=u\nSMTP_PASSWORD=TOPSECRET123\n")
    for k in ("SMTP_HOST", "SMTP_PORT", "SMTP_SECURITY", "SMTP_FROM", "SMTP_USER", "SMTP_PASSWORD"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(sys, "argv", ["check_email.py", "--env-file", str(env)])
    try:
        ce.main()
    finally:
        srv.stop()
    assert "TOPSECRET123" not in capsys.readouterr().out
