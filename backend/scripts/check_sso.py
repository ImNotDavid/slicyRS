"""Validate the Imperial / Entra SSO settings before (and after) deploying.

    python scripts/check_sso.py                 # reads OIDC_* and PUBLIC_URL from the environment / .env
    python scripts/check_sso.py --probe-secret  # also proves the client id + secret are valid for the tenant

Exit code 0 when nothing is wrong. It never prints the client secret.
"""
import argparse
import os
import re
import sys
from pathlib import Path

import httpx

GUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)


def load_dotenv(path: Path) -> None:
    if path.exists():
        for line in path.read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip().strip('"'))


def check(env: dict[str, str], probe_secret: bool = False) -> list[tuple[str, str]]:
    """Return (level, message) findings; level is ok / warn / fail."""
    out: list[tuple[str, str]] = []
    add = lambda level, msg: out.append((level, msg))
    issuer = env.get("OIDC_ISSUER", "").rstrip("/")
    tenant, cid, secret = env.get("OIDC_TENANT_ID", ""), env.get("OIDC_CLIENT_ID", ""), env.get("OIDC_CLIENT_SECRET", "")
    public = env.get("PUBLIC_URL", "http://localhost:8000").rstrip("/")

    for name, val in (("OIDC_ISSUER", issuer), ("OIDC_CLIENT_ID", cid), ("OIDC_CLIENT_SECRET", secret)):
        if not val:
            add("fail", f"{name} is not set")
    if not issuer:
        return out
    m = re.match(r"^https://login\.microsoftonline\.com/([^/]+)/v2\.0$", issuer)
    if not m:
        add("warn", "OIDC_ISSUER is not of the form https://login.microsoftonline.com/<tenant-id>/v2.0 (fine for a non-Microsoft provider)")
    else:
        if m.group(1).lower() in ("common", "organizations", "consumers"):
            add("fail", f"The issuer uses '{m.group(1)}'; use Imperial's tenant id, otherwise token issuers cannot be validated")
        elif not GUID.match(m.group(1)):
            add("warn", f"'{m.group(1)}' is not a tenant GUID (a verified domain name also works for discovery but use the GUID)")
        if tenant and m.group(1).lower() != tenant.lower():
            add("fail", f"OIDC_TENANT_ID ({tenant}) differs from the tenant in OIDC_ISSUER ({m.group(1)})")
    if not tenant:
        add("warn", "OIDC_TENANT_ID is empty: tokens from any tenant the app is registered for would pass the tenant check")
    if not public.startswith("https://") and "localhost" not in public and "127.0.0.1" not in public:
        add("fail", f"PUBLIC_URL {public} must be https (or localhost for local tests); Entra rejects other http redirect URIs")

    try:
        meta = httpx.get(f"{issuer}/.well-known/openid-configuration", timeout=10).raise_for_status().json()
    except Exception as e:
        add("fail", f"Cannot fetch the discovery document from {issuer}: {e}")
        return out
    add("ok", "Discovery document fetched")
    if meta.get("issuer", "").rstrip("/") != issuer:
        add("fail", f"Discovery issuer {meta.get('issuer')} does not match OIDC_ISSUER")
    for key in ("authorization_endpoint", "token_endpoint", "jwks_uri"):
        if key not in meta:
            add("fail", f"Discovery document has no {key}")
    if "S256" not in meta.get("code_challenge_methods_supported", ["S256"]):
        add("fail", "Provider does not support PKCE S256")
    if "RS256" not in meta.get("id_token_signing_alg_values_supported", ["RS256"]):
        add("fail", "Provider does not sign id_tokens with RS256")
    try:
        keys = httpx.get(meta["jwks_uri"], timeout=10).raise_for_status().json().get("keys", [])
        add("ok" if keys else "fail", f"Signing keys available: {len(keys)}")
    except Exception as e:
        add("fail", f"Cannot fetch signing keys: {e}")

    add("ok", f"Register this redirect URI (type: Web): {public}/auth/callback")
    if probe_secret and cid and secret and "token_endpoint" in meta:
        r = httpx.post(meta["token_endpoint"], timeout=15, data={
            "grant_type": "client_credentials", "client_id": cid, "client_secret": secret,
            "scope": "https://graph.microsoft.com/.default"})
        if r.status_code == 200:
            add("ok", "Client id and secret are valid for this tenant")
        else:
            err = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
            add("fail", f"Token endpoint rejected the client credentials: {err.get('error_description', r.text)[:200]}")
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--probe-secret", action="store_true")
    ap.add_argument("--env-file", default=str(Path(__file__).resolve().parents[2] / ".env"))
    args = ap.parse_args()
    load_dotenv(Path(args.env_file))
    findings = check(dict(os.environ), args.probe_secret)
    mark = {"ok": "OK  ", "warn": "WARN", "fail": "FAIL"}
    for level, msg in findings:
        print(f"[{mark[level]}] {msg}")
    return 1 if any(level == "fail" for level, _ in findings) else 0


if __name__ == "__main__":
    sys.exit(main())
