"""Pause, resume and stop from the web (2.4): who may, what is sent, and what people are told."""
import time
import uuid

import pytest

from app import adapter, alerts, mailer, notify, outcomes, store, upkeep
from conftest import make_user


class FakeLink:
    def __init__(self, pid):
        self.pid, self.sent = pid, []

    def control(self, action):
        self.sent.append(action)
        new = {"pause": "PAUSE", "resume": "RUNNING", "stop": "FAILED"}[action]
        st = (store.printers.get(self.pid) or {}).get("status") or {}
        store.printers.update(self.pid, status={**st, "state": new})
        return True


def user(client, email, **fields):
    c = make_user(client, email)
    u = next(x for x in store.users.all() if x["email"] == email)
    if fields:
        store.users.update(u["id"], **fields)
    return c, store.users.get(u["id"])


@pytest.fixture
def printing(monkeypatch):
    links = {}

    def make(owners):
        pid = uuid.uuid4().hex[:8]
        store.printers.put(pid, {"id": pid, "name": "PC", "model": "P1S", "enabled": True, "status": {"state": "RUNNING", "subtask_name": "pc-" + pid}})
        jobs = []
        t = time.time() - 100
        for i, o in enumerate(owners):
            jid = uuid.uuid4().hex[:8]
            store.jobs.put(jid, {"id": jid, "name": f"Part {i}", "owner_id": o["id"], "owner_email": o["email"], "printer_id": pid, "printer_name": "PC",
                                 "phase": "printing", "printed": True, "state": "done", "started_at": t, "created": t, "remote_name": f"pc-{pid}.gcode.3mf"})
            jobs.append(jid)
        if len(jobs) > 1:                          # a shared plate: the first job leads, the others are members
            store.jobs.update(jobs[0], members=jobs[1:])
            for m in jobs[1:]:
                store.jobs.update(m, batch_lead=jobs[0], remote_name=None)
        links[pid] = FakeLink(pid)
        return pid, jobs
    monkeypatch.setattr(adapter, "get", lambda pid: links.get(pid))
    mailer.outbox.clear()
    return make, links


def test_owner_can_pause_and_resume_and_others_cannot(client, printing):
    make, links = printing
    oc, owner = user(client, "pc.owner@imperial.ac.uk")
    sc, _ = user(client, "pc.stranger@imperial.ac.uk")
    pid, (jid,) = make([owner])
    assert sc.post(f"/api/jobs/{jid}/pause").status_code == 404
    r = oc.post(f"/api/jobs/{jid}/pause")
    assert r.status_code == 200 and r.json()["done"] and links[pid].sent == ["pause"]
    assert oc.post(f"/api/jobs/{jid}/pause").status_code == 409            # already paused
    assert oc.post(f"/api/jobs/{jid}/resume").json()["state"] == "RUNNING"


def test_the_person_who_paused_is_not_emailed_but_operators_are(client, printing):
    make, links = printing
    oc, owner = user(client, "pc.pauser@imperial.ac.uk")
    _, op = user(client, "pc.op@imperial.ac.uk", operator=True)
    pid, (jid,) = make([owner])
    oc.post(f"/api/jobs/{jid}/pause")
    alerts.on_status(pid, "RUNNING", {"state": "PAUSE", "layer_num": 7})
    to = [m["to"] for m in mailer.outbox]
    assert owner["email"] not in to and op["email"] in to
    assert any("from the web by pc.pauser@imperial.ac.uk" in m["subject"] for m in mailer.outbox)


def test_a_shared_plate_can_be_paused_by_an_owner_but_only_staff_can_stop_it(client, printing):
    make, links = printing
    ac, a = user(client, "pc.share.a@imperial.ac.uk")
    _, b = user(client, "pc.share.b@imperial.ac.uk")
    pid, jobs = make([a, b])
    r = ac.post(f"/api/jobs/{jobs[0]}/stop")
    assert r.status_code == 403 and "operator or admin" in r.json()["detail"]
    assert ac.post(f"/api/jobs/{jobs[0]}/pause").status_code == 200
    assert client.post(f"/api/jobs/{jobs[0]}/stop").status_code == 200      # the admin may
    assert links[pid].sent == ["pause", "stop"]


def test_a_stop_from_the_web_is_not_a_printer_fault(client, printing):
    make, links = printing
    oc, owner = user(client, "pc.stopper@imperial.ac.uk")
    pid, (jid,) = make([owner])
    assert oc.post(f"/api/jobs/{jid}/stop").status_code == 200
    outcomes.on_status(pid, {"state": "FAILED", "subtask_name": "pc-" + pid, "layer_num": 12, "total_layer_num": 80})
    j = store.jobs.get(jid)
    assert j["result"]["summary"].startswith("Stopped from the web by pc.stopper@imperial.ac.uk") and j["phase"] == "failed"
    assert upkeep.unavailable(store.printers.get(pid)) is None              # no "needs attention" for a deliberate stop
    assert owner["email"] not in [m["to"] for m in mailer.outbox]


def test_a_printer_without_a_connection_says_to_use_the_screen(client, printing):
    make, links = printing
    oc, owner = user(client, "pc.manual@imperial.ac.uk")
    pid, (jid,) = make([owner])
    links.pop(pid)
    r = oc.post(f"/api/jobs/{jid}/pause")
    assert r.status_code == 409 and "printer's screen" in r.json()["detail"]


def test_unknown_command_is_not_found(client):
    assert client.post("/api/jobs/x/launch").status_code == 404


def test_the_gateway_passes_commands_to_its_printer_link():
    from app.gateway_agent import Agent
    agent = Agent("http://localhost:1", "t", "test")
    agent.links = {"p1": type("L", (), {"control": lambda self, a: a == "pause"})()}
    assert agent.handle_control({"printer_id": "p1", "action": "pause"}) == {"ok": True, "done": True}
    assert agent.handle_control({"printer_id": "nope", "action": "pause"})["ok"] is False
