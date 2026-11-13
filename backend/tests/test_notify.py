"""The notification service: who is told, over which channel, and what each person can switch off."""
import time
import uuid

import pytest

from app import alerts, mailer, notify, store
from conftest import make_user


def user(client, email, **fields):
    make_user(client, email)
    u = next(x for x in store.users.all() if x["email"] == email)
    return store.users.update(u["id"], **fields) if fields else u


def sent_to(email, kind=None):
    return [m for m in mailer.outbox if m["to"] == email and (kind is None or kind in m["subject"])]


@pytest.fixture(autouse=True)
def clean():
    mailer.outbox.clear()
    notify.deliveries.clear()
    for c in store.channels.all():
        store.channels.delete(c["id"])
    yield


def test_owner_gets_a_default_on_event_and_not_a_default_off_one(client):
    user(client, "n.owner@imperial.ac.uk")
    notify.emit("finished", "done", "body", owners=["n.owner@imperial.ac.uk"])
    notify.emit("pickup_at_risk", "risk", "body", owners=["n.owner@imperial.ac.uk"])
    assert len(sent_to("n.owner@imperial.ac.uk")) == 1 and sent_to("n.owner@imperial.ac.uk")[0]["subject"] == "done"


def test_a_person_can_opt_out_and_opt_in(client):
    u = user(client, "n.choice@imperial.ac.uk", notify={"finished": False, "pickup_late": True})
    notify.emit("finished", "done", "b", owners=[u["email"]])
    notify.emit("pickup_late", "late", "b", owners=[u["email"]])
    assert [m["subject"] for m in sent_to(u["email"])] == ["late"]


def test_preferences_api_round_trip(client):
    c = make_user(client, "n.api@imperial.ac.uk")
    prefs = {p["kind"]: p["on"] for p in c.get("/api/me/notifications").json()}
    assert prefs["finished"] is True and prefs["pickup_at_risk"] is False and "plate_waiting" not in prefs   # operators only
    after = {p["kind"]: p["on"] for p in c.put("/api/me/notifications", json={"finished": False}).json()}
    assert after["finished"] is False
    assert c.put("/api/me/notifications", json={"nonsense": True}).status_code == 400


def test_operators_get_the_short_text_and_owners_the_full_one(client):
    owner = user(client, "n.o1@imperial.ac.uk")
    op = user(client, "n.op1@imperial.ac.uk", operator=True)
    notify.emit("print_paused", "paused", "long text", owners=[owner["email"]], operator_body="short")
    assert sent_to(owner["email"])[0]["body"].startswith("long text") and sent_to(op["email"])[0]["body"].startswith("short")


def test_an_operator_who_owns_the_job_is_told_once(client):
    op = user(client, "n.both@imperial.ac.uk", operator=True)
    notify.emit("failed", "failed", "long", owners=[op["email"]], operator_body="short")
    assert len(sent_to(op["email"])) == 1


def test_operator_only_events_do_not_reach_owners(client):
    owner = user(client, "n.plain@imperial.ac.uk")
    op = user(client, "n.op2@imperial.ac.uk", operator=True)
    notify.emit("plate_waiting", "wait", "b", owners=[owner["email"]])
    assert not sent_to(owner["email"]) and len(sent_to(op["email"])) == 1


def test_links_point_at_the_app(client):
    u = user(client, "n.link@imperial.ac.uk")
    notify.emit("finished", "done", "body", owners=[u["email"]], link="/#/jobs")
    assert sent_to(u["email"])[0]["body"].endswith("http://testserver/#/jobs")


# ---- Discord channels

class FakeResp:
    def read(self):
        return b""


def test_only_discord_webhook_addresses_are_accepted(client):
    for bad in ("http://discord.com/api/webhooks/1/x", "https://evil.example/api/webhooks/1/x", "https://discord.com/other", "https://169.254.169.254/"):
        assert client.post("/api/admin/notifications/channels", json={"name": "x", "url": bad, "events": ["failed"]}).status_code == 400


def test_a_channel_gets_only_its_events_and_the_url_stays_secret(client, monkeypatch):
    posts = []
    monkeypatch.setattr(notify.urllib.request, "urlopen", lambda req, timeout=0: posts.append((req.full_url, req.data)) or FakeResp())
    r = client.post("/api/admin/notifications/channels", json={"name": "operators", "url": "https://discord.com/api/webhooks/123/SECRETTOKEN", "events": ["failed"]})
    assert r.status_code == 200 and "SECRETTOKEN" not in r.text and r.json()["url_hint"].endswith("KEN")
    assert "SECRETTOKEN" not in client.get("/api/admin/notifications").text
    notify.emit("finished", "ignored", "b")
    notify.emit("failed", "Print failed", "details", operator_body="short details")
    assert len(posts) == 1 and posts[0][0].endswith("SECRETTOKEN") and b"short details" in posts[0][1] and b"allowed_mentions" in posts[0][1]


def test_a_failed_post_is_recorded_for_admins_and_does_not_raise(client, monkeypatch):
    def boom(req, timeout=0):
        raise OSError("connection refused")
    monkeypatch.setattr(notify.urllib.request, "urlopen", boom)
    client.post("/api/admin/notifications/channels", json={"name": "ops", "url": "https://discord.com/api/webhooks/1/t", "events": ["failed"]})
    notify.emit("failed", "x", "y")
    recent = client.get("/api/admin/notifications").json()["recent"]
    assert recent[0]["channel"] == "discord" and recent[0]["ok"] is False and "refused" in recent[0]["error"]


def test_channel_management_needs_an_admin(client):
    plain = make_user(client, "n.notadmin@imperial.ac.uk")
    assert plain.get("/api/admin/notifications").status_code == 403
    assert plain.post("/api/admin/notifications/channels", json={"name": "x", "url": "https://discord.com/api/webhooks/1/t"}).status_code == 403


# ---- mid-print alerts

def printing(owner_email, name="Bracket"):
    pid = uuid.uuid4().hex[:8]
    store.printers.put(pid, {"id": pid, "name": "P1", "model": "P1S", "status": {"state": "RUNNING"}})
    jid = uuid.uuid4().hex[:8]
    store.jobs.put(jid, {"id": jid, "name": name, "owner_email": owner_email, "printer_id": pid, "phase": "printing", "printed": True,
                         "started_at": time.time() - 300, "created": time.time() - 400})
    return pid, jid


def test_a_pause_alerts_owner_and_operator_once(client):
    owner = user(client, "n.paused@imperial.ac.uk")
    op = user(client, "n.op3@imperial.ac.uk", operator=True)
    pid, _ = printing(owner["email"])
    alerts.on_status(pid, "RUNNING", {"state": "PAUSE", "layer_num": 12})
    alerts.on_status(pid, "PAUSE", {"state": "PAUSE", "layer_num": 12})        # same state again: nothing new
    assert len(sent_to(owner["email"])) == 1 and "layer 12" in sent_to(owner["email"])[0]["body"] and len(sent_to(op["email"])) == 1
    alerts.on_status(pid, "RUNNING", {"state": "RUNNING"})
    alerts.on_status(pid, "RUNNING", {"state": "PAUSE", "layer_num": 20})      # paused again later: a new event
    assert len(sent_to(owner["email"])) == 2


def test_a_printer_error_code_is_reported_once_per_print(client):
    owner = user(client, "n.err@imperial.ac.uk")
    pid, jid = printing(owner["email"])
    hms = [{"attr": 0x05000500, "code": 0x00010007, "timestamp": int(time.time())}]
    alerts.on_status(pid, "RUNNING", {"state": "RUNNING", "hms": hms})
    alerts.on_status(pid, "RUNNING", {"state": "RUNNING", "hms": hms})
    assert len(sent_to(owner["email"], "error")) == 1 and "Developer Mode" in sent_to(owner["email"])[0]["body"]


def test_old_error_codes_from_before_the_print_do_not_alert(client):
    owner = user(client, "n.olderr@imperial.ac.uk")
    pid, _ = printing(owner["email"])
    alerts.on_status(pid, "RUNNING", {"state": "RUNNING", "hms": [{"attr": 1, "code": 2, "timestamp": int(time.time()) - 86400}]})
    assert not sent_to(owner["email"])


def test_losing_the_printer_mid_print_alerts_and_idle_printers_do_not(client):
    owner = user(client, "n.lost@imperial.ac.uk")
    pid, jid = printing(owner["email"])
    alerts.on_status(pid, "RUNNING", {"state": "OFFLINE"})
    assert len(sent_to(owner["email"], "offline")) == 1
    store.jobs.update(jid, phase="printed")
    mailer.outbox.clear()
    alerts.on_status(pid, "RUNNING", {"state": "PAUSE"})
    assert not mailer.outbox


def test_a_shared_plate_tells_every_owner(client):
    a, b = user(client, "n.share.a@imperial.ac.uk"), user(client, "n.share.b@imperial.ac.uk")
    pid, jid = printing(a["email"], "Part A")
    t = store.jobs.get(jid)["started_at"]
    other = uuid.uuid4().hex[:8]
    store.jobs.put(other, {"id": other, "name": "Part B", "owner_email": b["email"], "printer_id": pid, "phase": "printing", "printed": True, "started_at": t, "created": t})
    alerts.on_status(pid, "RUNNING", {"state": "PAUSE", "layer_num": 3})
    assert len(sent_to(a["email"])) == 1 and len(sent_to(b["email"])) == 1
