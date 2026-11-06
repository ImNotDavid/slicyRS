"""Slicer worker (A.5). Claims tasks from the database queue and runs them.

The web process starts a few of these as threads by default. For a separate worker (more capacity, or a machine with more
memory), set EMBED_WORKERS=0 on the web process and run `python -m app.worker` against the same data directory.
A worker that dies mid-slice loses nothing: its lease runs out and another worker takes the task.
"""
import logging
import os
import threading
import time
import uuid

from . import pipeline, taskqueue

log = logging.getLogger("worker")


def run_once(worker_id: str) -> bool:
    """Claim and run one task. False when there was nothing to do."""
    for dead in taskqueue.reap_dead():
        pipeline.on_dead(dead)
    task = taskqueue.claim(worker_id)
    if task is None:
        return False
    stop = threading.Event()

    def beat():
        while not stop.wait(taskqueue.LEASE_S / 3):
            taskqueue.heartbeat(task["id"], worker_id)

    threading.Thread(target=beat, daemon=True).start()
    try:
        handler = pipeline.HANDLERS.get(task["kind"])
        if handler is None:
            raise RuntimeError(f"Unknown task kind {task['kind']}")
        handler(task["payload"])
        taskqueue.finish(task["id"], worker_id)
    except Exception as e:
        log.exception("task %s failed", task["id"])
        taskqueue.finish(task["id"], worker_id, error=f"{type(e).__name__}: {e}")
        if task["attempts"] >= task["max_attempts"]:
            pipeline.on_dead(task)
    finally:
        stop.set()
    return True


def loop(stop: threading.Event, worker_id: str | None = None, idle_s: float = 0.3) -> None:
    wid = worker_id or f"w-{os.getpid()}-{uuid.uuid4().hex[:4]}"
    while not stop.is_set():
        try:
            if not run_once(wid):
                stop.wait(idle_s)
        except Exception:
            log.exception("worker loop error")
            stop.wait(2)


_threads: list[threading.Thread] = []
_stop = threading.Event()


def start_embedded(n: int) -> None:
    if _threads or n <= 0:
        return
    _stop.clear()
    for i in range(n):
        t = threading.Thread(target=loop, args=(_stop, f"embedded-{os.getpid()}-{i}"), daemon=True, name=f"slice-worker-{i}")
        t.start()
        _threads.append(t)


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    n = int(os.environ.get("SLICE_WORKERS", "2"))
    log.info("starting %d slicer worker(s)", n)
    stop = threading.Event()
    threads = [threading.Thread(target=loop, args=(stop,), daemon=True) for _ in range(n)]
    for t in threads:
        t.start()
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        stop.set()


if __name__ == "__main__":
    main()
