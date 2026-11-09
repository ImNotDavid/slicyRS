"""Task queue (A.5). A worker claims a task with a lease and keeps extending it while it works. If the worker dies, the
lease runs out and the task is handed to another worker; after `max_attempts` it is marked dead. The queue itself lives in
the database (storage layer), so it works across processes."""
import time
from typing import Any

from .storage import db

LEASE_S = 90


def enqueue(kind: str, payload: dict[str, Any], max_attempts: int = 3) -> int:
    return db.tasks.enqueue(kind, payload, max_attempts)


def reap_dead(now: float | None = None) -> list[dict[str, Any]]:
    """Tasks whose lease ran out on their last allowed attempt are dead; return them so the caller can fail the work."""
    return db.tasks.reap_dead(now or time.time())


def claim(worker: str, now: float | None = None) -> dict[str, Any] | None:
    return db.tasks.claim(worker, LEASE_S, now or time.time())


def heartbeat(task_id: int, worker: str) -> bool:
    return db.tasks.heartbeat(task_id, worker, LEASE_S, time.time())


def finish(task_id: int, worker: str, error: str | None = None) -> None:
    db.tasks.finish(task_id, worker, error, time.time())


def counts() -> dict[str, int]:
    return db.tasks.counts()


def clear() -> None:
    db.tasks.clear()
