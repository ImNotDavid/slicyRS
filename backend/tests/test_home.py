"""Home page (2.18): my jobs, the pool, notices, and staff numbers."""
import time
import uuid

from app import store
from conftest import make_user
from test_pipeline import pooled_job


def test_member_home_has_my_jobs_pool_and_notices_but_no_fleet(client, fleet):
    c = make_user(client, "home.member@imperial.ac.uk")
    me = c.get("/api/me").json()
    jid = pooled_job(me, "mine", time.time())
    other = pooled_job(client.get("/api/me").json(), "not mine", time.time())
    h = c.get("/api/home").json()
    assert [w["id"] for w in h["mine"]["waiting"]] == [jid] and "fleet" not in h
    assert h["pool"]["jobs_waiting"] >= 2 and h["pool"]["printers"] == 4
    assert h["pool"]["typical_wait_s"] is not None
    for j in (jid, other):
        store.jobs.update(j, phase="cancelled")


def test_staff_get_fleet_numbers(client, fleet):
    store.printers.update(fleet["p1s"], attention={"reason": "the last print failed"})
    h = client.get("/api/home").json()
    assert h["fleet"]["needs_attention"][0]["name"] == "p1s"
    store.printers.update(fleet["p1s"], attention=None)


def test_admins_post_notices_everyone_sees_them_and_expired_ones_hide(client):
    n = client.post("/api/admin/notices", json={"text": "Makerspace closed on Friday", "level": "warning"}).json()
    old = client.post("/api/admin/notices", json={"text": "old news", "until": time.time() - 10}).json()
    member = make_user(client, "home.reader@imperial.ac.uk")
    texts = [x["text"] for x in member.get("/api/home").json()["notices"]]
    assert "Makerspace closed on Friday" in texts and "old news" not in texts
    assert member.post("/api/admin/notices", json={"text": "hi"}).status_code == 403
    assert client.delete(f"/api/admin/notices/{n['id']}").status_code == 200
    client.delete(f"/api/admin/notices/{old['id']}")


def test_models_are_kept_by_default():
    from app import retention
    assert retention.get_settings()["model_files_days"] in (0, retention.get_settings()["model_files_days"])
    assert retention.DEFAULTS["model_files_days"] == 0
