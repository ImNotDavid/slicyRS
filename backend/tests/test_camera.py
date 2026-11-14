"""Printer cameras (2.7): the A1/P1 picture protocol, live pictures, and the photo of the finished plate."""
import socket
import struct
import threading
import time
import uuid

import pytest

from app import adapter, camera, store
from app.storage import JOBS, files
from conftest import make_user

JPEG = b"\xff\xd8" + b"picture" * 20 + b"\xff\xd9"


def frame(data):
    return struct.pack("<IIII", len(data), 0, 1, 0) + data


def test_login_packet_has_the_bambu_layout():
    p = camera.login_packet("12345678")
    assert len(p) == 80 and struct.unpack("<IIII", p[:16]) == (0x40, 0x3000, 0, 0)
    assert p[16:20] == b"bblp" and p[48:56] == b"12345678"


def test_a_frame_is_read_whole_even_in_small_pieces():
    a, b = socket.socketpair()
    def send():
        data = frame(JPEG)
        for i in range(0, len(data), 7):
            b.sendall(data[i:i + 7])
    threading.Thread(target=send).start()
    assert camera.read_frame(a) == JPEG
    a.close(); b.close()


@pytest.mark.parametrize("payload,why", [(b"not a jpeg", "JPEG"), (b"", "impossible size")])
def test_bad_frames_are_refused(payload, why):
    a, b = socket.socketpair()
    b.sendall(frame(payload) if payload else struct.pack("<IIII", 0, 0, 0, 0))
    with pytest.raises(camera.CameraError, match=why):
        camera.read_frame(a)
    a.close(); b.close()


class FakeLink:
    calls = 0
    events: list = []

    def snapshot(self):
        FakeLink.calls += 1
        FakeLink.events.append("picture")
        return JPEG

    def light(self, on):
        FakeLink.events.append("light on" if on else "light off")


def _printer(model="A1 mini"):
    pid = uuid.uuid4().hex[:8]
    store.printers.put(pid, {"id": pid, "name": "Cam", "model": model, "enabled": True, "status": {"state": "IDLE"},
                             "host": "10.0.0.9", "serial": "S", "access_code": "x"})
    return pid


def test_everyone_signed_in_can_see_a_live_picture_and_viewers_share_one_fetch(client, monkeypatch):
    pid = _printer()
    monkeypatch.setattr(adapter, "get", lambda p: FakeLink())
    FakeLink.calls = 0
    member = make_user(client, "cam.member@imperial.ac.uk")
    for c in (member, client, member):
        r = c.get(f"/api/printers/{pid}/camera.jpg")
        assert r.status_code == 200 and r.content == JPEG and r.headers["content-type"] == "image/jpeg"
    assert FakeLink.calls == 1                                   # cached for a couple of seconds
    assert next(p for p in member.get("/api/printers").json() if p["id"] == pid)["camera"] is True


def test_unsupported_models_say_so(client):
    pid = _printer("X1C")
    r = client.get(f"/api/printers/{pid}/camera.jpg")
    assert r.status_code == 404 and "not supported" in r.json()["detail"]
    assert next(p for p in client.get("/api/printers").json() if p["id"] == pid)["camera"] is False


def test_the_finished_plate_is_photographed_onto_the_job(client, monkeypatch):
    pid = _printer()
    monkeypatch.setattr(adapter, "get", lambda p: FakeLink())
    jid = uuid.uuid4().hex[:12]
    files.create(JOBS, jid)
    files.publish(JOBS, jid)
    me = client.get("/api/me").json()
    store.jobs.put(jid, {"id": jid, "name": "Photo", "owner_id": me["id"], "owner_email": me["email"], "printer_id": pid, "phase": "printed",
                         "state": "done", "created": time.time()})
    FakeLink.events = []
    monkeypatch.setattr(camera, "LIGHT_SETTLE_S", 0)
    camera.photograph_plate(jid, pid, delay=0)
    assert FakeLink.events == ["light on", "picture", "light off"]          # lit for the photo, then put back
    page = client.get(f"/api/jobs/{jid}/page").json()
    assert page["photo"].startswith(f"/api/jobs/{jid}/photo.jpg")
    assert client.get(f"/api/jobs/{jid}/photo.jpg").content == JPEG
    assert make_user(client, "cam.other@imperial.ac.uk").get(f"/api/jobs/{jid}/photo.jpg").status_code == 404


def test_the_gateway_takes_pictures_for_its_printers():
    from app.gateway_agent import Agent
    agent = Agent("http://localhost:1", "t", "test")
    agent.links = {"p1": FakeLink()}
    r = agent.handle_snapshot({"printer_id": "p1"})
    import base64
    assert r["ok"] and base64.b64decode(r["jpeg"]) == JPEG


def _self_signed(tmp_path):
    import datetime
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "printer")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key()).serial_number(1)
            .not_valid_before(now).not_valid_after(now + datetime.timedelta(days=1)).sign(key, hashes.SHA256()))
    (tmp_path / "c.pem").write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    (tmp_path / "k.pem").write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    return tmp_path / "c.pem", tmp_path / "k.pem"


def test_snapshot_over_tls_logs_in_and_returns_the_first_picture(tmp_path):
    import ssl
    cert, key = _self_signed(tmp_path)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(cert, key)
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    seen = {}

    def serve():
        conn, _ = srv.accept()
        with ctx.wrap_socket(conn, server_side=True) as s:
            seen["login"] = camera._read_exact(s, 80)
            s.sendall(frame(JPEG) + frame(b"\xff\xd8second\xff\xd9"))
    t = threading.Thread(target=serve)
    t.start()
    assert camera.snapshot("127.0.0.1", "87654321", port=port, timeout=5) == JPEG
    t.join(5)
    srv.close()
    assert seen["login"][48:56] == b"87654321"



def test_a_light_that_was_already_on_is_left_on(client, monkeypatch):
    pid = _printer()
    store.printers.update(pid, status={"state": "FINISH", "light": "on"})
    monkeypatch.setattr(adapter, "get", lambda p: FakeLink())
    FakeLink.events = []
    assert camera.lit_picture(store.printers.get(pid), settle=0) == JPEG
    assert FakeLink.events == ["picture"]


def test_the_picture_is_still_taken_if_the_light_cannot_be_switched(client, monkeypatch):
    class NoLight(FakeLink):
        def light(self, on):
            raise RuntimeError("no light")
    pid = _printer()
    monkeypatch.setattr(adapter, "get", lambda p: NoLight())
    assert camera.lit_picture(store.printers.get(pid), settle=0) == JPEG


def test_the_light_command_matches_bambu_and_the_report_is_read():
    import json as _json
    from app import bambulan
    sent, seen = [], {}
    link = bambulan.LanLink({"id": "p", "host": "h", "serial": "SER"}, "code", lambda pid, u: seen.update(u), lambda: {}, lambda: None)
    link.client.is_connected = lambda: True
    class Info:
        def wait_for_publish(self, timeout=0):
            pass
    link.client.publish = lambda topic, payload, qos=0: sent.append((topic, _json.loads(payload))) or Info()
    link.light(True)
    assert sent[0][0] == "device/SER/request" and sent[0][1]["system"]["command"] == "ledctrl" and sent[0][1]["system"]["led_mode"] == "on"
    msg = type("M", (), {"payload": _json.dumps({"print": {"lights_report": [{"node": "chamber_light", "mode": "off"}]}}).encode()})()
    link._on_message(None, None, msg)
    assert seen["light"] == "off"


def test_the_gateway_switches_the_light_for_its_printers():
    from app.gateway_agent import Agent
    agent = Agent("http://localhost:1", "t", "test")
    FakeLink.events = []
    agent.links = {"p1": FakeLink()}
    assert agent.handle_light({"printer_id": "p1", "on": True}) == {"ok": True} and FakeLink.events == ["light on"]
