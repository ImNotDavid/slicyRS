"""Database-backed task queue (A.5). A worker claims a task with a lease and keeps extending it while it works. If the
worker dies, the lease runs out and the task is handed to another worker; after `max_attempts` it is marked dead.
Works across processes because every claim is one atomic SQL statement."""
import json
import time
from typing import Any

from . import store

LEASE_S = 90
store._conn.execute(
    "CREATE TABLE IF NOT EXISTS tasks (id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, payload TEXT NOT NULL, "
    "status TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0, max_attempts INTEGER NOT NULL DEFAULT 3, "
    "lease_until REAL, worker TEXT, created REAL NOT NULL, finished REAL, error TEXT)")
store._conn.execute("CREATE INDEX IF NOT EXISTS tasks_status ON tasks (status, id)")

COLS = "id,kind,payload,status,attempts,max_attempts,lease_until,worker,created,finished,error"


def _row(r) -> dict[str, Any]:
    return {"id": r[0], "kind": r[1], "payload": json.loads(r[2]), "status": r[3], "attempts": r[4], "max_attempts": r[5],
            "lease_until": r[6], "worker": r[7], "created": r[8], "finished": r[9], "error": r[10]}


def enqueue(kind: str, payload: dict[str, Any], max_attempts: int = 3) -> int:
    with store._lock:
        cur = store._conn.execute("INSERT INTO tasks(kind,payload,status,max_attempts,created) VALUES(?,?,?,?,?)",
                                  (kind, json.dumps(payload), "queued", max_attempts, time.time()))
        return cur.lastrowid


def reap_dead(now: float | None = None) -> list[dict[str, Any]]:
    """Tasks whose lease ran out on their last allowed attempt are dead; return them so the caller can fail the work."""
    now = now or time.time()
    with store._lock:
        rows = store._conn.execute(
            f"UPDATE tasks SET status='dead', finished=?, error='worker stopped before finishing' "
            f"WHERE status='running' AND lease_until<? AND attempts>=max_attempts RETURNING {COLS}", (now, now)).fetchall()
    return [_row(r) for r in rows]


def claim(worker: str, now: float | None = None) -> dict[str, Any] | None:
    now = now or time.time()
    with store._lock:
        r = store._conn.execute(
            f"UPDATE tasks SET status='running', worker=?, lease_until=?, attempts=attempts+1 "
            f"WHERE id=(SELECT id FROM tasks WHERE status='queued' OR (status='running' AND lease_until<? AND attempts<max_attempts) "
            f"ORDER BY id LIMIT 1) RETURNING {COLS}", (worker, now + LEASE_S, now)).fetchone()
    return _row(r) if r else None


def heartbeat(task_id: int, worker: str) -> bool:
    with store._lock:
        n = store._conn.execute("UPDATE tasks SET lease_until=? WHERE id=? AND worker=? AND status='running'",
                                (time.time() + LEASE_S, task_id, worker)).rowcount
    return n > 0


def finish(task_id: int, worker: str, error: str | None = None) -> None:
    with store._lock:
        store._conn.execute("UPDATE tasks SET status=?, finished=?, error=? WHERE id=? AND worker=?",
                            ("failed" if error else "done", time.time(), error, task_id, worker))


def counts() -> dict[str, int]:
    with store._lock:
        return dict(store._conn.execute("SELECT status, COUNT(*) FROM tasks GROUP BY status").fetchall())
