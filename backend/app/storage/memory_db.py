"""In-memory Database: nothing survives a restart and it is private to one process. For tests and throwaway development
runs, and a worked example of the interface for anyone adding a real backend."""
import copy
import json
import threading
import time
from typing import Any


class MemoryCollection:
    def __init__(self, db: "MemoryDatabase", name: str):
        self.db, self.name = db, name
        db.docs.setdefault(name, {})

    @property
    def _d(self) -> dict[str, str]:
        return self.db.docs[self.name]

    def all(self) -> list[dict[str, Any]]:
        with self.db.lock:
            return [json.loads(v) for v in self._d.values()]

    def get(self, key: str) -> dict[str, Any] | None:
        with self.db.lock:
            v = self._d.get(key)
        return json.loads(v) if v is not None else None

    def put(self, key: str, value: dict[str, Any]) -> dict[str, Any]:
        with self.db.lock:
            self._d[key] = json.dumps(value)          # stored as JSON text, like every other backend: callers get copies
        return copy.deepcopy(value)

    def update(self, key: str, **fields: Any) -> dict[str, Any] | None:
        with self.db.lock:
            cur = self.get(key)
            if cur is None:
                return None
            cur.update(fields)
            return self.put(key, cur)

    def delete(self, key: str) -> bool:
        with self.db.lock:
            return self._d.pop(key, None) is not None


class MemoryTasks:
    def __init__(self, db: "MemoryDatabase"):
        self.db, self.rows, self.next_id = db, [], 1

    def enqueue(self, kind: str, payload: dict[str, Any], max_attempts: int = 3) -> int:
        with self.db.lock:
            row = {"id": self.next_id, "kind": kind, "payload": json.loads(json.dumps(payload)), "status": "queued", "attempts": 0,
                   "max_attempts": max_attempts, "lease_until": None, "worker": None, "created": time.time(), "finished": None, "error": None}
            self.next_id += 1
            self.rows.append(row)
            return row["id"]

    def claim(self, worker: str, lease_s: float, now: float) -> dict[str, Any] | None:
        with self.db.lock:
            for r in self.rows:
                if r["status"] == "queued" or (r["status"] == "running" and r["lease_until"] < now and r["attempts"] < r["max_attempts"]):
                    r.update(status="running", worker=worker, lease_until=now + lease_s, attempts=r["attempts"] + 1)
                    return copy.deepcopy(r)
        return None

    def heartbeat(self, task_id: int, worker: str, lease_s: float, now: float) -> bool:
        with self.db.lock:
            for r in self.rows:
                if r["id"] == task_id and r["worker"] == worker and r["status"] == "running":
                    r["lease_until"] = now + lease_s
                    return True
        return False

    def finish(self, task_id: int, worker: str, error: str | None, now: float) -> None:
        with self.db.lock:
            for r in self.rows:
                if r["id"] == task_id and r["worker"] == worker:
                    r.update(status="failed" if error else "done", finished=now, error=error)

    def reap_dead(self, now: float) -> list[dict[str, Any]]:
        out = []
        with self.db.lock:
            for r in self.rows:
                if r["status"] == "running" and r["lease_until"] < now and r["attempts"] >= r["max_attempts"]:
                    r.update(status="dead", finished=now, error="worker stopped before finishing")
                    out.append(copy.deepcopy(r))
        return out

    def counts(self) -> dict[str, int]:
        with self.db.lock:
            out: dict[str, int] = {}
            for r in self.rows:
                out[r["status"]] = out.get(r["status"], 0) + 1
            return out

    def clear(self) -> None:
        with self.db.lock:
            self.rows.clear()


class MemoryDatabase:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.docs: dict[str, dict[str, str]] = {}
        self.hits: dict[str, list[float]] = {}
        self.tasks = MemoryTasks(self)

    def collection(self, name: str) -> MemoryCollection:
        return MemoryCollection(self, name)

    def rate_hit(self, key: str, limit: int, window_s: int) -> bool:
        now = time.time()
        with self.lock:
            live = [t for t in self.hits.get(key, []) if t >= now - window_s]
            if len(live) >= limit:
                self.hits[key] = live
                return False
            self.hits[key] = live + [now]
            return True

    def rate_reset(self) -> None:
        with self.lock:
            self.hits.clear()

    def size_bytes(self) -> int:
        with self.lock:
            return sum(len(v) for c in self.docs.values() for v in c.values())
