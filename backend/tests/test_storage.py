"""Milestone A.9: the storage boundary. Every backend must pass the same contract, and nothing outside app/storage may know
how records or files are kept."""
import re
import time
from pathlib import Path

import pytest

from app.storage import Database, FileStore
from app.storage.local_files import LocalFiles
from app.storage.memory_db import MemoryDatabase
from app.storage.sqlite_db import SqliteDatabase

APP = Path(__file__).resolve().parent.parent / "app"


@pytest.fixture(params=["sqlite", "memory"])
def database(request, tmp_path):
    return SqliteDatabase(tmp_path / "t.db") if request.param == "sqlite" else MemoryDatabase()


@pytest.fixture()
def fs(tmp_path):
    return LocalFiles(tmp_path / "files")


# ------------------------------------------------------------------ database contract

def test_backends_satisfy_the_interfaces(database, fs):
    assert isinstance(database, Database) and isinstance(fs, FileStore)


def test_collections_keep_documents_in_insertion_order_and_hand_out_copies(database):
    c = database.collection("things")
    c.put("b", {"id": "b", "n": 1})
    c.put("a", {"id": "a", "n": 2})
    assert [d["id"] for d in c.all()] == ["b", "a"]
    c.put("b", {"id": "b", "n": 3})                                    # replacing keeps the place
    assert [d["id"] for d in c.all()] == ["b", "a"] and c.get("b")["n"] == 3
    got = c.get("a")
    got["n"] = 99
    assert c.get("a")["n"] == 2                                       # callers cannot change stored data by accident
    assert c.get("missing") is None and database.collection("other").all() == []


def test_update_merges_fields_and_delete_reports_whether_it_removed(database):
    c = database.collection("things")
    c.put("x", {"id": "x", "a": 1, "b": 2})
    assert c.update("x", b=5, c=6) == {"id": "x", "a": 1, "b": 5, "c": 6}
    assert c.update("nope", a=1) is None
    assert c.delete("x") is True and c.delete("x") is False


def test_rate_limit_window(database):
    assert all(database.rate_hit("k", 3, 60) for _ in range(3)) and not database.rate_hit("k", 3, 60)
    assert database.rate_hit("other", 1, 60)
    database.rate_reset()
    assert database.rate_hit("k", 3, 60)
    assert database.rate_hit("w", 1, 1) and not database.rate_hit("w", 1, 1)
    time.sleep(1.1)
    assert database.rate_hit("w", 1, 1)


def test_task_queue_leases_retries_and_dead_tasks(database):
    t = database.tasks
    first, second = t.enqueue("slice", {"job_id": "1"}), t.enqueue("slice", {"job_id": "2"}, max_attempts=2)
    now = time.time()
    a = t.claim("w1", 10, now)
    assert a["id"] == first and a["payload"] == {"job_id": "1"} and a["attempts"] == 1 and a["status"] == "running"
    assert t.claim("w2", 10, now)["id"] == second                      # each task goes to one worker
    assert t.claim("w3", 10, now) is None
    assert t.heartbeat(first, "w1", 10, now + 5) and not t.heartbeat(first, "w2", 10, now + 5)   # w1's lease now runs to now+15
    lost = t.claim("w3", 10, now + 12)                                 # w2 went quiet: its task is handed on (attempt 2 of 2)
    assert lost["id"] == second and lost["attempts"] == 2 and lost["worker"] == "w3"
    assert t.claim("w4", 10, now + 13) is None
    again = t.claim("w4", 10, now + 16)                                # w1 went quiet too
    assert again["id"] == first and again["attempts"] == 2 and again["worker"] == "w4"
    t.finish(first, "w4", None, now + 17)
    assert t.reap_dead(now + 20) == []                                 # second's lease (to now+22) is still running
    dead = t.reap_dead(now + 40)                                       # out of attempts and out of time
    assert [d["id"] for d in dead] == [second] and dead[0]["status"] == "dead"
    assert t.counts() == {"done": 1, "dead": 1}
    t.finish(second, "someone-else", "boom", now)                      # a stranger cannot close it
    t.clear()
    assert t.counts() == {}


# ------------------------------------------------------------------ file contract

def test_files_are_kept_per_area(fs):
    assert not fs.exists("jobs", "j1")
    fs.create("jobs", "j1")
    with pytest.raises(FileExistsError):
        fs.create("jobs", "j1")
    fs.write_bytes("jobs", "j1", "a.bin", b"12345")
    fs.write_text("jobs", "j1", "n.txt", "héllo")
    assert fs.exists("jobs", "j1") and fs.has("jobs", "j1", "a.bin") and not fs.has("jobs", "j1", "zzz")
    assert fs.read_bytes("jobs", "j1", "a.bin") == b"12345" and fs.read_text("jobs", "j1", "n.txt") == "héllo"
    assert fs.names("jobs", "j1") == ["a.bin", "n.txt"] and fs.names("jobs", "none") == []
    assert fs.path("jobs", "j1", "a.bin").read_bytes() == b"12345"      # a real path, for tools like the slicer
    assert not fs.exists("models", "j1")                                # namespaces are separate


def test_workdir_scratch_removal_sizes_and_free_space(fs):
    wd = fs.workdir("jobs", "j2")
    (wd / "preview").mkdir()
    (wd / "preview" / "x").write_bytes(b"x" * 100)
    fs.write_bytes("jobs", "j2", "keep", b"y" * 50)
    fs.publish("jobs", "j2")
    assert fs.size("jobs", "j2") == 150 and fs.total_bytes("jobs") >= 150 and fs.total_bytes("models") == 0
    fs.remove_subdir("jobs", "j2", "preview")
    assert fs.size("jobs", "j2") == 50
    assert fs.remove("jobs", "j2") == 50 and not fs.exists("jobs", "j2") and fs.remove("jobs", "j2") == 0
    total, free = fs.total_free()
    assert total > 0 and 0 < free <= total and fs.free_bytes() > 0


# ------------------------------------------------------------------ nothing leaks past the boundary

def sources():
    for p in APP.rglob("*.py"):
        if "storage" not in p.relative_to(APP).parts:
            yield p, p.read_text(encoding="utf-8")


def test_only_the_storage_layer_touches_sqlite_or_data_paths():
    bad = []
    for p, text in sources():
        for pattern in (r"\bsqlite3\b", r"\b(JOBS_DIR|MODELS_DIR)\b", r"store\._conn", r"store\._lock", r"shutil\.rmtree", r"shutil\.disk_usage"):
            if re.search(pattern, text):
                bad.append(f"{p.relative_to(APP)}: {pattern}")
    assert not bad, bad


def test_the_data_directory_is_used_only_for_the_secret_key():
    users = [str(p.relative_to(APP)) for p, text in sources() if re.search(r"\bDATA_DIR\b", text)]
    assert sorted(users) == ["config.py"], users
