"""The staff queue (2.2): reorder the pool, line jobs up for one printer, and the allocator follows."""
import time

from app import allocator, queue, store, upkeep
from conftest import make_user
from test_pipeline import pooled_job


def only(fleet, *keys):
    for k, pid in fleet.items():
        store.printers.update(pid, enabled=k in keys, bed={"state": "clear", "source": "manual", "at": time.time()},
                              status={"state": "IDLE", "ams": []}, maintenance=None, attention=None)


def test_members_cannot_see_or_change_the_queue(client, fleet):
    c = make_user(client, "q.member@imperial.ac.uk")
    assert c.get("/api/queue").status_code == 403
    assert c.post("/api/queue/move", json={"job_id": "x", "lane": "pool", "index": 0}).status_code == 403


def test_reordering_the_pool_changes_who_goes_first(client, fleet):
    only(fleet, "mini")
    me = client.get("/api/me").json()
    first, second = pooled_job(me, "first", 100), pooled_job(me, "second", 200)
    store.printers.update(fleet["mini"], enabled=False)                     # nothing free while we rearrange
    assert [j["id"] for j in queue.lane_jobs("pool")][:2] == [first, second]
    assert client.post("/api/queue/move", json={"job_id": second, "lane": "pool", "index": 0}).json()["position"] == 1
    assert [j["id"] for j in queue.lane_jobs("pool")][:2] == [second, first]
    store.printers.update(fleet["mini"], enabled=True)
    allocator.assign_pool()
    assert store.jobs.get(second)["phase"] in ("assigned", "slicing") and store.jobs.get(first)["phase"] == "pooled"


def test_a_job_lined_up_for_a_printer_waits_for_that_one(client, fleet):
    only(fleet, "mini", "p1s")
    store.printers.update(fleet["p1s"], status={"state": "RUNNING", "ams": []})       # busy
    me = client.get("/api/me").json()
    pinned, other = pooled_job(me, "pinned", 100), pooled_job(me, "other", 200)
    store.printers.update(fleet["mini"], enabled=False)
    assert client.post("/api/queue/move", json={"job_id": pinned, "lane": fleet["p1s"], "index": 0}).status_code == 200
    store.printers.update(fleet["mini"], enabled=True)
    allocator.assign_pool()
    assert store.jobs.get(pinned)["phase"] == "pooled"                   # mini is free but the job waits for the P1S
    assert store.jobs.get(other)["printer_id"] == fleet["mini"]
    allocator.assign_pool()
    assert store.jobs.get(pinned)["waiting_for"].startswith("Lined up for p1s")
    assert {l["id"]: l for l in client.get("/api/queue").json()["lanes"]}[fleet["p1s"]]["jobs"][0]["id"] == pinned


def test_a_job_cannot_be_lined_up_where_it_cannot_print(client, fleet):
    only(fleet, "mini", "x1c")
    me = client.get("/api/me").json()
    abs_job = pooled_job(me, "abs", 100)
    store.jobs.update(abs_job, filament="ABS")
    r = client.post("/api/queue/move", json={"job_id": abs_job, "lane": fleet["mini"], "index": 0})
    assert r.status_code == 409 and "enclosed" in r.json()["detail"]
    upkeep.set_maintenance(fleet["x1c"], True, "belts", {"email": "t@x"})
    r = client.post("/api/queue/move", json={"job_id": abs_job, "lane": fleet["x1c"], "index": 0})
    assert r.status_code == 409 and "maintenance" in r.json()["detail"]
    lanes = {l["id"]: l for l in client.get("/api/queue").json()["lanes"]}
    card = next(c for c in lanes["pool"]["jobs"] if c["id"] == abs_job)
    assert card["fits"][fleet["mini"]] and card["fits"][fleet["x1c"]]
    upkeep.set_maintenance(fleet["x1c"], False, "", {"email": "t@x"})


def test_let_the_pool_decide_clears_every_hand_placed_order(client, fleet):
    only(fleet, "mini")
    store.printers.update(fleet["mini"], enabled=False)
    me = client.get("/api/me").json()
    a, b = pooled_job(me, "a", 100), pooled_job(me, "b", 200)
    client.post("/api/queue/move", json={"job_id": b, "lane": "pool", "index": 0})
    assert client.post("/api/queue/reset").json()["reset"] >= 2
    assert [j["id"] for j in queue.lane_jobs("pool")][:2] == [a, b]
    assert store.jobs.get(a)["queue_rank"] is None


def test_only_waiting_jobs_can_be_moved(client, fleet):
    me = client.get("/api/me").json()
    jid = pooled_job(me, "printing", 100)
    store.jobs.update(jid, phase="printing")
    assert client.post("/api/queue/move", json={"job_id": jid, "lane": "pool", "index": 0}).status_code == 409
