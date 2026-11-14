"""slicyRS web application: assembles the route modules (`app/routes/`), the security middleware and the web app files."""
import os
import uuid
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from . import allocator, auth, bedstate, gateways, outcomes, retention, sessions, slicer, store, worker  # noqa: F401  (slicer: tests patch it here)
from .bambu import links  # noqa: F401  (tests reach the live links here)
from .config import COOKIE_SECURE, MAX_WORKERS, SECRET_KEY, SESSION_HOURS
from .routes import admin, catalog, filament as filament_routes, home, jobpage, parts, jobs, models, notifications, printers, queue as queue_routes

app = FastAPI(title="slicyRS")
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY, https_only=COOKIE_SECURE, same_site="lax",
                   max_age=SESSION_HOURS * 3600, session_cookie="slicyrs_session")


@app.middleware("http")
async def csrf_origin_check(request: Request, call_next):
    """Browsers always send Origin on cross-site writes: it must name this host (SameSite=Lax is the second layer)."""
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        origin = request.headers.get("origin")
        if origin and origin.split("://", 1)[-1] != request.headers.get("host"):
            return JSONResponse({"detail": "Cross-origin request blocked"}, status_code=403)
    return await call_next(request)


CONTENT_SECURITY_POLICY = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; "
                           "font-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'; "
                           "object-src 'none'")


@app.middleware("http")
async def security_headers(request: Request, call_next):
    """Set on every response, so they hold behind any reverse proxy (or none)."""
    response = await call_next(request)
    h = response.headers
    h.setdefault("Content-Security-Policy", CONTENT_SECURITY_POLICY)
    h.setdefault("X-Content-Type-Options", "nosniff")
    h.setdefault("X-Frame-Options", "DENY")
    h.setdefault("Referrer-Policy", "same-origin")
    h.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=()")
    h.setdefault("Cross-Origin-Opener-Policy", "same-origin")
    if COOKIE_SECURE:
        h.setdefault("Strict-Transport-Security", "max-age=31536000")
    if request.url.path.startswith(("/api/", "/auth/")):
        h.setdefault("Cache-Control", "no-store")  # never let a browser or proxy keep account, job or printer data
    return response


@app.on_event("startup")
def _startup() -> None:
    if not store.printers.all() and os.environ.get("SEED_DEMO", "0") == "1":
        for name, model, state, ams in [
            ("Bay 1 – P1S", "P1S", "IDLE", [{"slot": 0, "type": "PLA", "color": "#2f6bff", "remain": 80}]),
            ("Bay 2 – A1 mini", "A1 mini", "IDLE", [{"slot": 0, "type": "PETG", "color": "#ffffff", "remain": 60}]),
            ("Bay 3 – X1C", "X1C", "RUNNING", [{"slot": 0, "type": "ABS", "color": "#111111", "remain": 90}]),
            ("Bay 4 – H2D", "H2D", "IDLE", [{"slot": 0, "type": "PLA", "color": "#e11d48", "remain": 70}]),
        ]:
            pid = uuid.uuid4().hex[:8]
            store.printers.put(pid, {"id": pid, "name": name, "model": model, "demo": True,
                                     "status": {"state": state, "ams": ams}})
    links.sync()
    sessions.purge_expired()
    outcomes.sweep()
    bedstate.reset_all()   # after a restart nothing is known about any build plate
    worker.start_embedded(int(os.environ.get("EMBED_WORKERS", str(MAX_WORKERS))))
    allocator.start_background(float(os.environ.get("ALLOCATOR_INTERVAL_S", "5")))
    retention.start_background()


for _router in (auth.router, gateways.router, catalog.router, models.router, printers.router, admin.router, notifications.router, home.router, parts.router, filament_routes.router, jobpage.router, queue_routes.router, jobs.router):
    app.include_router(_router)

# ------------------------------------------------------------------- static

_dist = Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"
if _dist.exists():
    app.mount("/", StaticFiles(directory=_dist, html=True), name="web")
