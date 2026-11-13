"""The central permission check (2.15): today's rules, asked in one place."""
import uuid

import pytest
from fastapi import HTTPException

from app import permissions, store

ADMIN = {"id": "a1", "role": "admin", "active": True}
OP = {"id": "o1", "role": "user", "operator": True, "active": True}
MEMBER = {"id": "m1", "role": "user", "active": True}
OTHER = {"id": "m2", "role": "user", "active": True}
GONE = {"id": "a2", "role": "admin", "active": False}


@pytest.mark.parametrize("action,expected", [
    ("admin", {"a1"}), ("job.see_all", {"a1", "o1"}), ("printer.see_connection", {"a1"}), ("preset.share", {"a1"}),
    ("plate.mark_occupied", {"a1", "o1"}), ("printer.see_people", {"a1", "o1"}),
])
def test_role_only_actions(action, expected):
    assert {u["id"] for u in (ADMIN, OP, MEMBER, GONE) if permissions.can(u, action)} == expected


def test_jobs_and_models_belong_to_their_owner_and_admins():
    rec = {"owner_id": "m1"}
    assert permissions.can(MEMBER, "job.manage", rec) and permissions.can(ADMIN, "job.manage", rec)
    assert not permissions.can(OTHER, "job.manage", rec) and not permissions.can(OP, "job.manage", rec)
    assert permissions.can(MEMBER, "model.use", rec) and not permissions.can(OTHER, "model.use", rec)
    assert not permissions.can(MEMBER, "job.manage", None)


def test_preset_delete_owner_or_admin_for_shared_only():
    mine, shared_by_other = {"owner_id": "m1", "scope": "user"}, {"owner_id": "m2", "scope": "shared"}
    assert permissions.can(MEMBER, "preset.delete", mine) and not permissions.can(MEMBER, "preset.delete", shared_by_other)
    assert permissions.can(ADMIN, "preset.delete", shared_by_other) and not permissions.can(ADMIN, "preset.delete", {"owner_id": "m2", "scope": "user"})


def test_plate_clear_operators_or_any_owner_on_the_latest_plate():
    pid = uuid.uuid4().hex[:8]
    store.printers.put(pid, {"id": pid, "name": "P", "model": "P1S"})
    for owner in ("m1", "m2"):
        jid = uuid.uuid4().hex[:8]
        store.jobs.put(jid, {"id": jid, "name": "x", "owner_id": owner, "printer_id": pid, "started_at": 50, "created": 50})
    stranger = {"id": "m3", "role": "user", "active": True}
    assert permissions.can(MEMBER, "plate.clear", pid) and permissions.can(OTHER, "plate.clear", pid) and permissions.can(OP, "plate.clear", pid)
    assert not permissions.can(stranger, "plate.clear", pid)


def test_disabled_accounts_can_do_nothing():
    assert not any(permissions.can(GONE, a, {"owner_id": "a2", "scope": "shared"}) for a in permissions.ACTIONS if a != "plate.clear")


def test_require_hides_or_refuses():
    with pytest.raises(HTTPException) as e:
        permissions.require(OTHER, "job.manage", {"owner_id": "m1"}, hide=True, message="Unknown job")
    assert e.value.status_code == 404
    with pytest.raises(HTTPException) as e:
        permissions.require(MEMBER, "admin")
    assert e.value.status_code == 403


def test_unknown_action_is_a_bug_not_a_no():
    with pytest.raises(ValueError):
        permissions.can(ADMIN, "printer.launch_missiles")


def test_every_action_is_documented():
    doc = permissions.__doc__ or ""
    assert all(a in doc for a in permissions.ACTIONS)


def test_a_finished_job_on_an_occupied_plate_offers_its_owner_the_plate_check(client):
    from conftest import make_user
    c = make_user(client, "plate.owner@imperial.ac.uk")
    me = c.get("/api/me").json()
    pid = uuid.uuid4().hex[:8]
    store.printers.put(pid, {"id": pid, "name": "P", "model": "P1S", "bed": {"state": "occupied"}})
    jid = uuid.uuid4().hex[:8]
    store.jobs.put(jid, {"id": jid, "name": "x", "owner_id": me["id"], "owner_email": me["email"], "printer_id": pid, "phase": "printed",
                         "state": "done", "started_at": 70, "created": 70})
    flag = lambda: next(j for j in c.get("/api/jobs").json() if j["id"] == jid).get("plate_to_clear")
    assert flag() is True
    assert c.post(f"/api/printers/{pid}/bed/clear", json={"bed_empty": True, "plate_matches": True, "filament_matches": True}).status_code == 200
    assert not flag()


def test_my_jobs_and_all_jobs(client):
    from conftest import make_user
    c = make_user(client, "scope.member@imperial.ac.uk")
    assert c.get("/api/jobs?scope=all").status_code == 403
    assert c.get("/api/jobs").status_code == 200
    mine = client.get("/api/jobs").json()
    everyone = client.get("/api/jobs?scope=all").json()
    me = client.get("/api/me").json()
    assert all(j["owner_id"] == me["id"] for j in mine) and len(everyone) >= len(mine)
