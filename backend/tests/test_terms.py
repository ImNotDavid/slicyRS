"""Terms and safety acknowledgement (3.6)."""
import time

from app import store
from conftest import make_user
from test_pipeline import pooled_job


def test_no_terms_means_nothing_is_asked(client):
    store.settings.delete("terms")
    assert client.get("/api/terms").json()["needed"] is False


def test_terms_must_be_accepted_before_printing_and_again_after_a_change(client, fleet):
    c = make_user(client, "t.reader@imperial.ac.uk")
    v1 = client.put("/api/admin/terms", json={"text": "Never leave a print unattended for its first layer."}).json()
    me = c.get("/api/me").json()
    assert me["terms_needed"] is True
    jid = pooled_job(me, "t", time.time())
    store.jobs.update(jid, phase="previewed", state="previewed", draft=True, pooled=False)
    assert c.post(f"/api/jobs/{jid}/submit", json={}).status_code == 428
    assert c.post("/api/terms/accept").json()["version"] == v1["version"]
    assert c.get("/api/me").json()["terms_needed"] is False
    assert c.post(f"/api/jobs/{jid}/submit", json={}).status_code == 200
    client.put("/api/admin/terms", json={"text": "New rule: wear gloves for resin."})
    assert c.get("/api/me").json()["terms_needed"] is True
    assert make_user(client, "t.nosy@imperial.ac.uk").put("/api/admin/terms", json={"text": "x"}).status_code == 403
    client.put("/api/admin/terms", json={"text": ""})
    assert c.get("/api/me").json()["terms_needed"] is False
    store.jobs.delete(jid)
