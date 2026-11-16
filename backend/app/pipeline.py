"""The slicing steps of the job pipeline, run by workers from the task queue (A.1, A.5).

  preview  slice for a generic reference printer (the P2S profile when installed, else X1C) so the user sees an estimate
           and a pickup time can be judged before any printer is chosen
  slice    slice for the printer the allocator chose, then optionally start the print
"""
import logging
from pathlib import Path
from typing import Any

from . import jobstate, plates, printing, slicer, store, taskqueue, thumbnail
from .catalog import DEFAULT_BED_TYPE, PRINTER_MODELS, job_is_plate, job_position
from .storage import JOBS, files
from .defaults import effective_overrides

log = logging.getLogger("pipeline")
PREVIEW_MODELS = ("P2S", "X1C")   # the P2S is the reference printer for previews (docs/ARCHITECTURE.md); X1C if it is ever removed


def preview_model() -> str:
    return next((m for m in PREVIEW_MODELS if m in PRINTER_MODELS), "X1C")


def request_preview(job_id: str) -> None:
    jobstate.transition(job_id, "previewing", None)
    taskqueue.enqueue("preview", {"job_id": job_id})


def request_slice(job_id: str) -> None:
    taskqueue.enqueue("slice", {"job_id": job_id})


def _input(job: dict[str, Any]) -> Path:
    return files.path(JOBS, job["id"], job["input_file"])


def handle_preview(payload: dict[str, Any]) -> None:
    job = store.jobs.get(payload["job_id"])
    if not job or job.get("phase") != "previewing":
        return
    spec = job["spec"]
    wd = files.path(JOBS, job["id"], "preview")
    try:
        # a laid-out plate is estimated with the slicer free to arrange it: the preview printer's bed is not the one it will print on
        extra = {"placements": [(files.path(JOBS, job["id"], q["file"]), (0.0, 0.0)) for q in spec["parts"]], "rearrange": True} if job_is_plate(job) else {}
        r = slicer.run(wd, _input(job), preview_model(), spec["preset"], spec["filament"],
                       effective_overrides(spec["preset"], spec["settings"], DEFAULT_BED_TYPE), **extra)
        preview = {"time_s": r.get("time_s"), "filament_g": r.get("filament_g"), "layers": r.get("layers"), "model": preview_model()}
        if (store.jobs.get(job["id"]) or {}).get("phase") == "previewing":
            jobstate.transition(job["id"], "previewed", None, preview=preview)
    except Exception as e:
        log.info("preview failed for %s: %s", job["id"], e)
        jobstate.transition(job["id"], "failed", None, state="failed", error=str(e))
    finally:
        files.remove_subdir(JOBS, job["id"], "preview")   # only the numbers are kept


def handle_slice(payload: dict[str, Any]) -> None:
    job = store.jobs.get(payload["job_id"])
    if not job or job.get("phase") != "slicing":
        return
    spec = job["spec"]
    wd = files.workdir(JOBS, job["id"])
    members = [m for m in (store.jobs.get(i) for i in job.get("members") or []) if m]
    inputs = [_input(job)] + [_input(m) for m in members]
    placements = [(files.path(JOBS, job["id"], q["file"]), tuple(q["position"])) for q in spec["parts"]] if job_is_plate(job) else None
    try:
        r = slicer.run(wd, inputs, job["printer_model"], spec["preset"], spec["filament"],
                       effective_overrides(spec["preset"], spec["settings"], job.get("bed_type") or DEFAULT_BED_TYPE),
                       object_settings=[{}] + [m.get("plate_overrides") or {} for m in members],
                       offset=job_position(job) if not members and not placements else None, placements=placements)
    except Exception as e:
        if (store.jobs.get(job["id"]) or {}).get("phase") == "slicing":
            if plates.drop_last(job["id"], f"the plate could not be sliced ({e})"):
                request_slice(job["id"])         # try again with one job fewer
            else:
                jobstate.transition(job["id"], "failed", None, state="failed", error=str(e))
        return
    if not members and not placements and r.get("bundle"):
        thumbnail.maybe_add(files.path(JOBS, job["id"], r["bundle"]), _input(job))
    files.publish(JOBS, job["id"])
    cur = store.jobs.get(job["id"]) or {}
    if cur.get("phase") != "slicing":
        return                                    # cancelled while slicing: drop the result
    jobstate.transition(job["id"], "ready", None, stats=r, gcode=r["gcode"], bundle=r.get("bundle"))
    if plates.auto_start(store.jobs.get(job["id"])):
        try:
            printing.start(store.jobs.get(job["id"]), printing.bundle_path(store.jobs.get(job["id"])), None, require_clear=True)
        except printing.PrintBlocked as e:
            jobstate.event(job["id"], "auto_start_failed", None, reason=e.message)   # stays ready for the owner to start


def on_dead(task: dict[str, Any]) -> None:
    """A task that kept losing its worker: fail the job instead of leaving it waiting for ever."""
    jid = task["payload"].get("job_id")
    job = store.jobs.get(jid) if jid else None
    if job and job.get("phase") in ("previewing", "slicing"):
        jobstate.transition(jid, "failed", None, state="failed", error="The slicer stopped repeatedly while working on this job. Try again.")


HANDLERS = {"preview": handle_preview, "slice": handle_slice}
