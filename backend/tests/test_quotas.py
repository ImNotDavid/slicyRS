"""Quotas (3.2): per person per group per admin-set term; refused when over; operators top up."""
import time

from app import groups as G, quotas, store
from conftest import make_user
from test_pipeline import pooled_job

DAY = 86400


def _setup(client, hours=2, grams=None):
    now = time.time()
    assert client.put("/api/admin/periods", json=[{"name": "Autumn", "start": now - 10 * DAY, "end": now + 60 * DAY}]).status_code == 200
    d = G.default_group()
    client.put(f"/api/admin/groups/{d['id']}", json={"name": d["name"], "default_role": "member", "quota_hours": hours, "quota_g": grams})
    return d


def _previewed(owner, hours, grams=10):
    jid = pooled_job(owner, "q", time.time())
    store.jobs.update(jid, phase="previewed", state="previewed", draft=True, pooled=False, preview={"time_s": hours * 3600, "filament_g": grams})
    return jid


def test_quota_counts_and_refuses_and_top_up_helps(client, fleet):
    d = _setup(client, hours=2)
    c = make_user(client, "q.student@imperial.ac.uk")
    me = c.get("/api/me").json()
    a = _previewed(me, 1.5)
    assert c.post(f"/api/jobs/{a}/submit", json={}).status_code == 200
    q = next(x for x in c.get("/api/me/quota").json() if x["group_id"] == d["id"])
    assert q["hours"]["used"] == 1.5 and q["hours"]["warn"] is False and q["grams"] is None
    b = _previewed(me, 1.0)
    r = c.post(f"/api/jobs/{b}/submit", json={})
    assert r.status_code == 409 and "0.5 h left" in r.json()["detail"]
    lead = make_user(client, "q.lead@imperial.ac.uk")
    store.users.update(next(u for u in store.users.all() if u["email"] == "q.lead@imperial.ac.uk")["id"], operator=True)
    assert lead.post(f"/api/groups/{d['id']}/members/{me['id']}/topup", json={"hours": 1}).status_code == 200
    assert c.post(f"/api/jobs/{b}/submit", json={}).status_code == 200
    q = next(x for x in c.get("/api/me/quota").json() if x["group_id"] == d["id"])
    assert q["hours"]["allowance"] == 3 and q["hours"]["warn"] is True
    assert c.post(f"/api/groups/{d['id']}/members/{me['id']}/topup", json={"hours": 5}).status_code == 403
    client.put(f"/api/admin/groups/{d['id']}", json={"name": d["name"], "default_role": "member"})
    for jid in (a, b):
        store.jobs.delete(jid)


def test_cancelled_jobs_and_other_terms_do_not_count(client, fleet):
    d = _setup(client, hours=1)
    c = make_user(client, "q.other@imperial.ac.uk")
    me = c.get("/api/me").json()
    old = pooled_job(me, "old", time.time() - 30 * DAY)
    store.jobs.update(old, preview={"time_s": 3600}, submitted_at=time.time() - 30 * DAY)
    gone = pooled_job(me, "gone", time.time())
    store.jobs.update(gone, phase="cancelled", preview={"time_s": 3600})
    assert quotas.usage(me["id"], d["id"], quotas.current_period())["hours"] == 0
    for jid in (old, gone):
        store.jobs.delete(jid)
    client.put(f"/api/admin/groups/{d['id']}", json={"name": d["name"], "default_role": "member"})


def test_overlapping_terms_are_refused(client):
    now = time.time()
    r = client.put("/api/admin/periods", json=[{"name": "A", "start": now, "end": now + 10 * DAY}, {"name": "B", "start": now + 5 * DAY, "end": now + 20 * DAY}])
    assert r.status_code == 400 and "overlap" in r.json()["detail"]


def test_weekly_and_monthly_quotas_start_again_on_calendar_boundaries(client, fleet):
    from datetime import datetime
    assert client.put("/api/admin/quota-cycle", json={"cycle": "fortnight"}).status_code == 400
    assert make_user(client, "q.nosy@imperial.ac.uk").put("/api/admin/quota-cycle", json={"cycle": "week"}).status_code == 403
    try:
        assert client.put("/api/admin/quota-cycle", json={"cycle": "week"}).json()["cycle"] == "week"
        wed = datetime(2026, 11, 18, 15, 0).timestamp()
        w = quotas.current_period(wed)
        assert datetime.fromtimestamp(w["start"]) == datetime(2026, 11, 16) and datetime.fromtimestamp(w["end"]) == datetime(2026, 11, 23)
        client.put("/api/admin/quota-cycle", json={"cycle": "month"})
        m = quotas.current_period(datetime(2026, 12, 31, 23, 0).timestamp())
        assert m["name"] == "December 2026" and datetime.fromtimestamp(m["end"]) == datetime(2027, 1, 1)
        d = _setup(client, hours=1)
        c = make_user(client, "q.monthly@imperial.ac.uk")
        me = c.get("/api/me").json()
        last_month = pooled_job(me, "lm", time.time())
        store.jobs.update(last_month, preview={"time_s": 3600}, submitted_at=quotas.current_period()["start"] - 60)
        assert quotas.usage(me["id"], d["id"], quotas.current_period())["hours"] == 0      # last month does not count
        q = next(x for x in c.get("/api/me/quota").json() if x["group_id"] == d["id"])
        assert q["cycle"] == "month" and q["hours"]["left"] == 1
        store.jobs.delete(last_month)
        client.put(f"/api/admin/groups/{d['id']}", json={"name": d["name"], "default_role": "member"})
    finally:
        client.put("/api/admin/quota-cycle", json={"cycle": "term"})
