"""Operators and admins see who is using each printer; ordinary users do not."""
import uuid

from app import store
from conftest import make_user


def _printer(**kw):
    pid = uuid.uuid4().hex[:8]
    store.printers.put(pid, {"id": pid, "name": "P", "model": "P1S", "status": {"state": "IDLE"}, "enabled": True, **kw})
    return pid


def _job(pid, owner, phase, name="part.stl", at=1):
    jid = uuid.uuid4().hex[:8]
    store.jobs.put(jid, {"id": jid, "name": name, "owner_email": owner, "printer_id": pid, "phase": phase, "phase_at": at, "created": at})
    return jid


def entry(client, pid):
    return next(p for p in client.get("/api/printers").json() if p["id"] == pid)


def test_admin_sees_current_users_once_each(client):
    pid = _printer()
    _job(pid, "a@imperial.ac.uk", "printing")
    _job(pid, "a@imperial.ac.uk", "printing", name="second.stl")
    _job(pid, "b@imperial.ac.uk", "printing")
    e = entry(client, pid)
    assert sorted(u["email"] for u in e["users"]) == ["a@imperial.ac.uk", "b@imperial.ac.uk"] and e["last_user"] is None


def test_idle_printer_names_the_last_user(client):
    pid = _printer()
    _job(pid, "old@imperial.ac.uk", "printed", at=1)
    _job(pid, "new@imperial.ac.uk", "printed", at=5)
    e = entry(client, pid)
    assert e["users"] == [] and e["last_user"]["email"] == "new@imperial.ac.uk"


def test_a_finished_job_still_counts_until_the_plate_is_cleared(client):
    pid = _printer(bed={"state": "occupied"})
    _job(pid, "c@imperial.ac.uk", "printed")
    assert [u["email"] for u in entry(client, pid)["users"]] == ["c@imperial.ac.uk"]


def test_ordinary_users_do_not_see_names_but_operators_do(client):
    pid = _printer()
    _job(pid, "d@imperial.ac.uk", "printing")
    plain = make_user(client, "plain.user@imperial.ac.uk")
    assert "users" not in entry(plain, pid)
    op = make_user(client, "an.operator@imperial.ac.uk")
    store.users.put(*[(u["id"], {**u, "operator": True}) for u in store.users.all() if u["email"] == "an.operator@imperial.ac.uk"][0])
    assert [u["email"] for u in entry(op, pid)["users"]] == ["d@imperial.ac.uk"]
