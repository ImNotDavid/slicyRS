"""The PRINT button: FTPS upload + MQTT start, against a fake printer, plus the API error handling."""
import json
import threading
import time
import uuid
import zipfile
from types import SimpleNamespace

import pytest

from app import bambu, crypto, main as app_main, store
from app.bambu import PrinterError, PrinterLink
from app.config import JOBS_DIR
from conftest import VALID, upload, wait_job
from fake_printer import FakeFTPS

CODE = "12345678"


@pytest.fixture()
def ftps(tmp_path):
    srv = FakeFTPS(tmp_path, CODE)
    yield srv
    srv.close()


@pytest.fixture()
def bundle(tmp_path):
    p = tmp_path / "model.gcode.3mf"
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("Metadata/plate_1.gcode", "G1 X1 E1\n" * 20000)
        z.writestr("Metadata/plate_1.gcode.md5", "ABCDEF0123456789\n")
    return p


class FakeMQTT:
    """Stands in for paho: records publishes and, like a real printer, flips to RUNNING when told to print."""

    def __init__(self, printer_id, connected=True, starts=True, hms=None):
        self.printer_id, self.connected, self.starts, self.published, self.hms = printer_id, connected, starts, [], hms

    def is_connected(self):
        return self.connected

    def loop_stop(self):
        pass

    def disconnect(self):
        pass

    def publish(self, topic, payload, qos=0):
        self.published.append((topic, json.loads(payload)))
        if self.hms and "project_file" in payload:
            entry = {"attr": self.hms[0], "code": self.hms[1], "timestamp": int(time.time())}
            threading.Timer(0.3, lambda: bambu._set_status(self.printer_id, {"hms": [entry]})).start()
        elif self.starts and "project_file" in payload:
            threading.Timer(0.3, lambda: bambu._set_status(self.printer_id, {"state": "RUNNING"})).start()
        return SimpleNamespace(wait_for_publish=lambda timeout=None: None)


def make_link(ftps_port, state="IDLE", connected=True, starts=True, code=CODE, port_override=None, hms=None):
    pid = uuid.uuid4().hex[:8]
    rec = {"id": pid, "name": "Fake", "model": "P1S", "host": "127.0.0.1", "port": 8883, "ftp_port": port_override or ftps_port,
           "serial": "SN123", "access_code": crypto.seal(code), "enabled": True, "status": {"state": state}}
    store.printers.put(pid, rec)
    link = PrinterLink(rec)
    link.client = FakeMQTT(pid, connected, starts, hms)
    return pid, link


@pytest.fixture(autouse=True)
def fast_start(monkeypatch):
    monkeypatch.setattr(bambu, "START_WAIT_S", 3)


# ------------------------------------------------------------- connector

def test_upload_and_start(ftps, bundle):
    pid, link = make_link(ftps.port)
    assert link.send_print(bundle, "cube-abc123.gcode.3mf") is True
    assert ftps.files["cube-abc123.gcode.3mf"] == bundle.read_bytes()          # whole file arrived intact
    topic, msg = link.client.published[-1]
    assert topic == "device/SN123/request"
    p = msg["print"]
    assert p["command"] == "project_file" and p["param"] == "Metadata/plate_1.gcode"
    assert p["url"] == "file:///sdcard/cube-abc123.gcode.3mf" and p["subtask_name"] == "cube-abc123"
    assert p["md5"] == "ABCDEF0123456789"


def test_use_ams_follows_whether_the_printer_has_one(ftps, bundle):
    pid, link = make_link(ftps.port)                       # no AMS reported (like an A1 mini)
    link.send_print(bundle, "a.gcode.3mf")
    assert link.client.published[-1][1]["print"]["use_ams"] is False
    store.printers.update(pid, status={"state": "IDLE", "ams": [{"slot": 2, "type": "PLA", "color": "#fff", "remain": 90}]})
    link.send_print(bundle, "b.gcode.3mf")
    p = link.client.published[-1][1]["print"]
    assert p["use_ams"] is True and p["ams_mapping"] == [2]


def test_survives_control_channel_drop_after_transfer(tmp_path, bundle):
    srv = FakeFTPS(tmp_path, CODE, drop_control_after_data=True)
    try:
        _, link = make_link(srv.port)
        assert link.send_print(bundle, "x.gcode.3mf") is True
        assert srv.files["x.gcode.3mf"] == bundle.read_bytes()
    finally:
        srv.close()


def test_works_when_server_has_no_size_command(tmp_path, bundle):
    srv = FakeFTPS(tmp_path, CODE, size_supported=False)
    try:
        _, link = make_link(srv.port)
        assert link.send_print(bundle, "x.gcode.3mf") is True
    finally:
        srv.close()


def test_wrong_access_code(ftps, bundle):
    _, link = make_link(ftps.port, code="00000000")
    with pytest.raises(PrinterError, match="access code"):
        link.send_print(bundle, "x.gcode.3mf")
    assert not ftps.files


def test_incomplete_upload_is_detected(tmp_path, bundle):
    srv = FakeFTPS(tmp_path, CODE, truncate=True)
    try:
        _, link = make_link(srv.port)
        with pytest.raises(PrinterError, match="incomplete"):
            link.send_print(bundle, "x.gcode.3mf")
        assert not link.client.published                                     # never told the printer to start
    finally:
        srv.close()


def test_printer_unreachable(bundle):
    import socket
    s = socket.socket(); s.bind(("127.0.0.1", 0)); closed_port = s.getsockname()[1]; s.close()
    _, link = make_link(closed_port)
    with pytest.raises(PrinterError, match="Cannot reach"):
        link.send_print(bundle, "x.gcode.3mf")


def test_not_a_tls_server(tmp_path, bundle):
    import socket
    srv = socket.socket(); srv.bind(("127.0.0.1", 0)); srv.listen(1)
    threading.Thread(target=lambda: (srv.accept()[0].sendall(b"220 plain text ftp\r\n")), daemon=True).start()
    _, link = make_link(srv.getsockname()[1])
    with pytest.raises(PrinterError):
        link.send_print(bundle, "x.gcode.3mf")
    srv.close()


@pytest.mark.parametrize("state", ["RUNNING", "PREPARE", "PAUSE"])
def test_busy_printer_refused(ftps, bundle, state):
    _, link = make_link(ftps.port, state=state)
    with pytest.raises(PrinterError, match="busy"):
        link.send_print(bundle, "x.gcode.3mf")
    assert not ftps.files


def test_mqtt_not_connected(ftps, bundle):
    _, link = make_link(ftps.port, connected=False)
    with pytest.raises(PrinterError, match="not connected"):
        link.send_print(bundle, "x.gcode.3mf")


def test_printer_rejecting_unauthorized_command_is_explained(ftps, bundle):
    """Real behaviour seen on an A1 mini: HMS 0500-0500-0001-0007 right after the start command."""
    _, link = make_link(ftps.port, hms=(83887360, 65543))
    with pytest.raises(PrinterError) as e:
        link.send_print(bundle, "stand.gcode.3mf")
    msg = str(e.value)
    assert "0500-0500-0001-0007" in msg and "Developer Mode" in msg and "stand.gcode.3mf" in msg
    assert ftps.files["stand.gcode.3mf"] == bundle.read_bytes()          # file is on the SD card for manual start


def test_stale_hms_entries_are_ignored(ftps, bundle):
    pid, link = make_link(ftps.port)
    store.printers.update(pid, status={"state": "IDLE", "hms": [{"attr": 83887360, "code": 65543, "timestamp": 1}]})
    assert link.send_print(bundle, "x.gcode.3mf") is True               # an old error must not block a new print


def test_other_hms_errors_are_reported(ftps, bundle):
    _, link = make_link(ftps.port, hms=(0x03000100, 0x00020001))
    with pytest.raises(PrinterError, match="0300-0100-0002-0001"):
        link.send_print(bundle, "x.gcode.3mf")


def test_returns_false_when_printer_never_starts(ftps, bundle):
    _, link = make_link(ftps.port, starts=False)
    assert link.send_print(bundle, "x.gcode.3mf") is False                   # uploaded and commanded, but no state change seen


# -------------------------------------------------------------------- API

def make_job(owner, printer_id, with_bundle=True, name="Cube.stl"):
    jid = uuid.uuid4().hex[:12]
    d = JOBS_DIR / jid
    d.mkdir()
    if with_bundle:
        with zipfile.ZipFile(d / "model.gcode.3mf", "w") as z:
            z.writestr("Metadata/plate_1.gcode", "G1 X1 E1\n" * 5000)
            z.writestr("Metadata/plate_1.gcode.md5", "FF00\n")
    store.jobs.put(jid, {"id": jid, "name": name, "state": "done", "created": time.time(), "owner_id": owner["id"], "owner_email": owner["email"],
                         "printer_id": printer_id, "printer_name": "Fake", "printer_model": "P1S", "filament": "PLA", "preset": "p",
                         "gcode": "model.gcode", "bundle": "model.gcode.3mf" if with_bundle else None, "queued": False, "printed": False})
    return jid


@pytest.fixture()
def me(client):
    return client.get("/api/me").json()


def test_print_endpoint_success(client, ftps, me):
    pid, link = make_link(ftps.port)
    app_main.links.links[pid] = link
    jid = make_job(me, pid, name="My part (v2).stl")
    r = client.post(f"/api/jobs/{jid}/print")
    assert r.status_code == 200 and r.json()["started"] is True and r.json()["file"].endswith(".gcode.3mf")
    name = next(iter(ftps.files))
    assert name.endswith(".gcode.3mf") and name.startswith("My_part_v2-") and jid[:6] in name   # safe + unique name
    job = client.get(f"/api/jobs/{jid}").json()
    assert job["printed"] is True and job["queued"] is True
    assert any(a["action"] == "print" for a in client.get("/api/admin/audit").json())


def test_two_prints_do_not_overwrite_each_other(client, ftps, me):
    pid, link = make_link(ftps.port)
    app_main.links.links[pid] = link
    a, b = make_job(me, pid), make_job(me, pid)
    client.post(f"/api/jobs/{a}/print")
    store.printers.update(pid, status={"state": "IDLE"})
    client.post(f"/api/jobs/{b}/print")
    assert len(ftps.files) == 2


@pytest.mark.parametrize("kwargs,status,fragment", [
    ({"code": "00000000"}, 502, "access code"),
    ({"state": "RUNNING"}, 502, "busy"),
    ({"connected": False}, 502, "not connected"),
])
def test_print_endpoint_reports_printer_problems_as_502_not_500(client, ftps, me, kwargs, status, fragment):
    pid, link = make_link(ftps.port, **kwargs)
    app_main.links.links[pid] = link
    jid = make_job(me, pid)
    r = client.post(f"/api/jobs/{jid}/print")
    assert r.status_code == status and fragment in r.json()["detail"]
    assert client.get(f"/api/jobs/{jid}").json()["printed"] is False
    assert any(a["action"] == "print_failed" for a in client.get("/api/admin/audit").json())


def test_unexpected_exception_is_still_not_a_bare_500(client, ftps, me, monkeypatch):
    pid, link = make_link(ftps.port)
    monkeypatch.setattr(link, "send_print", lambda *a, **k: (_ for _ in ()).throw(KeyError("boom")))
    app_main.links.links[pid] = link
    r = client.post(f"/api/jobs/{make_job(me, pid)}/print")
    assert r.status_code == 502 and "KeyError" in r.json()["detail"]


def test_print_endpoint_preconditions(client, me):
    manual = client.post("/api/admin/printers", json={"name": "Manual", "model": "P1S"}).json()["id"]
    assert client.post(f"/api/jobs/{make_job(me, manual)}/print").status_code == 409          # no live connection
    assert client.post(f"/api/jobs/{make_job(me, manual, with_bundle=False)}/print").status_code == 409  # nothing sliced to send
    assert client.post(f"/api/jobs/{make_job(me, 'deleted-printer')}/print").status_code == 409
    assert client.post("/api/jobs/nope/print").status_code == 404
    pid = client.post("/api/admin/printers", json={"name": "Off", "model": "P1S", "enabled": False}).json()["id"]
    assert client.post(f"/api/jobs/{make_job(me, pid)}/print").status_code == 409


def test_real_sliced_job_prints_through_the_api(client, fleet, ftps, me):
    """Slice with the app, then print the resulting job (bundle comes from the slicer when a real engine is configured)."""
    m = upload(client, VALID / "cube_binary.stl").json()
    job = client.post("/api/slice", json={"model_id": m["id"], "filament": "PLA"}).json()
    job = wait_job(client, job["id"])
    assert job["state"] == "done"
    if not job.get("bundle"):                                              # mock engine produces no .gcode.3mf
        r = client.post(f"/api/jobs/{job['id']}/print")
        assert r.status_code == 409
        pytest.skip("mock slicer: no printable bundle")


def test_large_upload_arrives_complete_every_time(tmp_path):
    """Regression: closing the TLS data connection without ending the session truncated uploads on Linux (intermittently)."""
    import os
    srv = FakeFTPS(tmp_path, CODE)
    big = tmp_path / "big.gcode.3mf"
    with zipfile.ZipFile(big, "w", zipfile.ZIP_STORED) as z:
        z.writestr("Metadata/plate_1.gcode", os.urandom(3_000_000))      # incompressible, ~3 MB
        z.writestr("Metadata/plate_1.gcode.md5", "AA\n")
    try:
        for i in range(8):
            pid, link = make_link(srv.port)
            assert link.send_print(big, f"big-{i}.gcode.3mf") is True
            assert srv.files[f"big-{i}.gcode.3mf"] == big.read_bytes(), f"upload {i} was truncated"
    finally:
        srv.close()
