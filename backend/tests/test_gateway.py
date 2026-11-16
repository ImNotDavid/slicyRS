"""Milestone A.7: the printer gateway. A real server on a real socket, a real gateway agent connecting out to it, and fake
printer links (the LAN protocol itself is covered by test_print.py)."""
import asyncio
import json
import threading
import time
import uuid
import zipfile
from pathlib import Path

import pytest
import uvicorn
from starlette.websockets import WebSocketDisconnect

from app import bedstate, gateways, jobstate, store
from app.bambulan import PrinterError
from app.gateway_agent import Agent
from app.main import app
from app.storage import JOBS, files

CODE = "12345678"


@pytest.fixture(scope="module")
def live(client):
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning", lifespan="off"))
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    end = time.time() + 10
    while not server.started and time.time() < end:
        time.sleep(0.05)
    yield f"http://127.0.0.1:{server.servers[0].sockets[0].getsockname()[1]}"
    server.should_exit = True
    t.join(5)


class FakeLink:
    def __init__(self, cfg, agent):
        self.cfg, self.agent = cfg, agent
        self.received: list[tuple[str, bytes]] = []
        self.error: Exception | None = None
        self.starts = True

    def send_print(self, path: Path, remote_name: str, plate="Metadata/plate_1.gcode", timelapse=False, slot=None):
        if self.error:
            raise self.error
        self.received.append((remote_name, path.read_bytes()))
        return self.starts

    def stop(self):
        pass


class Running:
    """An agent running in its own thread and event loop, as it would on a Raspberry Pi."""

    def __init__(self, url, token, **kw):
        self.links: dict[str, FakeLink] = {}
        self.agent = Agent(url, token, name="test-gateway", link_factory=self._make, **kw)
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _make(self, cfg):
        link = self.links[cfg["id"]] = FakeLink(cfg, self.agent)
        return link

    def _run(self):
        asyncio.set_event_loop(self.loop)
        self.task = self.loop.create_task(self.agent.run_forever())
        try:
            self.loop.run_until_complete(self.task)
        except asyncio.CancelledError:
            pass

    def stop(self):
        self.loop.call_soon_threadsafe(self.task.cancel)
        self.thread.join(5)


def wait_for(cond, timeout=10, what="condition"):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return
        time.sleep(0.05)
    raise TimeoutError(what)


@pytest.fixture()
def site(client, live):
    """A gateway record, a printer behind it and a connected agent."""
    for p in store.printers.all():
        store.printers.delete(p["id"])
    for g in store.gateways.all():
        store.gateways.delete(g["id"])
    g = client.post("/api/admin/gateways", json={"name": "lab-" + uuid.uuid4().hex[:4]}).json()
    r = client.post("/api/admin/printers", json={"name": "Behind", "model": "P1S", "host": "192.168.1.50", "serial": "SN1",
                                                 "access_code": CODE, "gateway": g["id"]})
    assert r.status_code == 200, r.text
    pid = r.json()["id"]
    run = Running(live, g["token"])
    wait_for(lambda: gateways.hub.online(g["id"]) and pid in run.links, what="gateway to connect and receive its printer")
    yield type("Site", (), {"gw": g, "pid": pid, "run": run, "link": run.links[pid], "url": live})
    run.stop()


def job_with_bundle(owner, pid, payload=b"PK\x03\x04 sliced plate"):
    jid = uuid.uuid4().hex[:12]
    files.create(JOBS, jid)
    with zipfile.ZipFile(files.path(JOBS, jid, "model.gcode.3mf"), "w") as z:
        z.writestr("Metadata/plate_1.gcode", payload.decode("latin1"))
    store.jobs.put(jid, {"id": jid, "name": "Cube.stl", "state": "done", "phase": "ready", "created": time.time(), "owner_id": owner["id"],
                         "owner_email": owner["email"], "printer_id": pid, "printer_name": "Behind", "printer_model": "P1S",
                         "filament": "PLA", "preset": "p", "gcode": "model.gcode", "bundle": "model.gcode.3mf", "queued": False, "printed": False})
    return jid


# ------------------------------------------------------------------ tokens and admin

def test_token_is_shown_once_and_only_its_hash_is_kept(client):
    g = client.post("/api/admin/gateways", json={"name": "tok-" + uuid.uuid4().hex[:4]}).json()
    assert g["token"].startswith("gw_") and g["online"] is False
    stored = store.gateways.get(g["id"])
    assert g["token"] not in json.dumps(stored) and "token" not in client.get("/api/admin/gateways").json()[0]
    assert gateways.authenticate(g["token"])["id"] == g["id"] and gateways.authenticate("gw_wrong") is None
    assert client.post("/api/admin/gateways", json={"name": g["name"].upper()}).status_code == 409


def test_only_admins_manage_gateways(client):
    from conftest import make_user
    user = make_user(client, "plain.user@imperial.ac.uk")
    assert user.get("/api/admin/gateways").status_code == 403 and user.post("/api/admin/gateways", json={"name": "x"}).status_code == 403


def test_a_printer_needs_a_real_gateway_and_a_gateway_with_printers_cannot_be_deleted(client, site):
    r = client.post("/api/admin/printers", json={"name": "P", "model": "P1S", "host": "192.168.1.9", "serial": "S", "access_code": "1", "gateway": "nope"})
    assert r.status_code == 400
    assert client.delete(f"/api/admin/gateways/{site.gw['id']}").status_code == 409
    printer = client.get("/api/printers").json()[0]
    assert printer["gateway"] == site.gw["id"]


# ------------------------------------------------------------------ the connection

def test_a_gateway_receives_its_printers_with_their_access_codes(client, site):
    cfg = site.link.cfg
    assert (cfg["host"], cfg["serial"], cfg["access_code"]) == ("192.168.1.50", "SN1", CODE)
    assert store.printers.get(site.pid)["access_code"] != CODE            # sealed in the database, plain only on the wire
    assert client.get("/api/admin/gateways").json()[0]["online"] is True


def test_changing_a_printer_updates_the_gateway(client, site):
    client.put(f"/api/admin/printers/{site.pid}", json={"name": "Behind", "model": "P1S", "host": "192.168.1.51", "serial": "SN1", "gateway": site.gw["id"]})
    wait_for(lambda: site.run.agent.configs.get(site.pid, {}).get("host") == "192.168.1.51", what="new address to reach the gateway")
    assert site.run.agent.configs[site.pid]["access_code"] == CODE        # a blank code keeps the stored one


def test_status_from_the_gateway_drives_the_printer_and_its_plate(client, site):
    site.run.agent.on_status(site.pid, {"state": "RUNNING", "progress": 10})
    wait_for(lambda: store.printers.get(site.pid)["status"].get("state") == "RUNNING", what="status to arrive")
    assert bedstate.get(store.printers.get(site.pid))["state"] == "occupied"
    site.run.agent.on_status(site.pid, {"state": "FINISH"})
    wait_for(lambda: bedstate.get(store.printers.get(site.pid)).get("finished"), what="finish to arrive")


def test_a_gateway_cannot_report_for_a_printer_it_does_not_serve(client, site):
    other = client.post("/api/admin/gateways", json={"name": "intruder-" + uuid.uuid4().hex[:4]}).json()
    with client.websocket_connect("/gateway/ws") as ws:
        ws.send_text(json.dumps({"type": "hello", "token": other["token"], "name": "x", "version": "1"}))
        assert json.loads(ws.receive_text())["type"] == "welcome"
        ws.send_text(json.dumps({"type": "status", "printer_id": site.pid, "update": {"state": "RUNNING"}}))
        time.sleep(0.5)
    assert store.printers.get(site.pid)["status"].get("state") != "RUNNING"


def test_a_wrong_token_is_refused(client, live):
    with client.websocket_connect("/gateway/ws") as ws:
        ws.send_text(json.dumps({"type": "hello", "token": "gw_not_a_real_token", "name": "x", "version": "1"}))
        with pytest.raises(WebSocketDisconnect) as e:
            ws.receive_text()
        assert e.value.code == 4401
    with client.websocket_connect("/gateway/ws") as ws:
        ws.send_text(json.dumps({"type": "status", "printer_id": "x", "update": {}}))     # no hello first
        with pytest.raises(WebSocketDisconnect):
            ws.receive_text()


def test_disconnecting_marks_the_printers_offline(client, site):
    site.run.agent.on_status(site.pid, {"state": "IDLE"})
    wait_for(lambda: store.printers.get(site.pid)["status"].get("state") == "IDLE")
    site.run.stop()
    wait_for(lambda: not gateways.hub.online(site.gw["id"]), what="server to notice")
    wait_for(lambda: store.printers.get(site.pid)["status"].get("state") == "OFFLINE", what="printer to go offline")


# ------------------------------------------------------------------ printing through a gateway

def test_print_goes_through_the_gateway_and_the_file_arrives_intact(client, site):
    me = client.get("/api/me").json()
    jid = job_with_bundle(me, site.pid)
    bedstate.set_state(site.pid, "clear", "manual")
    r = client.post(f"/api/jobs/{jid}/print")
    assert r.status_code == 200 and r.json()["started"] is True, r.text
    name, data = site.link.received[-1]
    assert name == r.json()["file"] and data == files.path(JOBS, jid, "model.gcode.3mf").read_bytes()
    job = store.jobs.get(jid)
    assert job["phase"] == "printing" and job["remote_name"] == name
    assert bedstate.get(store.printers.get(site.pid))["state"] == "occupied"


def test_a_printer_error_reaches_the_user_unchanged(client, site):
    me = client.get("/api/me").json()
    jid = job_with_bundle(me, site.pid)
    bedstate.set_state(site.pid, "clear", "manual")
    site.link.error = PrinterError("The printer rejected the access code (FTP login failed)")
    r = client.post(f"/api/jobs/{jid}/print")
    assert r.status_code == 502 and "rejected the access code" in r.json()["detail"]
    assert store.jobs.get(jid)["phase"] == "ready"


def test_printing_while_the_gateway_is_away_says_so(client, site):
    me = client.get("/api/me").json()
    jid = job_with_bundle(me, site.pid)
    bedstate.set_state(site.pid, "clear", "manual")
    site.run.stop()
    wait_for(lambda: not gateways.hub.online(site.gw["id"]))
    bedstate.set_state(site.pid, "clear", "manual")
    r = client.post(f"/api/jobs/{jid}/print")
    assert r.status_code == 502 and "offline" in r.json()["detail"]


def test_a_file_can_be_fetched_only_by_the_gateway_it_was_offered_to(client, site):
    path = files.path(JOBS, job_with_bundle(client.get("/api/me").json(), site.pid), "model.gcode.3mf")
    token = gateways.hub.offer_file(path, site.gw["id"])
    ok = {"Authorization": f"Bearer {site.gw['token']}"}
    assert client.get(f"/gateway/transfer/{token}", headers=ok).content == path.read_bytes()
    other = client.post("/api/admin/gateways", json={"name": "thief-" + uuid.uuid4().hex[:4]}).json()
    assert client.get(f"/gateway/transfer/{token}", headers={"Authorization": f"Bearer {other['token']}"}).status_code == 404
    assert client.get(f"/gateway/transfer/{token}").status_code == 404
    assert client.get(f"/gateway/transfer/{token}", headers={"Authorization": "Bearer gw_wrong"}).status_code == 404


def test_the_admin_connection_test_can_run_from_the_gateway(client, live):
    g = client.post("/api/admin/gateways", json={"name": "prb-" + uuid.uuid4().hex[:4]}).json()
    seen = []
    run = Running(live, g["token"], probe=lambda host, port, serial, code: seen.append((host, port, serial, code)) or {"ok": True, "state": "IDLE", "ams": []})
    try:
        wait_for(lambda: gateways.hub.online(g["id"]))
        r = client.post("/api/admin/printers/test", json={"host": "192.168.1.77", "port": 8883, "serial": "SNX", "access_code": CODE, "gateway": g["id"]})
        assert r.status_code == 200 and r.json()["ok"] is True and seen == [("192.168.1.77", 8883, "SNX", CODE)]
        assert client.post("/api/admin/printers/test", json={"host": "127.0.0.1", "serial": "S", "access_code": "1", "gateway": g["id"]}).status_code == 400
    finally:
        run.stop()
        wait_for(lambda: not gateways.hub.online(g["id"]))


def test_a_gateway_can_only_store_known_status_fields_and_not_huge_ones(client, site):
    assert gateways.clean_status({"state": "IDLE", "evil": "x", "nozzle_c": 200}) == {"state": "IDLE", "nozzle_c": 200}
    assert gateways.clean_status({"hms": ["x" * 30_000]}) == {} and gateways.clean_status("nope") == {}
    site.run.agent.on_status(site.pid, {"state": "IDLE", "access_code": "stolen", "host": "10.0.0.1"})
    wait_for(lambda: store.printers.get(site.pid)["status"].get("state") == "IDLE")
    rec = store.printers.get(site.pid)
    assert "access_code" not in rec["status"] and "host" not in rec["status"] and rec["host"] == "192.168.1.50"
