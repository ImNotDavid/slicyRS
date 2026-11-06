"""Thread-safe JSON document store on SQLite (one table, documents keyed by collection + id)."""
import json
import sqlite3
import threading
import time
from typing import Any

from .config import DATA_DIR

_DB = DATA_DIR / "slicyrs.db"
_lock = threading.RLock()
_conn = sqlite3.connect(_DB, check_same_thread=False, isolation_level=None)
_conn.execute("PRAGMA journal_mode=WAL")
_conn.execute("PRAGMA busy_timeout=5000")
_conn.execute("CREATE TABLE IF NOT EXISTS docs (collection TEXT NOT NULL, key TEXT NOT NULL, body TEXT NOT NULL, "
              "PRIMARY KEY (collection, key))")
_conn.execute("CREATE TABLE IF NOT EXISTS ratelimit (key TEXT NOT NULL, ts REAL NOT NULL)")
_conn.execute("CREATE INDEX IF NOT EXISTS ratelimit_key ON ratelimit (key, ts)")
_rate_calls = 0


def rate_hit(key: str, limit: int, window_s: int) -> bool:
    """Sliding-window limiter kept in the database, so limits survive restarts. True if the hit is allowed (and counted)."""
    global _rate_calls
    now = time.time()
    with _lock:
        _conn.execute("DELETE FROM ratelimit WHERE key=? AND ts<?", (key, now - window_s))
        if _conn.execute("SELECT COUNT(*) FROM ratelimit WHERE key=?", (key,)).fetchone()[0] >= limit:
            return False
        _conn.execute("INSERT INTO ratelimit(key, ts) VALUES(?, ?)", (key, now))
        _rate_calls += 1
        if _rate_calls % 200 == 0:  # housekeeping: nothing is ever needed beyond a day
            _conn.execute("DELETE FROM ratelimit WHERE ts<?", (now - 86400,))
    return True


def rate_reset() -> None:
    with _lock:
        _conn.execute("DELETE FROM ratelimit")


class Collection:
    def __init__(self, name: str):
        self.name = name

    def all(self) -> list[dict[str, Any]]:
        with _lock:
            rows = _conn.execute("SELECT body FROM docs WHERE collection=? ORDER BY rowid", (self.name,)).fetchall()
        return [json.loads(r[0]) for r in rows]

    def get(self, key: str) -> dict[str, Any] | None:
        with _lock:
            row = _conn.execute("SELECT body FROM docs WHERE collection=? AND key=?", (self.name, key)).fetchone()
        return json.loads(row[0]) if row else None

    def put(self, key: str, value: dict[str, Any]) -> dict[str, Any]:
        with _lock:
            _conn.execute("INSERT INTO docs(collection,key,body) VALUES(?,?,?) "
                          "ON CONFLICT(collection,key) DO UPDATE SET body=excluded.body",
                          (self.name, key, json.dumps(value)))
        return dict(value)

    def update(self, key: str, **fields: Any) -> dict[str, Any] | None:
        with _lock:  # read-modify-write under one lock
            cur = self.get(key)
            if cur is None:
                return None
            cur.update(fields)
            return self.put(key, cur)

    def delete(self, key: str) -> bool:
        with _lock:
            n = _conn.execute("DELETE FROM docs WHERE collection=? AND key=?", (self.name, key)).rowcount
        return n > 0


printers = Collection("printers")
jobs = Collection("jobs")
users = Collection("users")
models = Collection("models")
audit = Collection("audit")
codes = Collection("login_codes")
settings = Collection("settings")
sessions = Collection("sessions")
events = Collection("job_events")
