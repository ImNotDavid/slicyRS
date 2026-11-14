"""Groups, roles and memberships (3.0)."""
import time

from app import allocator, groups as G, queue, store
from conftest import VALID, make_user, upload
from test_pipeline import pooled_job


def u(email):
    return next(x for x in store.users.all() if x["email"] == email)


def test_migration_keeps_todays_rights(client):
    c = make_user(client, "g.member@imperial.ac.uk")
    me = c.get("/api/me").json()
    assert [g["role"] for g in me["groups"]] == ["Member"] and me["groups"][0]["default"] and not me["can"]["all_jobs"]
    store.users.update(u("g.member@imperial.ac.uk")["id"], operator=True)          # the old flag still means operator
    me = c.get("/api/me").json()
    assert me["groups"][0]["role"] == "Operator" and me["can"]["queue"] and me["operator"]
    admin = client.get("/api/me").json()
    assert set(admin["groups"][0]["rights"]) == set(G.RIGHTS) - G.RESTRICTIONS and admin["can"]["admin"]


def test_admins_manage_roles_and_admin_is_fixed(client):
    roles = client.get("/api/admin/roles").json()
    assert [r["id"] for r in roles["roles"]][:3] == ["admin", "operator", "member"]
    assert client.put("/api/admin/roles/admin", json={"name": "Admin", "rights": []}).status_code == 409
    r = client.post("/api/admin/roles", json={"name": "Trusted member", "rights": ["pick_printer", "plates"]}).json()
    assert client.post("/api/admin/roles", json={"name": "x", "rights": ["fly"]}).status_code == 400
    assert client.delete("/api/admin/roles/member").status_code == 409
    assert client.delete(f"/api/admin/roles/{r['id']}").status_code == 200


def test_group_operators_manage_members_without_escalating(client):
    g = client.post("/api/admin/groups", json={"name": "RoboCup team"}).json()
    client.post(f"/api/admin/groups/{g['id']}/members", json={"email": "g.lead@imperial.ac.uk", "role_id": "operator"})
    lead = make_user(client, "g.lead@imperial.ac.uk")
    managed = lead.get("/api/groups/managed").json()
    assert [m["name"] for m in managed] == ["RoboCup team"]
    r = lead.post(f"/api/groups/{g['id']}/members", json={"email": "g.new@imperial.ac.uk", "role_id": "member"})
    assert r.status_code == 200 and u("g.new@imperial.ac.uk")                       # an invited account was made
    strong = client.post("/api/admin/roles", json={"name": "Canceller", "rights": ["manage_jobs"]}).json()
    assert lead.post(f"/api/groups/{g['id']}/members", json={"email": "g.x@imperial.ac.uk", "role_id": strong["id"]}).status_code == 403
    assert lead.post(f"/api/groups/{g['id']}/members", json={"email": "g.y@imperial.ac.uk", "role_id": "admin"}).status_code == 403
    new_id = u("g.new@imperial.ac.uk")["id"]
    assert lead.put(f"/api/groups/{g['id']}/members/{new_id}", json={"role_id": "operator"}).json()["role"] == "Operator"
    dflt = G.default_group()["id"]
    assert lead.delete(f"/api/groups/{dflt}/members/{new_id}").status_code in (403, 409)
    assert lead.delete(f"/api/groups/{g['id']}/members/{new_id}").status_code == 200
    member = make_user(client, "g.plain@imperial.ac.uk")
    assert member.post(f"/api/groups/{g['id']}/members", json={"email": "z@imperial.ac.uk"}).status_code == 403
    client.delete(f"/api/admin/roles/{strong['id']}")


def test_operators_see_only_their_groups_jobs(client, fleet):
    g = client.post("/api/admin/groups", json={"name": "Lab A"}).json()
    client.post(f"/api/admin/groups/{g['id']}/members", json={"email": "g.labop@imperial.ac.uk", "role_id": "operator"})
    op = make_user(client, "g.labop@imperial.ac.uk")
    me = client.get("/api/me").json()
    inside = pooled_job(me, "lab job", time.time())
    store.jobs.update(inside, group_id=g["id"])
    outside = pooled_job(me, "default job", time.time())
    ids = {j["id"] for j in op.get("/api/jobs?scope=all").json()}
    assert inside in ids and outside not in ids
    assert op.get(f"/api/jobs/{outside}/page").status_code == 404 and op.get(f"/api/jobs/{inside}/page").status_code == 200


def test_a_printer_limited_to_a_group_only_takes_that_groups_jobs(client, fleet):
    g = client.post("/api/admin/groups", json={"name": "Lab B"}).json()
    for k, pid in fleet.items():
        store.printers.update(pid, enabled=(k == "mini"), bed={"state": "clear", "source": "manual", "at": time.time()}, status={"state": "IDLE", "ams": []},
                              maintenance=None, attention=None, groups=[g["id"]] if k == "mini" else [])
    me = client.get("/api/me").json()
    other = pooled_job(me, "not lab", time.time())
    allocator.assign_pool()
    assert store.jobs.get(other)["phase"] == "pooled" and "only for other groups" in queue.fits(store.jobs.get(other), store.printers.get(fleet["mini"]))
    lab = pooled_job(me, "lab", time.time())
    store.jobs.update(lab, group_id=g["id"])
    allocator.assign_pool()
    assert store.jobs.get(lab)["printer_id"] == fleet["mini"]
    outsider = make_user(client, "g.outsider@imperial.ac.uk")
    m = upload(outsider, VALID / "cube_binary.stl").json()
    cands = outsider.post("/api/assign", json={"model_id": m["id"], "filament": "PLA"}).json()["candidates"]
    assert fleet["mini"] not in [c["printer_id"] for c in cands]
    store.printers.update(fleet["mini"], groups=[])


def test_choosing_a_printer_needs_the_right_in_that_group(client, fleet):
    for pid in fleet.values():
        store.printers.update(pid, enabled=True, groups=[], maintenance=None, attention=None)
    member = make_user(client, "g.picker@imperial.ac.uk")
    me = member.get("/api/me").json()
    jid = pooled_job(me, "pick", time.time())
    store.jobs.update(jid, phase="previewed", state="previewed", draft=True, pooled=False, owner_id=me["id"])
    r = member.post(f"/api/jobs/{jid}/submit", json={"printer_id": fleet["p1s"]})
    assert r.status_code == 403
    trusted = client.post("/api/admin/roles", json={"name": "Picker", "rights": ["pick_printer"]}).json()
    G.set_membership(me["id"], G.default_group()["id"], trusted["id"])
    r = member.post(f"/api/jobs/{jid}/submit", json={"printer_id": fleet["p1s"]})
    assert r.status_code == 200, r.text
    assert store.jobs.get(jid)["pinned_printer"] == fleet["p1s"] and store.jobs.get(jid)["group_id"] == G.default_group()["id"]
    client.delete(f"/api/admin/roles/{trusted['id']}")


def test_main_group_and_offboarding_hand_over(client):
    g = client.post("/api/admin/groups", json={"name": "Course C"}).json()
    client.post(f"/api/admin/groups/{g['id']}/members", json={"email": "g.student@imperial.ac.uk", "role_id": "member"})
    s = make_user(client, "g.student@imperial.ac.uk")
    assert s.put("/api/me/main_group", json={"group_id": g["id"]}).json()["main_group"] == g["id"]
    assert s.put("/api/me/main_group", json={"group_id": "nope"}).status_code == 400
    m = upload(s, VALID / "cube_binary.stl").json()
    sid = u("g.student@imperial.ac.uk")["id"]
    r = client.patch(f"/api/admin/users/{sid}", json={"active": False, "transfer_parts_to": "admin@imperial.ac.uk"})
    assert r.status_code == 200 and store.models.get(m["id"])["owner_id"] == client.get("/api/me").json()["id"]
    assert client.delete(f"/api/admin/groups/{G.default_group()['id']}").status_code == 409
    assert client.delete(f"/api/admin/groups/{g['id']}").status_code == 200


def test_a_group_operator_queues_only_their_groups_jobs(client, fleet):
    g = client.post("/api/admin/groups", json={"name": "Lab Q"}).json()
    client.post(f"/api/admin/groups/{g['id']}/members", json={"email": "g.qop@imperial.ac.uk", "role_id": "operator"})
    op = make_user(client, "g.qop@imperial.ac.uk")
    me = client.get("/api/me").json()
    for pid in fleet.values():
        store.printers.update(pid, enabled=False)
    mine, theirs = pooled_job(me, "lab q", time.time()), pooled_job(me, "default q", time.time())
    store.jobs.update(mine, group_id=g["id"])
    pool = next(l for l in op.get("/api/queue").json()["lanes"] if l["id"] == "pool")
    assert [c["id"] for c in pool["jobs"]] == [mine]
    assert op.post("/api/queue/move", json={"job_id": theirs, "lane": "pool", "index": 0}).status_code == 403
    for pid in fleet.values():
        store.printers.update(pid, enabled=True)


def test_a_group_can_cap_how_long_a_pool_print_may_be(client, fleet):
    dflt = G.default_group()
    assert client.put(f"/api/admin/groups/{dflt['id']}", json={"name": dflt["name"], "default_role": "member", "max_print_h": 2}).json()["max_print_h"] == 2
    me = client.get("/api/me").json()
    long_job = pooled_job(me, "long", time.time())
    store.jobs.update(long_job, phase="previewed", state="previewed", draft=True, pooled=False, preview={"time_s": 3 * 3600})
    r = client.post(f"/api/jobs/{long_job}/submit", json={})
    assert r.status_code == 409 and "at most 2 h" in r.json()["detail"]
    short = pooled_job(me, "short", time.time())
    store.jobs.update(short, phase="previewed", state="previewed", draft=True, pooled=False, preview={"time_s": 3600})
    assert client.post(f"/api/jobs/{short}/submit", json={}).status_code == 200
    client.put(f"/api/admin/groups/{dflt['id']}", json={"name": dflt["name"], "default_role": "member", "max_print_h": None})


def test_home_says_no_wait_when_a_printer_is_free_and_nothing_waits(client, fleet):
    for pid in fleet.values():
        store.printers.update(pid, bed={"state": "clear", "source": "manual", "at": time.time()}, status={"state": "IDLE", "ams": []},
                              maintenance=None, attention=None, groups=[])
    for j in store.jobs.all():
        if j.get("phase") == "pooled":
            store.jobs.update(j["id"], phase="cancelled")
    pool = client.get("/api/home").json()["pool"]
    assert pool["printers_free"] >= 1 and pool["typical_wait_s"] == 0
