"""Disk use and clean-up. Files are deleted after a retention period; job history and statistics records are kept.

Settings are admin-editable (stored in the database), with defaults from the environment. 0 means "keep forever".
  job_files_days    G-code, print bundles and slicer files of a job, counted from when it was created or printed (default 30)
  failed_job_days   the same for jobs whose slicing failed (default 7)
  model_files_days  uploaded models and previews not used for this long (default 30)
"""
import logging
import os
import shutil
import threading
import time
from pathlib import Path
from typing import Any

from . import store
from .config import DATA_DIR, JOBS_DIR, MODELS_DIR

log = logging.getLogger("retention")
DEFAULTS = {
    "job_files_days": int(os.environ.get("RETENTION_JOB_FILES_DAYS", "30")),
    "failed_job_days": int(os.environ.get("RETENTION_FAILED_JOB_DAYS", "7")),
    "model_files_days": int(os.environ.get("RETENTION_MODEL_FILES_DAYS", "30")),
}
MIN_FREE_MB = int(os.environ.get("MIN_FREE_MB", "1024"))        # uploads are refused below this
WARN_FREE_PERCENT = 10                                           # the admin page warns below this share of free disk
INTERVAL_HOURS = float(os.environ.get("RETENTION_INTERVAL_HOURS", "6"))   # 0 turns the background clean-up off
_lock = threading.Lock()


def get_settings() -> dict[str, int]:
    saved = store.settings.get("retention") or {}
    return {k: int(saved.get(k, v)) for k, v in DEFAULTS.items()}


def set_settings(values: dict[str, int]) -> dict[str, int]:
    merged = {**get_settings(), **{k: int(v) for k, v in values.items() if k in DEFAULTS}}
    if any(v < 0 or v > 3650 for v in merged.values()):
        raise ValueError("Days must be between 0 (keep forever) and 3650")
    store.settings.put("retention", merged)
    return merged


def dir_bytes(path: Path) -> int:
    total = 0
    for root, _dirs, files in os.walk(path):
        for f in files:
            try:
                total += (Path(root) / f).stat().st_size
            except OSError:
                pass
    return total


def usage() -> dict[str, Any]:
    disk = shutil.disk_usage(DATA_DIR)
    db = sum(p.stat().st_size for p in DATA_DIR.glob("slicyrs.db*") if p.is_file())
    jobs, models = dir_bytes(JOBS_DIR), dir_bytes(MODELS_DIR)
    return {
        "disk": {"total": disk.total, "used": disk.used, "free": disk.free,
                 "free_percent": round(100 * disk.free / disk.total, 1) if disk.total else None,
                 "low": disk.free < MIN_FREE_MB * 1024 * 1024 or (disk.total and 100 * disk.free / disk.total < WARN_FREE_PERCENT)},
        "data": {"jobs": jobs, "models": models, "database": db},
        "counts": {"jobs": len(store.jobs.all()), "jobs_with_files": sum(1 for j in store.jobs.all() if not j.get("files_deleted_at") and (JOBS_DIR / j["id"]).is_dir()),
                   "models": len(store.models.all())},
        "settings": get_settings(), "last_cleanup": store.settings.get("last_cleanup"),
    }


def free_bytes() -> int:
    return shutil.disk_usage(DATA_DIR).free


def has_room_for_upload() -> bool:
    return free_bytes() >= MIN_FREE_MB * 1024 * 1024


def _job_reference_time(j: dict[str, Any]) -> float:
    return max(j.get("created", 0), j.get("started_at") or 0)


def run(now: float | None = None, dry_run: bool = False) -> dict[str, Any]:
    """Delete expired files. Returns what was (or, for a dry run, would be) removed. Never touches records or running work."""
    now = now or time.time()
    cfg = get_settings()
    report: dict[str, Any] = {"at": now, "dry_run": dry_run, "jobs_cleaned": 0, "models_cleaned": 0, "bytes_freed": 0, "settings": cfg}
    day = 86400
    with _lock:
        for j in store.jobs.all():
            if j.get("files_deleted_at") or j.get("state") == "slicing":
                continue
            days = cfg["failed_job_days"] if j.get("state") == "failed" else cfg["job_files_days"]
            if not days or now - _job_reference_time(j) < days * day:
                continue
            d = JOBS_DIR / j["id"]
            size = dir_bytes(d) if d.is_dir() else 0
            if not dry_run:
                shutil.rmtree(d, ignore_errors=True)
                store.jobs.update(j["id"], files_deleted_at=now, files_deleted_bytes=size)
            report["jobs_cleaned"] += 1
            report["bytes_freed"] += size
        for m in store.models.all():
            if m.get("files_deleted_at") or not cfg["model_files_days"]:
                continue
            if now - max(m.get("last_used", 0), m.get("created", 0)) < cfg["model_files_days"] * day:
                continue
            d = MODELS_DIR / m["id"]
            size = dir_bytes(d) if d.is_dir() else 0
            if not dry_run:
                shutil.rmtree(d, ignore_errors=True)
                store.models.update(m["id"], files_deleted_at=now)
            report["models_cleaned"] += 1
            report["bytes_freed"] += size
    if not dry_run:
        store.settings.put("last_cleanup", report)
        if report["jobs_cleaned"] or report["models_cleaned"]:
            from .auth import audit
            audit(None, "storage_cleanup", f"{report['jobs_cleaned']} jobs, {report['models_cleaned']} models, "
                                          f"{report['bytes_freed'] // (1024 * 1024)} MB freed")
    return report


def touch_model(model_id: str) -> None:
    """Remember that a model was used, so models in use are never expired. Throttled to one write an hour."""
    m = store.models.get(model_id)
    if m and time.time() - m.get("last_used", 0) > 3600:
        store.models.update(model_id, last_used=time.time())


def start_background() -> None:
    if INTERVAL_HOURS <= 0:
        return

    def loop():
        time.sleep(60)  # let the server finish starting first
        while True:
            try:
                run()
                if not usage()["disk"]["free"] >= MIN_FREE_MB * 1024 * 1024:
                    from .auth import audit
                    audit(None, "disk_low", f"{usage()['disk']['free'] // (1024 * 1024)} MB free")
            except Exception:
                log.exception("retention run failed")
            time.sleep(INTERVAL_HOURS * 3600)

    threading.Thread(target=loop, daemon=True, name="retention").start()
