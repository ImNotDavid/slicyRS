"""Start a sliced job on its printer. Used by the Print button and by the allocator's automatic start, so both obey the same
rules (printer exists and is enabled, has a connection, plate not occupied)."""
import logging
import re
import time
from pathlib import Path
from typing import Any

from . import adapter, bedstate, jobstate, store
from .adapter import AdapterError
from .storage import JOBS, files


class PrintBlocked(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status, self.message = status, message


def remote_name(job: dict[str, Any]) -> str:
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(job["name"]).stem).strip("._")[:40] or "part"
    return f"{stem}-{job['id'][:6]}.gcode.3mf"  # unique per job so prints never overwrite each other on the SD card


def start(job: dict[str, Any], bundle: Path, actor: dict[str, Any] | None, require_clear: bool = False) -> dict[str, Any]:
    from .auth import audit
    printer = store.printers.get(job["printer_id"])
    if not printer:
        raise PrintBlocked(409, "That printer no longer exists")
    if not printer.get("enabled", True):
        raise PrintBlocked(409, "That printer has been disabled by an admin")
    bed = bedstate.get(printer)["state"]
    if bed == "occupied" or (require_clear and bed != "clear"):
        raise PrintBlocked(409, f"The build plate on {printer['name']} has not been confirmed clear. "
                                f"Remove the previous print, then confirm the plate is clear on the Fleet page.")
    link = adapter.get(job["printer_id"])
    if link is None:
        raise PrintBlocked(409, "This printer has no live connection (an admin must add its IP, serial and access code); "
                                "download the file and print from SD instead")
    who = actor["email"] if actor else "allocator"
    try:
        remote = remote_name(job)
        started = link.send_print(bundle, remote)
    except AdapterError as e:
        audit(actor, "print_failed", f"{job['name']} -> {job['printer_name']}: {e}")
        raise PrintBlocked(502, str(e))
    except Exception as e:  # never leak a bare 500 for a printer problem
        logging.getLogger("slicyrs").exception("print failed")
        audit(actor, "print_failed", f"{job['name']} -> {job['printer_name']}: {type(e).__name__}")
        raise PrintBlocked(502, f"Unexpected error talking to the printer: {type(e).__name__}: {e}")
    jobstate.transition(job["id"], "printing", who, force=True, queued=True, printed=True, remote_name=remote,
                        started_at=time.time(), result=None)
    bedstate.set_state(job["printer_id"], "occupied", "print_started", by=who)
    audit(actor, "print", f"{job['name']} -> {job['printer_name']}")
    return {"ok": True, "started": started, "file": remote}


def bundle_path(job: dict[str, Any]) -> Path:
    return files.path(JOBS, job["id"], job["bundle"])
