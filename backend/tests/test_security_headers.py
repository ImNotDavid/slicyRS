"""Security headers on every response, and the page really works under the policy."""
import re
from pathlib import Path

import pytest

from app import main as app_main
from app.main import CONTENT_SECURITY_POLICY


@pytest.mark.parametrize("path", ["/api/health", "/auth/config", "/api/me", "/does-not-exist", "/"])
def test_headers_on_every_kind_of_response(client, path):
    r = client.get(path)
    assert r.headers["content-security-policy"] == CONTENT_SECURITY_POLICY
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-frame-options"] == "DENY"
    assert r.headers["referrer-policy"] == "same-origin"
    assert "camera=()" in r.headers["permissions-policy"]


def test_errors_carry_headers_too(client):
    r = client.post("/api/admin/users", json={"email": "x@imperial.ac.uk"}, headers={"Origin": "https://evil.example"})
    assert r.status_code == 403 and r.headers["x-content-type-options"] == "nosniff"


def test_api_responses_are_never_cached(client):
    for path in ("/api/printers", "/api/jobs", "/auth/config", "/api/me"):
        assert client.get(path).headers["cache-control"] == "no-store", path


def test_hsts_only_over_https(client, monkeypatch):
    assert "strict-transport-security" not in client.get("/api/health").headers      # tests run on http
    monkeypatch.setattr(app_main, "COOKIE_SECURE", True)
    assert client.get("/api/health").headers["strict-transport-security"].startswith("max-age=")


def test_policy_blocks_the_dangerous_things():
    p = CONTENT_SECURITY_POLICY
    assert "'unsafe-eval'" not in p and "script-src 'self'" in p            # no inline or eval scripts
    assert "frame-ancestors 'none'" in p and "object-src 'none'" in p and "form-action 'self'" in p
    assert "connect-src 'self'" in p                                        # the page can only talk to its own server


def test_built_page_needs_no_inline_script():
    """The policy forbids inline scripts, so the built page must not contain any."""
    index = Path(__file__).resolve().parents[2] / "frontend" / "dist" / "index.html"
    if not index.exists():
        pytest.skip("frontend not built")
    html = index.read_text(encoding="utf-8")
    inline = [m for m in re.findall(r"<script\b([^>]*)>(.*?)</script>", html, re.S) if "src=" not in m[0] and m[1].strip()]
    assert not inline, "inline scripts would be blocked by the Content-Security-Policy"
    assert not re.search(r"\son\w+\s*=", html), "inline event handlers would be blocked"
