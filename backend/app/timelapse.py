"""Timelapse videos (2.8). A print records one only when its owner asked (owner decision). After the print ends, the new video is
copied from the printer's SD card into the job's files and then deleted from the card (owner decision). The job's owners,
operators and admins may watch it; it is deleted with the job's other files.

The printer finishes writing the video a little after the print ends, so this waits for a new file whose size has stopped
changing. Printers behind a gateway are not supported yet (the file would have to travel back through the gateway).
"""
import logging
import threading
import time
from typing import Any

from . import adapter, jobstate, store
from .storage import JOBS, files

log = logging.getLogger("timelapse")
FIRST_WAIT_S = 45            # the printer encodes the video after the print ends
POLL_S = 30
GIVE_UP_S = 20 * 60


def _new_video(link: Any, before: list[str]) -> str | None:
    fresh = [p for p in link.list_timelapses() if p not in set(before)]
    return fresh[-1] if fresh else None


def collect(job_id: str, printer_id: str, first_wait: float = FIRST_WAIT_S, poll: float = POLL_S, give_up: float = GIVE_UP_S,
            sleep=time.sleep) -> str | None:
    """Wait for the print's video, copy it next to the job's files and remove it from the card. Returns the saved file name."""
    job = store.jobs.get(job_id) or {}
    link = adapter.get(printer_id)
    if not job or link is None or not hasattr(link, "list_timelapses"):
        if job:
            jobstate.event(job_id, "timelapse_missing", None, reason="the printer is not reachable for file transfer")
        return None
    sleep(first_wait)
    waited, last = first_wait, (None, None)
    while waited <= give_up:
        try:
            path = _new_video(link, job.get("timelapse_before") or [])
            size = link.timelapse_size(path) if path else None
            if path and size and last == (path, size):           # the same size twice in a row: the printer has finished writing
                ext = path.rsplit(".", 1)[-1].lower()
                name = f"timelapse.{ext}"
                got = link.fetch_timelapse(path, files.path(JOBS, job_id, name))
                store.jobs.update(job_id, timelapse_file=name, timelapse_at=time.time())
                jobstate.event(job_id, "timelapse_saved", None, size=got)
                return name
            last = (path, size)
        except Exception:
            log.info("timelapse not ready for job %s", job_id, exc_info=True)
        sleep(poll)
        waited += poll
    jobstate.event(job_id, "timelapse_missing", None, reason="no new video appeared on the printer's card")
    return None


def after_print(job: dict[str, Any], printer_id: str) -> None:
    """Called when a print ends; only does anything if the owner asked for a timelapse."""
    if not job.get("timelapse_wanted") or not files.exists(JOBS, job["id"]):
        return
    threading.Thread(target=collect, args=(job["id"], printer_id), daemon=True).start()
