"""Approval workflow (3.3): a role right, length or filament limits, or a material send a print to a group operator first."""
import time

from app import groups as G, store
from conftest import make_user
from test_pipeline import pooled_job


def _previewed(owner, hours=1.0, grams=20, filament="PLA"):
    jid = pooled_job(owner, "ap", time.time())
    store.jobs.update(jid, phase="previewed", state="previewed", draft=True, pooled=False, filament=filament,
                      preview={"time_s": hours * 3600, "filament_g": grams})
    return jid


def _group(client, **approve):
    d = G.default_group()
    body = {"name": d["name"], "default_role": "member", **approve}
    assert client.put(f"/api/admin/groups/{d['id']}", json=body).status_code == 200
    return d


def _operator(client, email):
    c = make_user(client, email)
    store.users.update(next(u for u in store.users.all() if u["email"] == email)["id"], operator=True)
    return c


def test_limits_and_materials_send_prints_for_approval_and_operators_decide(client, fleet):
    d = _group(client, approve_over_h=2, approve_materials=["TPU"])
    c = make_user(client, "ap.student@imperial.ac.uk")
    me = c.get("/api/me").json()
    ok = _previewed(me, hours=1)
    assert c.post(f"/api/jobs/{ok}/submit", json={}).json()["phase"] == "pooled"
    long = _previewed(me, hours=3)
    j = c.post(f"/api/jobs/{long}/submit", json={}).json()
    assert j["phase"] == "awaiting_approval" and j["pooled"] is False and "longer than 2 h" in j["approval"]["reasons"][0]
    tpu = _previewed(me, hours=1, filament="TPU")
    assert c.post(f"/api/jobs/{tpu}/submit", json={}).json()["approval"]["reasons"] == ["TPU prints need approval"]

    op = _operator(client, "ap.op@imperial.ac.uk")
    assert op.get("/api/me").json()["can"]["approve"] is True
    assert {x["id"] for x in op.get("/api/approvals").json()} >= {long, tpu}
    assert c.get("/api/approvals").status_code == 403
    assert c.post(f"/api/jobs/{long}/approve", json={}).status_code == 403          # not your own approval
    assert op.post(f"/api/jobs/{tpu}/reject", json={"note": ""}).status_code == 400   # a rejection says why
    r = op.post(f"/api/jobs/{tpu}/reject", json={"note": "TPU jams the AMS; use the P1S with an external spool"}).json()
    assert r["phase"] == "rejected" and "TPU jams" in r["error"]
    sub = store.jobs.get(long)["submitted_at"]
    a = op.post(f"/api/jobs/{long}/approve", json={}).json()
    assert a["pooled"] is True and a["approval"]["decision"] == "approved" and a["submitted_at"] == sub
    assert op.post(f"/api/jobs/{long}/approve", json={}).status_code == 409
    for jid in (ok, long, tpu):
        store.jobs.delete(jid)
    _group(client)


def test_role_right_needs_approval_and_approvers_and_admins_are_exempt(client, fleet):
    d = _group(client)
    client.put("/api/admin/roles/member", json={"name": "Member", "desc": "", "rights": ["needs_approval"]})
    try:
        c = make_user(client, "ap.new@imperial.ac.uk")
        me = c.get("/api/me").json()
        jid = _previewed(me)
        assert c.post(f"/api/jobs/{jid}/submit", json={}).json()["phase"] == "awaiting_approval"
        assert c.post(f"/api/jobs/{jid}/cancel").json()["phase"] == "cancelled"
        op = _operator(client, "ap.op2@imperial.ac.uk")
        own = _previewed(op.get("/api/me").json())
        assert op.post(f"/api/jobs/{own}/submit", json={}).json()["phase"] == "pooled"
        admin = client.get("/api/me").json()
        mine = _previewed(admin)
        assert client.post(f"/api/jobs/{mine}/submit", json={}).json()["phase"] == "pooled"
        for x in (jid, own, mine):
            store.jobs.delete(x)
    finally:
        client.put("/api/admin/roles/member", json={"name": "Member", "desc": "", "rights": []})


def test_existing_operator_role_gains_the_approve_right_once(client):
    op = store.roles.get("operator")
    store.roles.update("operator", rights=[r for r in op["rights"] if r != "approve"], rights_added=[])
    G.ensure()
    assert "approve" in store.roles.get("operator")["rights"]
    store.roles.update("operator", rights=[r for r in store.roles.get("operator")["rights"] if r != "approve"])
    G.ensure()                                   # an admin removed it on purpose: it stays removed
    assert "approve" not in store.roles.get("operator")["rights"]
    store.roles.update("operator", rights=[*store.roles.get("operator")["rights"], "approve"])
