"""SQLite implementation of the Database interface: one file, safe across processes (WAL), tasks claimed atomically."""
import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

COLS = "id,kind,payload,status,attempts,max_attempts,lease_until,worker,created,finished,error"


def _task(r) -> dict[str, Any]:
    return {"id": r[0], "kind": r[1], "payload": json.loads(r[2]), "status": r[3], "attempts": r[4], "max_attempts": r[5],
            "lease_until": r[6], "worker": r[7], "created": r[8], "finished": r[9], "error": r[10]}


class SqliteCollection:
    def __init__(self, db: "SqliteDatabase", name: str):
        self.db, self.name = db, name

    def all(self) -> list[dict[str, Any]]:
        with self.db.lock:
            rows = self.db.conn.execute("SELECT body FROM docs WHERE collection=? ORDER BY rowid", (self.name,)).fetchall()
        return [json.loads(r[0]) for r in rows]

    def get(self, key: str) -> dict[str, Any] | None:
        with self.db.lock:
            row = self.db.conn.execute("SELECT body FROM docs WHERE collection=? AND key=?", (self.name, key)).fetchone()
        return json.loads(row[0]) if row else None

    def put(self, key: str, value: dict[str, Any]) -> dict[str, Any]:
        with self.db.lock:
            self.db.conn.execute("INSERT INTO docs(collection,key,body) VALUES(?,?,?) "
                                 "ON CONFLICT(collection,key) DO UPDATE SET body=excluded.body", (self.name, key, json.dumps(value)))
        return dict(value)

    def update(self, key: str, **fields: Any) -> dict[str, Any] | None:
        with self.db.lock:  # read-modify-write under one lock
            cur = self.get(key)
            if cur is None:
                return None
            cur.update(fields)
            return self.put(key, cur)

    def delete(self, key: str) -> bool:
        with self.db.lock:
            return self.db.conn.execute("DELETE FROM docs WHERE collection=? AND key=?", (self.name, key)).rowcount > 0


class SqliteTasks:
    def __init__(self, db: "SqliteDatabase"):
        self.db = db
        with db.lock:
            db.conn.execute(
                "CREATE TABLE IF NOT EXISTS tasks (id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, payload TEXT NOT NULL, "
                "status TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0, max_attempts INTEGER NOT NULL DEFAULT 3, "
                "lease_until REAL, worker TEXT, created REAL NOT NULL, finished REAL, error TEXT)")
            db.conn.execute("CREATE INDEX IF NOT EXISTS tasks_status ON tasks (status, id)")

    def enqueue(self, kind: str, payload: dict[str, Any], max_attempts: int = 3) -> int:
        with self.db.lock:
            return self.db.conn.execute("INSERT INTO tasks(kind,payload,status,max_attempts,created) VALUES(?,?,?,?,?)",
                                        (kind, json.dumps(payload), "queued", max_attempts, time.time())).lastrowid

    def claim(self, worker: str, lease_s: float, now: float) -> dict[str, Any] | None:
        with self.db.lock:
            r = self.db.conn.execute(
                f"UPDATE tasks SET status='running', worker=?, lease_until=?, attempts=attempts+1 "
                f"WHERE id=(SELECT id FROM tasks WHERE status='queued' OR (status='running' AND lease_until<? AND attempts<max_attempts) "
                f"ORDER BY id LIMIT 1) RETURNING {COLS}", (worker, now + lease_s, now)).fetchone()
        return _task(r) if r else None

    def heartbeat(self, task_id: int, worker: str, lease_s: float, now: float) -> bool:
        with self.db.lock:
            return self.db.conn.execute("UPDATE tasks SET lease_until=? WHERE id=? AND worker=? AND status='running'",
                                        (now + lease_s, task_id, worker)).rowcount > 0

    def finish(self, task_id: int, worker: str, error: str | None, now: float) -> None:
        with self.db.lock:
            self.db.conn.execute("UPDATE tasks SET status=?, finished=?, error=? WHERE id=? AND worker=?",
                                 ("failed" if error else "done", now, error, task_id, worker))

    def reap_dead(self, now: float) -> list[dict[str, Any]]:
        with self.db.lock:
            rows = self.db.conn.execute(
                f"UPDATE tasks SET status='dead', finished=?, error='worker stopped before finishing' "
                f"WHERE status='running' AND lease_until<? AND attempts>=max_attempts RETURNING {COLS}", (now, now)).fetchall()
        return [_task(r) for r in rows]

    def counts(self) -> dict[str, int]:
        with self.db.lock:
            return dict(self.db.conn.execute("SELECT status, COUNT(*) FROM tasks GROUP BY status").fetchall())

    def clear(self) -> None:
        with self.db.lock:
            self.db.conn.execute("DELETE FROM tasks")


class SqliteDatabase:
    def __init__(self, path: Path):
        self.path = path
        self.lock = threading.RLock()
        self.conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA busy_timeout=5000")
        self.conn.execute("CREATE TABLE IF NOT EXISTS docs (collection TEXT NOT NULL, key TEXT NOT NULL, body TEXT NOT NULL, "
                          "PRIMARY KEY (collection, key))")
        self.conn.execute("CREATE TABLE IF NOT EXISTS ratelimit (key TEXT NOT NULL, ts REAL NOT NULL)")
        self.conn.execute("CREATE INDEX IF NOT EXISTS ratelimit_key ON ratelimit (key, ts)")
        self._rate_calls = 0
        self.tasks = SqliteTasks(self)

    def collection(self, name: str) -> SqliteCollection:
        return SqliteCollection(self, name)

    def rate_hit(self, key: str, limit: int, window_s: int) -> bool:
        """Sliding window kept in the database, so limits survive restarts."""
        now = time.time()
        with self.lock:
            self.conn.execute("DELETE FROM ratelimit WHERE key=? AND ts<?", (key, now - window_s))
            if self.conn.execute("SELECT COUNT(*) FROM ratelimit WHERE key=?", (key,)).fetchone()[0] >= limit:
                return False
            self.conn.execute("INSERT INTO ratelimit(key, ts) VALUES(?, ?)", (key, now))
            self._rate_calls += 1
            if self._rate_calls % 200 == 0:  # housekeeping: nothing is ever needed beyond a day
                self.conn.execute("DELETE FROM ratelimit WHERE ts<?", (now - 86400,))
        return True

    def rate_reset(self) -> None:
        with self.lock:
            self.conn.execute("DELETE FROM ratelimit")

    def size_bytes(self) -> int:
        return sum(p.stat().st_size for p in self.path.parent.glob(self.path.name + "*") if p.is_file())
