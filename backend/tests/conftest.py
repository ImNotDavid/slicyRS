import json
import os
import re
import sys
import tempfile
import time
from pathlib import Path

import pytest

# Isolated data dir must be set before the app is imported.
_TMP = tempfile.mkdtemp(prefix="slicyrs-test-")
os.environ["SLICY_DATA_DIR"] = _TMP
os.environ["SLICER_MODE"] = "mock"
os.environ["DEV_AUTH"] = "1"
os.environ["RETENTION_INTERVAL_HOURS"] = "0"   # no background clean-up thread in tests; they call retention.run() directly
os.environ["MIN_FREE_MB"] = "1"
os.environ["ALLOCATOR_INTERVAL_S"] = "0"   # tests call allocator.tick() themselves
os.environ["EMAIL_CONSOLE"] = "1"   # login codes land in app.mailer.outbox instead of being sent
os.environ["NOTIFY_SYNC"] = "1"          # notifications are delivered inline so tests can read them
os.environ["ADMIN_EMAILS"] = "admin@imperial.ac.uk"
os.environ["SECRET_KEY"] = "test-secret-key-not-for-production"
os.environ["PUBLIC_URL"] = "http://testserver"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient  # noqa: E402

from app import store  # noqa: E402
from app.main import app  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"
VALID = FIXTURES / "valid"
INVALID = FIXTURES / "invalid"
MANIFEST = json.loads((FIXTURES / "manifest.json").read_text())
DESKTOP = Path.home() / "Desktop"


ADMIN = "admin@imperial.ac.uk"


@pytest.fixture(scope="session")
def client():
    """Signed in as the bootstrap admin (most tests exercise the full feature set)."""
    with TestClient(app) as c:
        assert c.post("/auth/dev-login", json={"email": ADMIN}).status_code == 200
        yield c


def make_user(admin_client, email, role="user"):
    """Invite `email` (if new) and return a separate signed-in client with its own cookie jar."""
    r = admin_client.post("/api/admin/users", json={"email": email, "role": role})
    assert r.status_code in (200, 409), r.text
    c = TestClient(app)
    assert c.post("/auth/dev-login", json={"email": email}).status_code == 200
    return c


def _status(state="IDLE", ams=None):
    return {"state": state, "ams": ams or []}


def ams(kind, slot=0):
    return [{"slot": slot, "type": kind, "color": "#888888", "remain": 90}]


@pytest.fixture()
def fleet(client):
    """Deterministic fleet: wipes demo printers and any jobs, then adds known printers."""
    for p in store.printers.all():
        store.printers.delete(p["id"])
    for j in store.jobs.all():
        store.jobs.delete(j["id"])
    for m in store.models.all():
        store.models.delete(m["id"])
    ids = {}
    for key, model, status in [
        ("mini", "A1 mini", _status("IDLE", ams("PLA"))),
        ("p1s", "P1S", _status("IDLE", ams("PLA"))),
        ("x1c", "X1C", _status("IDLE", ams("ABS"))),
        ("h2d", "H2D", _status("IDLE", ams("PLA"))),
    ]:
        r = client.post("/api/admin/printers", json={"name": key, "model": model, "status": status})
        assert r.status_code == 200, r.text
        ids[key] = r.json()["id"]
    return ids


def upload(client, path: Path, name: str | None = None):
    with open(path, "rb") as fh:
        return client.post("/api/models", files={"file": (name or path.name, fh)})


def wait_job(client, job_id: str, timeout=60):
    end = time.time() + timeout
    while time.time() < end:
        j = client.get(f"/api/jobs/{job_id}").json()
        if j["state"] != "slicing":
            return j
        time.sleep(0.1)
    raise TimeoutError(job_id)


def slice_model(client, model_id: str, filament="PLA", **kw):
    r = client.post("/api/slice", json={"model_id": model_id, "filament": filament, **kw})
    if r.status_code != 200:
        return r, None
    return r, wait_job(client, r.json()["id"])


def parse_gcode(text: str):
    """Return dict with extrusion XY bounds, max Z, layer Z list and E sanity."""
    xs, ys, zs, e_bad = [], [], [], 0
    x = y = z = 0.0
    for line in text.splitlines():
        if not line.startswith("G1"):
            continue
        d = {t[0]: float(t[1:]) for t in line.split(";")[0].split()[1:] if re.match(r"^[XYZEF]-?[\d.]+$", t)}
        x, y, z = d.get("X", x), d.get("Y", y), d.get("Z", z)
        if "E" in d:
            if d["E"] <= 0:
                e_bad += 1
            xs.append(x); ys.append(y); zs.append(z)
    return {"xmin": min(xs), "xmax": max(xs), "ymin": min(ys), "ymax": max(ys),
            "zmax": max(zs), "layers": sorted(set(zs)), "e_bad": e_bad}
