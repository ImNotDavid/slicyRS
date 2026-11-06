"""Thread-safe JSON document store on SQLite (one table, documents keyed by collection + id)."""
import json
import sqlite3
import threading
from typing import Any

from .config import DATA_DIR

_DB = DATA_DIR / "slicyrs.db"
_lock = threading.RLock()
_conn = sqlite3.connect(_DB, check_same_thread=False, isolation_level=None)
_conn.execute("PRAGMA journal_mode=WAL")
_conn.execute("PRAGMA busy_timeout=5000")
_conn.execute("CREATE TABLE IF NOT EXISTS docs (collection TEXT NOT NULL, key TEXT NOT NULL, body TEXT NOT NULL, "
              "PRIMARY KEY (collection, key))")


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
