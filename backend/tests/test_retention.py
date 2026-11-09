"""Retention: expired files are deleted, records are kept, running work is safe, the disk guard works."""
import time
import uuid

import pytest

from app import main as app_main, retention, store
from app.storage import JOBS, MODELS, files
from conftest import VALID, make_user, upload, wait_job

DAY = 86400


def old_job(owner, age_days, state="done", printed=False, nbytes=5000):
    jid = uuid.uuid4().hex[:12]
    files.create(JOBS, jid)
    files.write_bytes(JOBS, jid, "model.gcode", b"G1 X1\n" * (nbytes // 6))
    files.write_bytes(JOBS, jid, "model.gcode.3mf", b"PK" + b"0" * 100)
    store.jobs.put(jid, {"id": jid, "name": "old.stl", "state": state, "created": time.time() - age_days * DAY, "owner_id": owner["id"],
                         "owner_email": owner["email"], "printer_id": "x", "printer_name": "p", "printer_model": "P1S", "filament": "PLA",
                         "preset": "p", "gcode": "model.gcode", "bundle": "model.gcode.3mf", "queued": False, "printed": printed})
    return jid


def old_model(owner, unused_days):
    mid = uuid.uuid4().hex[:12]
    files.create(MODELS, mid)
    files.write_bytes(MODELS, mid, "source.stl", b"x" * 2000)
    files.write_bytes(MODELS, mid, "preview.stl", b"y" * 1000)
    files.write_text(MODELS, mid, "name.txt", "old.stl")
    t = time.time() - unused_days * DAY
    store.models.put(mid, {"id": mid, "owner_id": owner["id"], "name": "old.stl", "created": t, "last_used": t})
    return mid


@pytest.fixture()
def me(client):
    return client.get("/api/me").json()


@pytest.fixture(autouse=True)
def default_settings():
    store.settings.delete("retention")
    store.settings.delete("last_cleanup")
    yield
    store.settings.delete("retention")


# --------------------------------------------------------------------- the rules

def test_defaults():
    assert retention.get_settings() == {"job_files_days": 30, "failed_job_days": 7, "model_files_days": 30}


def test_expired_job_files_are_deleted_and_the_record_is_kept(me):
    jid = old_job(me, 45)
    report = retention.run()
    assert report["jobs_cleaned"] >= 1 and report["bytes_freed"] > 0
    assert not files.exists(JOBS, jid)
    job = store.jobs.get(jid)
    assert job and job["files_deleted_at"] and job["name"] == "old.stl"          # history survives for statistics


def test_recent_jobs_are_left_alone(me):
    jid = old_job(me, 5)
    retention.run()
    assert files.has(JOBS, jid, "model.gcode") and not store.jobs.get(jid).get("files_deleted_at")


def test_failed_jobs_expire_sooner(me):
    failed, ok = old_job(me, 10, state="failed"), old_job(me, 10)
    retention.run()
    assert not files.exists(JOBS, failed) and files.exists(JOBS, ok)


def test_a_job_that_is_slicing_is_never_touched(me):
    jid = old_job(me, 400, state="slicing")
    retention.run()
    assert files.exists(JOBS, jid)


def test_recently_printed_job_keeps_its_files_even_if_created_long_ago(me):
    jid = old_job(me, 60, printed=True)
    store.jobs.update(jid, started_at=time.time() - 2 * DAY)
    retention.run()
    assert files.exists(JOBS, jid)


def test_unused_models_expire_and_used_ones_stay(me):
    stale, fresh = old_model(me, 45), old_model(me, 3)
    retention.run()
    assert not files.exists(MODELS, stale) and store.models.get(stale)["files_deleted_at"]
    assert files.exists(MODELS, fresh)


def test_zero_means_keep_forever(me):
    retention.set_settings({"job_files_days": 0, "failed_job_days": 0, "model_files_days": 0})
    jid, mid = old_job(me, 900), old_model(me, 900)
    retention.run()
    assert files.exists(JOBS, jid) and files.exists(MODELS, mid)


def test_dry_run_deletes_nothing_but_reports(me):
    jid = old_job(me, 45)
    report = retention.run(dry_run=True)
    assert report["dry_run"] and report["jobs_cleaned"] >= 1 and report["bytes_freed"] > 0
    assert files.exists(JOBS, jid) and not store.jobs.get(jid).get("files_deleted_at")


def test_cleanup_is_idempotent(me):
    old_job(me, 45)
    first = retention.run()
    second = retention.run()
    assert first["jobs_cleaned"] >= 1 and second["jobs_cleaned"] == 0


def test_model_use_postpones_expiry(me, client, fleet):
    m = upload(client, VALID / "cube_binary.stl").json()
    store.models.update(m["id"], created=time.time() - 40 * DAY, last_used=time.time() - 40 * DAY)
    client.post("/api/assign", json={"model_id": m["id"], "filament": "PLA"})        # using it records the use
    retention.run()
    assert files.exists(MODELS, m["id"])


# ------------------------------------------------------------- what users see

def test_expired_job_files_give_a_clear_410(client, me):
    jid = old_job(me, 45)
    retention.run()
    for path in (f"/api/jobs/{jid}/gcode", f"/api/jobs/{jid}/bundle"):
        r = client.get(path)
        assert r.status_code == 410 and "Slice the part again" in r.json()["detail"]
    assert client.post(f"/api/jobs/{jid}/print").status_code == 410
    assert client.get(f"/api/jobs/{jid}").json()["files_deleted_at"]                 # the job itself is still listed


def test_expired_model_gives_410_to_its_owner_and_404_to_others(client, me):
    mid = old_model(me, 45)
    retention.run()
    assert client.get(f"/api/models/{mid}/preview.stl").status_code == 410
    other = make_user(client, "retention.other@imperial.ac.uk")
    assert other.get(f"/api/models/{mid}/preview.stl").status_code == 404           # no information leak


# --------------------------------------------------------------------- admin API

def test_usage_endpoint(client, me):
    old_job(me, 1, nbytes=60000)
    u = client.get("/api/admin/storage").json()
    assert u["disk"]["total"] > u["disk"]["free"] > 0 and u["data"]["jobs"] >= 60000 and u["data"]["database"] > 0
    assert u["settings"]["job_files_days"] == 30 and u["counts"]["jobs"] >= 1


def test_settings_round_trip_and_validation(client):
    r = client.put("/api/admin/storage/settings", json={"job_files_days": 14, "model_files_days": 60})
    assert r.status_code == 200 and r.json() == {"job_files_days": 14, "failed_job_days": 7, "model_files_days": 60}
    assert client.get("/api/admin/storage").json()["settings"]["job_files_days"] == 14
    assert client.put("/api/admin/storage/settings", json={"job_files_days": -1}).status_code == 422
    assert client.put("/api/admin/storage/settings", json={"job_files_days": 99999}).status_code == 422
    assert any(a["action"] == "storage_settings" for a in client.get("/api/admin/audit?limit=20").json())


def test_manual_cleanup_and_dry_run(client, me):
    jid = old_job(me, 45)
    dry = client.post("/api/admin/storage/cleanup?dry_run=true").json()
    assert dry["dry_run"] and files.exists(JOBS, jid)
    done = client.post("/api/admin/storage/cleanup").json()
    assert not done["dry_run"] and done["jobs_cleaned"] >= 1 and not files.exists(JOBS, jid)
    assert client.get("/api/admin/storage").json()["last_cleanup"]["jobs_cleaned"] >= 1


def test_storage_endpoints_are_admin_only(client):
    user = make_user(client, "retention.user@imperial.ac.uk")
    assert user.get("/api/admin/storage").status_code == 403
    assert user.put("/api/admin/storage/settings", json={"job_files_days": 1}).status_code == 403
    assert user.post("/api/admin/storage/cleanup").status_code == 403


# -------------------------------------------------------------------- disk guard

def test_upload_is_refused_when_the_disk_is_nearly_full(client, monkeypatch):
    monkeypatch.setattr(files, "total_free", lambda: (100 * 1024**3, 10))
    monkeypatch.setattr(files, "free_bytes", lambda: 10)
    monkeypatch.setattr(retention, "MIN_FREE_MB", 500)
    r = upload(client, VALID / "cube_binary.stl")
    assert r.status_code == 507 and "out of disk space" in r.json()["detail"]
    assert client.get("/api/admin/storage").json()["disk"]["low"] is True


def test_low_disk_is_flagged_by_percentage(client, monkeypatch):
    monkeypatch.setattr(files, "total_free", lambda: (1000 * 1024**3, 50 * 1024**3))
    assert client.get("/api/admin/storage").json()["disk"]["low"] is True            # 5% free


def test_background_thread_can_be_disabled(monkeypatch):
    monkeypatch.setattr(retention, "INTERVAL_HOURS", 0)
    import threading
    before = threading.active_count()
    retention.start_background()
    assert threading.active_count() == before
