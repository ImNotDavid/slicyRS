"""Milestone A.6: opt-in plates shared by several jobs, across users."""
import time

import pytest

from app import allocator, bedstate, jobstate, outcomes, plates, slicer, stats, store
from conftest import VALID, make_user, upload, wait_job
from test_pipeline import CLEAR, draft, pooled_job, wait_phase


def only(fleet, key="p1s"):
    """Leave one printer enabled and confirmed clear, so every pooled job competes for it."""
    for k, pid in fleet.items():
        store.printers.update(pid, enabled=(k == key))
    bedstate.set_state(fleet[key], "clear", "manual")
    return fleet[key]


def submit(c, job, **kw):
    r = c.post(f"/api/jobs/{job['id']}/submit", json=kw)
    assert r.status_code == 200, r.text
    return r.json()


def two_users(client):
    return make_user(client, "ann@imperial.ac.uk"), make_user(client, "bob@imperial.ac.uk")


# ----------------------------------------------------------------------------- the rules

def test_footprints_are_packed_with_gaps_and_may_turn(client):
    bed = (180, 180, 180)
    assert plates.footprints_fit([(20, 20, 20)] * 6, bed)
    assert not plates.footprints_fit([(100, 100, 10)] * 2, bed)         # two 100 mm squares do not fit on 180 mm
    assert not plates.footprints_fit([(170, 100, 10)] * 2, bed)
    assert plates.footprints_fit([(60, 170, 10)] * 2, bed)              # a long part may be turned to fit the row
    assert not plates.footprints_fit([(200, 20, 10)], bed)


def test_only_opted_in_jobs_with_the_same_settings_and_colour_are_partners(client, fleet):
    me = client.get("/api/me").json()
    printer = store.printers.get(fleet["p1s"])
    lead = pooled_job(me, "lead", 100)
    mate = pooled_job(me, "mate", 110)
    stranger = pooled_job(me, "stranger", 120)
    other_preset = pooled_job(me, "fine", 130)
    red, blue = pooled_job(me, "red", 140), pooled_job(me, "blue", 150)
    store.jobs.update(lead, allow_batching=True)
    store.jobs.update(mate, allow_batching=True)
    store.jobs.update(other_preset, allow_batching=True, spec={"preset": "0.12mm Fine", "filament": "PLA", "settings": {}})
    store.jobs.update(red, allow_batching=True, colour="red")
    store.jobs.update(blue, allow_batching=True, colour="blue")
    pool = [j for j in store.jobs.all() if j["phase"] == "pooled"]
    got = {j["id"] for j in plates.find_partners(store.jobs.get(lead), printer, pool, time.time())}
    assert mate in got and red in got                                    # same settings, "any" colour matches red
    assert stranger not in got and other_preset not in got               # not opted in / different settings
    assert blue not in got                                               # red and blue cannot share a plate


def test_different_settings_may_share_a_plate_if_filament_and_layer_height_match(client, fleet):
    me = client.get("/api/me").json()
    printer = store.printers.get(fleet["p1s"])
    lead = pooled_job(me, "standard", 100)
    strong = pooled_job(me, "strong", 110)
    edited = pooled_job(me, "edited", 120)
    fine = pooled_job(me, "fine", 130)
    petg = pooled_job(me, "petg", 140)
    for j in (lead, strong, edited, fine, petg):
        store.jobs.update(j, allow_batching=True)
    store.jobs.update(strong, spec={"preset": "0.20mm Strength", "filament": "PLA", "settings": {}})
    store.jobs.update(edited, spec={"preset": "0.20mm Standard", "filament": "PLA", "settings": {"wall_loops": 4, "outer_wall_speed": 80, "skirt_loops": 3}})
    store.jobs.update(fine, spec={"preset": "0.12mm Fine", "filament": "PLA", "settings": {}})
    store.jobs.update(petg, filament="PETG", spec={"preset": "0.20mm Standard", "filament": "PETG", "settings": {}})
    L = store.jobs.get(lead)
    c = plates.compatibility(L, store.jobs.get(strong), printer)
    assert c["object_settings"]["wall_loops"] == 6 and c["object_settings"]["sparse_infill_density"] == 25
    c = plates.compatibility(L, store.jobs.get(edited), printer)
    assert c["object_settings"]["wall_loops"] == 4 and c["object_settings"]["outer_wall_speed"] == 80
    assert "skirt_loops" in c["plate_wide_differences"] and "skirt_loops" not in c["object_settings"]   # a whole-plate setting follows the lead
    assert plates.compatibility(L, store.jobs.get(fine), printer) is None        # different layer height
    assert plates.compatibility(L, store.jobs.get(petg), printer) is None        # different filament
    pool = [j for j in store.jobs.all() if j["phase"] == "pooled"]
    assert {j["id"] for j in plates.find_partners(L, printer, pool, time.time())} == {strong, edited}


def test_a_shared_plate_gives_each_part_its_own_settings_to_the_slicer(client, fleet, monkeypatch):
    ann, bob = two_users(client)
    only(fleet)
    seen = []
    real = slicer.run

    def spy(workdir, models, *args, **kw):
        seen.append((len(models) if isinstance(models, list) else 1, kw.get("object_settings")))
        return real(workdir, models, *args, **kw)

    monkeypatch.setattr(slicer, "run", spy)
    a = draft(ann)
    b = draft(bob, preset="0.20mm Strength")
    seen.clear()
    submit(ann, a, allow_batching=True)
    submit(bob, b, allow_batching=True)
    lead = next(j for j in store.jobs.all() if j.get("members"))
    wait_phase(client, lead["id"], {"ready", "failed"})
    member = store.jobs.get(lead["members"][0])
    sliced = [s for s in seen if s[0] == 2]
    assert sliced and sliced[0][1][0] == {} and sliced[0][1][1] == member["plate_overrides"]
    assert lead["spec"]["preset"] != member["spec"]["preset"] and "wall_loops" in member["plate_overrides"]


def test_plate_limits_and_pickup_times_are_respected(client, fleet):
    me = client.get("/api/me").json()
    printer = store.printers.get(fleet["p1s"])
    ids = [pooled_job(me, f"p{i}", 100 + i) for i in range(5)]
    for i in ids:
        store.jobs.update(i, allow_batching=True, preview={"time_s": 600})
    pool = [j for j in store.jobs.all() if j["phase"] == "pooled"]
    lead = store.jobs.get(ids[0])
    from app import pipelinecfg
    pipelinecfg.put({"plate_max_parts": 3})
    try:
        assert len(plates.find_partners(lead, printer, pool, time.time())) == 2             # lead + 2 = 3 parts
    finally:
        pipelinecfg.put({"plate_max_parts": 6})
    store.jobs.update(ids[1], pickup_by=time.time() + 600)                                  # cannot wait for a longer print
    pool = [j for j in store.jobs.all() if j["phase"] == "pooled"]
    got = {j["id"] for j in plates.find_partners(lead, printer, pool, time.time())}
    assert ids[1] not in got


def test_a_job_waits_for_a_partner_until_it_runs_out_of_slack_or_time(client, fleet):
    me = client.get("/api/me").json()
    jid = pooled_job(me, "waiter", time.time())
    store.jobs.update(jid, allow_batching=True, preview={"time_s": 600})
    now = time.time()
    assert "another part" in plates.hold_reason(store.jobs.get(jid), now)                   # whenever: wait
    assert plates.hold_reason(store.jobs.get(jid), now + 25 * 3600) is None                 # past the 24 h maximum hold
    store.jobs.update(jid, pickup_by=now + 4 * 3600)
    assert plates.hold_reason(store.jobs.get(jid), now)                                     # plenty of time to spare
    store.jobs.update(jid, pickup_by=now + 1800)
    assert plates.hold_reason(store.jobs.get(jid), now) is None                             # no slack left: go alone
    store.jobs.update(jid, allow_batching=False)
    assert plates.hold_reason(store.jobs.get(jid), now) is None


# ----------------------------------------------------------------------------- the whole path

def test_two_users_share_a_plate_from_pool_to_result(client, fleet):
    ann, bob = two_users(client)
    printer = only(fleet)
    a, b = draft(ann), draft(bob)
    submit(ann, a, allow_batching=True)
    assert allocator.tick()["assigned"] == 0                         # alone and opted in: waits for a partner
    assert "share the plate" in ann.get(f"/api/jobs/{a['id']}").json()["waiting_for"]
    submit(bob, b, allow_batching=True)
    ja, jb = ann.get(f"/api/jobs/{a['id']}").json(), bob.get(f"/api/jobs/{b['id']}").json()
    lead, member = (ja, jb) if ja.get("members") else (jb, ja)
    assert lead["members"] == [member["id"]] and member["batch_lead"] == lead["id"]
    assert lead["printer_id"] == printer and member["printer_id"] == printer
    assert abs(lead["plate_share"] + member["plate_share"] - 1) < 0.01

    done = wait_phase(client, lead["id"], {"ready", "failed"})
    assert done["phase"] == "ready"
    mirrored = wait_phase(client, member["id"], {"ready"})
    assert mirrored["gcode"] == done["gcode"] and mirrored["state"] == "done"
    owner_of_member = ann if member["id"] == a["id"] else bob
    assert owner_of_member.get(f"/api/jobs/{member['id']}/gcode").status_code == 200       # files come from the lead

    # either owner may start the plate; both jobs print
    assert owner_of_member.post(f"/api/jobs/{member['id']}/print").status_code in (200, 409, 502)   # no live printer in tests
    jobstate.transition(lead["id"], "printing", "t", force=True, queued=True, printed=True,
                        remote_name="plate-abc123.gcode.3mf", started_at=time.time(), result=None)
    assert store.jobs.get(member["id"])["phase"] == "printing"
    assert store.jobs.get(member["id"])["remote_name"] == "plate-abc123.gcode.3mf"

    # the printer's report ends both, with one printer-side result for the plate
    outcomes.on_status(printer, {"state": "FINISH", "subtask_name": "plate-abc123"})
    for jid in (lead["id"], member["id"]):
        j = store.jobs.get(jid)
        assert j["phase"] == "printed" and j["result"]["outcome"] == "finished"
    summary = stats.summary()
    assert summary["by_printer"][printer]["jobs"] == 1                                       # one plate, one print
    assert abs(sum(u["filament_g"] for u in summary["by_user"].values() if u["finished"]) -
               store.jobs.get(lead["id"])["stats"]["filament_g"]) < 0.5                      # filament split between owners

    # any one owner clears the whole plate; the other is told
    bedstate.set_state(printer, "occupied", "printer", finished=True, finished_at=time.time())
    from app import mailer
    before = len(mailer.outbox)
    assert owner_of_member.post(f"/api/printers/{printer}/bed/clear", json=CLEAR).status_code == 200
    time.sleep(0.2)
    assert len(mailer.outbox) > before


def test_a_job_that_is_not_opted_in_never_shares(client, fleet):
    ann, bob = two_users(client)
    only(fleet)
    a, b = draft(ann), draft(bob)
    submit(ann, a, allow_batching=True)
    submit(bob, b)                                                   # not opted in
    assert not any(j.get("members") for j in store.jobs.all())
    assert store.jobs.get(b["id"])["phase"] in ("assigned", "slicing", "ready")
    assert store.jobs.get(a["id"])["phase"] == "pooled"              # the other job took the only printer


def test_cancelling_one_job_returns_the_others_to_the_pool(client, fleet):
    ann, bob = two_users(client)
    only(fleet)
    a, b = draft(ann), draft(bob)
    submit(ann, a, allow_batching=True)
    submit(bob, b, allow_batching=True)
    lead = next(j for j in store.jobs.all() if j.get("members"))
    wait_phase(client, lead["id"], {"ready"})
    member_id = lead["members"][0]
    owner = ann if store.jobs.get(member_id)["owner_email"].startswith("ann") else bob
    assert owner.post(f"/api/jobs/{member_id}/cancel").json()["phase"] == "cancelled"
    other = store.jobs.get(lead["id"])
    assert other["phase"] in ("pooled", "assigned", "slicing", "ready") and not other.get("members")
    assert store.jobs.get(member_id)["phase"] == "cancelled" and not store.jobs.get(member_id).get("batch_lead")


def test_a_plate_the_slicer_cannot_fit_drops_its_last_job_and_slices_again(client, fleet, monkeypatch):
    ann, bob = two_users(client)
    only(fleet)
    real = slicer.run
    calls = []

    def fussy(workdir, models, *args, **kw):
        calls.append(len(models) if isinstance(models, list) else 1)
        if isinstance(models, list) and len(models) > 1:
            raise slicer.SliceError("One of the plate is empty or has no object fully inside it.")
        return real(workdir, models, *args, **kw)

    monkeypatch.setattr(slicer, "run", fussy)
    a, b = draft(ann), draft(bob)
    calls.clear()                                                    # the previews also went through the slicer
    submit(ann, a, allow_batching=True)
    submit(bob, b, allow_batching=True)
    lead = next(j for j in store.jobs.all() if j.get("members"))
    last = lead["members"][-1]
    done = wait_phase(client, lead["id"], {"ready", "failed"})
    assert done["phase"] == "ready" and not done.get("members") and calls[:2] == [2, 1]
    dropped = store.jobs.get(last)
    assert dropped["phase"] in ("pooled", "assigned", "slicing") and not dropped.get("batch_lead")
    assert lead["id"] in dropped["no_batch_with"]                                            # never paired with them again


def test_expired_hold_dissolves_a_plate(client, fleet):
    ann, bob = two_users(client)
    only(fleet)
    a, b = draft(ann), draft(bob)
    submit(ann, a, allow_batching=True)
    submit(bob, b, allow_batching=True)
    lead = next(j for j in store.jobs.all() if j.get("members"))
    wait_phase(client, lead["id"], {"ready"})
    wait_phase(client, lead["members"][0], {"ready"})
    store.jobs.update(lead["id"], phase_at=time.time() - 3 * 3600)
    assert allocator.expire_holds(time.time()) == 1
    for jid in (lead["id"], lead["members"][0]):
        assert store.jobs.get(jid)["phase"] == "pooled" and not store.jobs.get(jid).get("batch_lead")
    assert not store.jobs.get(lead["id"]).get("members")


def test_a_plate_always_starts_by_itself(client, fleet):
    me = client.get("/api/me").json()
    a, b = pooled_job(me, "a", 1), pooled_job(me, "b", 2)
    store.jobs.update(a, auto_start=True, members=[b])
    store.jobs.update(b, auto_start=False, batch_lead=a)
    assert plates.auto_start(store.jobs.get(a)) is True            # owner decision: pool plates always start by themselves


def test_slices_and_previews_are_drafts_that_leave_no_job_until_sent_somewhere(client, fleet):
    only(fleet)
    mid = upload(client, VALID / "cube_binary.stl").json()["id"]
    body = {"model_id": mid, "filament": "PLA", "preset": "0.20mm Standard", "settings": {}}
    before = {j["id"] for j in client.get("/api/jobs").json()}
    first = client.post("/api/slice", json=body).json()
    wait_job(client, first["id"])
    second = client.post("/api/slice", json=body).json()                 # every slice is only a draft
    wait_job(client, second["id"])
    assert store.jobs.get(first["id"])["draft"] and store.jobs.get(second["id"])["draft"]
    assert {j["id"] for j in client.get("/api/jobs").json()} == before   # drafts are not listed
    assert client.get(f"/api/jobs/{second['id']}").json()["draft"] is True      # but the slice can still be fetched and viewed
    assert all(j["id"] != second["id"] for j in stats_jobs())

    d = client.post("/api/jobs/preview", json={"model_id": mid, "filament": "PLA"}).json()
    wait_phase(client, d["id"], {"previewed"})
    assert client.delete(f"/api/jobs/{d['id']}").status_code == 200       # closing the dialog leaves nothing behind
    assert store.jobs.get(d["id"]) is None and client.get(f"/api/jobs/{d['id']}").status_code == 404

    d = client.post("/api/jobs/preview", json={"model_id": mid, "filament": "PLA"}).json()
    wait_phase(client, d["id"], {"previewed"})
    submit(client, d)                                                    # now it is a job, and the slices of the same part are gone
    assert store.jobs.get(second["id"]) is None and store.jobs.get(first["id"]) is None
    assert not [e for e in store.events.all() if e["job_id"] == first["id"]]
    listed = {j["id"] for j in client.get("/api/jobs").json()} - before
    assert listed == {d["id"]} and not store.jobs.get(d["id"])["draft"]
    assert client.delete(f"/api/jobs/{d['id']}").status_code == 409      # a real job is cancelled, not deleted


def test_a_slice_becomes_a_job_only_when_it_is_sent_to_a_printer(client, fleet, monkeypatch):
    from app import printing
    pid = only(fleet)
    mid = upload(client, VALID / "cube_binary.stl").json()["id"]
    s = client.post("/api/slice", json={"model_id": mid, "filament": "PLA", "preset": "0.20mm Standard", "settings": {}}).json()
    wait_job(client, s["id"])
    from app.storage import JOBS, files
    files.write_bytes(JOBS, s["id"], "model.gcode.3mf", b"PK")           # the mock slicer makes no print bundle
    store.jobs.update(s["id"], bundle="model.gcode.3mf")
    calls = []
    monkeypatch.setattr(printing, "start", lambda job, bundle, user, **kw: calls.append(job["id"]) or {"ok": True, "started": True, "file": "x"})
    assert client.post(f"/api/jobs/{s['id']}/print").status_code == 200
    assert calls == [s["id"]] and store.jobs.get(s["id"])["draft"] is False
    assert s["id"] in {j["id"] for j in client.get("/api/jobs").json()}


def test_old_drafts_are_cleaned_up(client, fleet):
    from app import retention
    me = client.get("/api/me").json()
    jid = pooled_job(me, "abandoned", time.time())
    store.jobs.update(jid, draft=True, phase="previewed", created=time.time() - 7 * 3600)
    fresh = pooled_job(me, "fresh", time.time())
    store.jobs.update(fresh, draft=True, phase="previewed")
    assert retention.run(dry_run=True)["drafts_cleaned"] == 1 and store.jobs.get(jid)
    assert retention.run()["drafts_cleaned"] == 1
    assert store.jobs.get(jid) is None and store.jobs.get(fresh)


def stats_jobs():
    return [j for j in store.jobs.all() if not j.get("draft")]
