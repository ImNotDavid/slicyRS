"""Filament cost (3.4) and audit log export (3.5)."""
import csv
import io
import time

from app import costs, store
from conftest import make_user
from test_pipeline import pooled_job


def _done(owner, name, grams, filament="PLA", outcome="printed", percent=None, share=None):
    jid = pooled_job(owner, name, time.time())
    res = {"outcome": "failed", "percent": percent} if outcome == "failed" else {"outcome": "finished"}
    store.jobs.update(jid, phase=outcome, filament=filament, stats={"filament_g": grams}, result=res, phase_at=time.time(), plate_share=share)
    return jid


def test_cost_uses_the_spool_price_else_the_default_and_counts_failed_shares(client):
    c = make_user(client, "cost.student@imperial.ac.uk")
    me = c.get("/api/me").json()
    a = _done(me, "a", 100)                                   # printed from a priced spool: 20 per kg
    b = _done(me, "b", 200, filament="PETG")                  # no spool: default price 30 per kg
    f = _done(me, "f", 100, outcome="failed", percent=50)     # half printed: 50 g at the PLA default
    t = _done(me, "t", 80, filament="TPU")                    # no price at all
    sid = "costspool"
    store.spools.put(sid, {"id": sid, "material": "PLA", "start_g": 1000, "remaining_g": 900, "price": 20, "used": [{"job_id": a, "grams": 100, "at": time.time()}]})
    assert client.put("/api/admin/costs/prices", json={"per_kg": {"PLA": 25, "PETG": 30, "TPU": None}}).json() == {"PLA": 25, "PETG": 30}
    assert c.put("/api/admin/costs/prices", json={"per_kg": {"PLA": 1}}).status_code == 403
    r = client.get("/api/admin/costs", params={"start": time.time() - 60}).json()
    me_row = next(p for p in r["by_person"] if p["key"] == "cost.student@imperial.ac.uk")
    assert me_row["prints"] == 4 and me_row["grams"] == 430 and me_row["unpriced_g"] == 80
    assert me_row["cost"] == round(0.1 * 20 + 0.2 * 30 + 0.05 * 25, 2)
    text = client.get("/api/admin/costs.csv", params={"start": time.time() - 60}).text
    rows = list(csv.DictReader(io.StringIO(text)))
    byname = {r["Print"]: r for r in rows if r["Email"] == "cost.student@imperial.ac.uk"}
    assert byname["a"]["Price from"] == "PLA" and byname["b"]["Price from"] == "default price" and byname["t"]["Cost"] == ""
    for jid in (a, b, f, t):
        store.jobs.delete(jid)
    store.spools.delete(sid)
    costs.set_prices({})


def test_cost_report_by_term_and_csv_escapes_formulas(client):
    now = time.time()
    client.put("/api/admin/periods", json=[{"id": "termx", "name": "Spring", "start": now - 3600, "end": now + 3600}])
    assert client.get("/api/admin/costs", params={"period": "nope"}).status_code == 404
    assert client.get("/api/admin/costs", params={"period": "termx"}).json()["start"] == now - 3600
    out = costs.to_csv([{"job": "=HYPERLINK(1)", "grams": -1}], [("job", "Print"), ("grams", "Grams")])
    assert "'=HYPERLINK(1)" in out and ",-1" in out


def test_audit_log_exports_as_csv_for_admins_only(client):
    c = make_user(client, "audit.nosy@imperial.ac.uk")
    assert c.get("/api/admin/audit.csv").status_code == 403
    r = client.get("/api/admin/audit.csv", params={"start": time.time() - 3600})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv") and "attachment" in r.headers["content-disposition"]
    assert r.text.splitlines()[0] == "When,Who,Action,Detail"
