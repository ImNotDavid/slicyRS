import os
import secrets
from pathlib import Path

DATA_DIR = Path(os.environ.get("SLICY_DATA_DIR", Path(__file__).resolve().parent.parent / "data"))
MODELS_DIR = DATA_DIR / "models"
JOBS_DIR = DATA_DIR / "jobs"
for d in (DATA_DIR, MODELS_DIR, JOBS_DIR):
    d.mkdir(parents=True, exist_ok=True)

# "orca" runs a Bambu Studio / OrcaSlicer CLI; "mock" writes a placeholder G-code so the
# rest of the pipeline can be exercised without a slicer installed.
SLICER_MODE = os.environ.get("SLICER_MODE", "mock")
ORCA_BIN = os.environ.get("ORCA_BIN", "orca-slicer")
ORCA_PROFILES_DIR = Path(os.environ.get("ORCA_PROFILES_DIR", "/opt/orca/resources/profiles"))
SLICE_TIMEOUT_S = int(os.environ.get("SLICE_TIMEOUT_S", "900"))
MAX_WORKERS = int(os.environ.get("SLICE_WORKERS", "2"))
MAX_UPLOAD_MB = int(os.environ.get("MAX_UPLOAD_MB", "200"))

# ---- cloud service ---------------------------------------------------------
PUBLIC_URL = os.environ.get("PUBLIC_URL", "http://localhost:8000").rstrip("/")


def _secret_key() -> str:
    """Session/encryption key. Production must set SECRET_KEY; dev persists a random one."""
    key = os.environ.get("SECRET_KEY")
    if key:
        return key
    f = DATA_DIR / "secret.key"
    if not f.exists():
        f.write_text(secrets.token_urlsafe(48))
    return f.read_text().strip()


SECRET_KEY = _secret_key()
COOKIE_SECURE = PUBLIC_URL.startswith("https://")
SESSION_HOURS = int(os.environ.get("SESSION_HOURS", "12"))

# Imperial College sign-in is Microsoft Entra ID: issuer https://login.microsoftonline.com/<tenant-id>/v2.0
OIDC_ISSUER = os.environ.get("OIDC_ISSUER", "")
OIDC_CLIENT_ID = os.environ.get("OIDC_CLIENT_ID", "")
OIDC_CLIENT_SECRET = os.environ.get("OIDC_CLIENT_SECRET", "")
OIDC_TENANT_ID = os.environ.get("OIDC_TENANT_ID", "")  # when set, tokens from other tenants are rejected
SSO_ENABLED = bool(OIDC_ISSUER and OIDC_CLIENT_ID and OIDC_CLIENT_SECRET)
SSO_NAME = os.environ.get("SSO_NAME", "Imperial College London")

# Comma-separated; may sign in via SSO and are created as admins on first login.
ADMIN_EMAILS = {e.strip().lower() for e in os.environ.get("ADMIN_EMAILS", "").split(",") if e.strip()}
# Invites and sign-ins must belong to one of these domains (Imperial staff use both).
ALLOWED_EMAIL_DOMAINS = {d.strip().lower() for d in os.environ.get("ALLOWED_EMAIL_DOMAINS", "imperial.ac.uk,ic.ac.uk").split(",") if d.strip()}

# Passwordless dev login for local testing ONLY. Never enable on a public server.
DEV_AUTH = os.environ.get("DEV_AUTH", "0") == "1"

# ---- emailed one-time login codes (alternative to SSO) -----------------------
SMTP_HOST = os.environ.get("SMTP_HOST", "")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")
SMTP_SECURITY = os.environ.get("SMTP_SECURITY", "starttls").lower()      # starttls | ssl | none
SMTP_FROM = os.environ.get("SMTP_FROM", "")                                # e.g. "slicyRS <slicyrs@your-domain>"
EMAIL_CONSOLE = os.environ.get("EMAIL_CONSOLE", "0") == "1"                # print codes to the server log (local testing only)
EMAIL_LOGIN = os.environ.get("EMAIL_LOGIN", "auto") != "0" and bool(SMTP_HOST or EMAIL_CONSOLE)
CODE_TTL_MIN = int(os.environ.get("CODE_TTL_MIN", "10"))
APP_NAME = os.environ.get("APP_NAME", "slicyRS")

# Refuse to start a public (https) deployment with development shortcuts.
if PUBLIC_URL.startswith("https://"):
    if DEV_AUTH:
        raise RuntimeError("DEV_AUTH=1 is not allowed when PUBLIC_URL is https (passwordless login).")
    if not os.environ.get("SECRET_KEY"):
        raise RuntimeError("Set SECRET_KEY for a public deployment (sessions and printer codes are keyed from it).")
    if EMAIL_CONSOLE:
        raise RuntimeError("EMAIL_CONSOLE=1 prints login codes to the log and is not allowed when PUBLIC_URL is https.")
    if not SSO_ENABLED and not (EMAIL_LOGIN and SMTP_HOST):
        raise RuntimeError("Configure a way to sign in: SSO (OIDC_ISSUER, OIDC_CLIENT_ID, OIDC_CLIENT_SECRET) "
                           "or emailed codes (SMTP_HOST, SMTP_FROM).")
    if EMAIL_LOGIN and SMTP_HOST and not SMTP_FROM:
        raise RuntimeError("Set SMTP_FROM (the sender address of the login emails).")
