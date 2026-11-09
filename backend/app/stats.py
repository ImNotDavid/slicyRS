"""Statistics from the job history and event log (A.8). Computed on request from stored records, so any new question can be
asked later without having collected anything extra."""
import time
from collections import defaultdict
from typing import Any

from . import jobstate, store


def _median(xs: list[float]) -> float | None:
    xs = sorted(xs)
    if not xs:
        return None
    m = len(xs) // 2
    return xs[m] if len(xs) % 2 else (xs[m - 1] + xs[m]) / 2


def summary(since: float | None = None) -> dict[str, Any]:
    jobs = [j for j in store.jobs.all() if not since or j.get("created", 0) >= since]
    events = store.events.all()
    first_event: dict[tuple[str, str], float] = {}
    for e in events:
        if e["kind"] == "phase":
            first_event.setdefault((e["job_id"], e["data"].get("to")), e["at"])

    by_user: dict[str, dict] = defaultdict(lambda: {"jobs": 0, "finished": 0, "failed": 0, "print_s": 0.0, "filament_g": 0.0})
    by_printer: dict[str, dict] = defaultdict(lambda: {"name": None, "jobs": 0, "finished": 0, "failed": 0, "print_s": 0.0})
    ratios_preview, ratios_sliced, waits = [], [], []
    pickup_met = pickup_missed = 0

    for j in jobs:
        res = j.get("result") or {}
        outcome = res.get("outcome")
        u = by_user[j.get("owner_email") or "?"]
        u["jobs"] += 1
        share = j.get("plate_share") or 1.0
        member = bool(j.get("batch_lead"))               # a plate is one print on the printer, however many owners it has
        if outcome == "finished":
            u["finished"] += 1
            u["filament_g"] += float((j.get("stats") or {}).get("filament_g") or 0) * share
        elif outcome == "failed":
            u["failed"] += 1
        dur = res.get("duration_s")
        if j.get("printer_id") and outcome in ("finished", "failed"):
            if not member:
                p = by_printer[j["printer_id"]]
                p["name"] = j.get("printer_name")
                p["jobs"] += 1
                p["finished" if outcome == "finished" else "failed"] += 1
                if dur:
                    p["print_s"] += dur
            if dur:
                u["print_s"] += dur * share
        if outcome == "finished" and dur and not member:        # estimate accuracy: actual / estimated
            if (j.get("preview") or {}).get("time_s"):
                ratios_preview.append(dur / j["preview"]["time_s"])
            if (j.get("stats") or {}).get("time_s"):
                ratios_sliced.append(dur / j["stats"]["time_s"])
        pooled, assigned = first_event.get((j["id"], "pooled")), first_event.get((j["id"], "assigned"))
        if pooled and assigned:
            waits.append(assigned - pooled)
        if j.get("pickup_by") and res.get("ended_at"):
            if res["ended_at"] <= j["pickup_by"]:
                pickup_met += 1
            else:
                pickup_missed += 1

    return {
        "generated": time.time(), "jobs": len(jobs),
        "by_phase": _count(j.get("phase") or "legacy" for j in jobs),
        "by_user": dict(by_user), "by_printer": dict(by_printer),
        "estimate_accuracy": {
            "samples": len(ratios_sliced),
            "sliced_median_ratio": _median(ratios_sliced),    # 1.0 = perfect, 1.2 = prints take 20% longer than estimated
            "preview_median_ratio": _median(ratios_preview),
            "preview_samples": len(ratios_preview),
        },
        "queue_wait_s": {"samples": len(waits), "median": _median(waits), "max": max(waits) if waits else None},
        "pickup": {"met": pickup_met, "missed": pickup_missed},
        "events": len(events),
    }


def _count(items) -> dict[str, int]:
    out: dict[str, int] = defaultdict(int)
    for i in items:
        out[i] += 1
    return dict(out)
