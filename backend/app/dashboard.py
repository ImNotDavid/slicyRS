"""Fleet dashboard for admins (2.11). Owner decisions: printer utilisation, failures by printer and material, queue and waiting
times, and filament used, over the last 7 or 30 days. Computed from stored jobs and events on request."""
import time
from collections import defaultdict
from typing import Any

from . import store
from .stats import _median

DAY = 86400


def _p90(xs: list[float]) -> float | None:
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(0.9 * (len(xs) - 1))))] if xs else None


def reason(result: dict[str, Any]) -> str:
    """A short, groupable reason for a failed print."""
    s = result.get("summary") or ""
    if result.get("stopped_by") or s.startswith("Stopped from the web"):
        return "Stopped by a person"
    if result.get("hms"):
        return "Printer error " + result["hms"][0].get("code", "")
    if result.get("print_error"):
        return f"Printer error number {result['print_error']}"
    if result.get("layer") is not None and result["layer"] <= 3:
        return "Stopped in the first layers"
    return "Stopped without an error code"


def summary(days: int = 7, now: float | None = None) -> dict[str, Any]:
    now = now or time.time()
    since = now - days * DAY
    window = days * DAY
    jobs = [j for j in store.jobs.all() if not j.get("draft")]
    printers = {p["id"]: p for p in store.printers.all()}

    busy: dict[str, float] = defaultdict(float)
    per_printer: dict[str, dict[str, Any]] = {pid: {"name": p["name"], "finished": 0, "failed": 0} for pid, p in printers.items()}
    per_material: dict[str, dict[str, int]] = defaultdict(lambda: {"finished": 0, "failed": 0})
    reasons: dict[str, int] = defaultdict(int)
    daily: dict[str, dict[str, int]] = {time.strftime("%Y-%m-%d", time.localtime(now - i * DAY)): {"finished": 0, "failed": 0} for i in range(days - 1, -1, -1)}
    grams_material: dict[str, float] = defaultdict(float)
    grams_person: dict[str, float] = defaultdict(float)

    for j in jobs:
        res = j.get("result") or {}
        outcome = res.get("outcome")
        start, end = j.get("started_at"), res.get("ended_at")
        member = bool(j.get("batch_lead"))                  # a shared plate is one print on the printer
        if start and j.get("printer_id") and not member:
            stop = end or (now if j.get("phase") == "printing" else None)
            if stop:
                busy[j["printer_id"]] += max(0.0, min(stop, now) - max(start, since))
        if outcome not in ("finished", "failed") or not end or end < since:
            continue
        share = j.get("plate_share") or 1.0
        grams = float((j.get("stats") or {}).get("filament_g") or 0)
        if outcome == "failed":
            grams *= max(0.0, min(1.0, float(res.get("percent") or 0) / 100))
        grams_material[j.get("filament") or "?"] += grams * share
        grams_person[j.get("owner_email") or "?"] += grams * share
        if member:
            continue
        if j.get("printer_id") in per_printer:
            per_printer[j["printer_id"]][outcome] += 1
        per_material[j.get("filament") or "?"][outcome] += 1
        day = time.strftime("%Y-%m-%d", time.localtime(end))
        if day in daily:
            daily[day][outcome] += 1
        if outcome == "failed":
            reasons[reason(res)] += 1

    # queue: time from entering the pool to getting a printer, for jobs that got one in the window
    pooled_at: dict[str, float] = {}
    waits: list[float] = []
    for e in sorted(store.events.all(), key=lambda e: e["at"]):
        if e["kind"] != "phase":
            continue
        to = e["data"].get("to")
        if to == "pooled":
            pooled_at.setdefault(e["job_id"], e["at"])
        elif to == "assigned" and e["job_id"] in pooled_at and e["at"] >= since:
            waits.append(e["at"] - pooled_at.pop(e["job_id"]))
    pickups = [j for j in jobs if j.get("pickup_by") and since <= j["pickup_by"] <= now]
    late = sum(1 for j in pickups if ((j.get("result") or {}).get("ended_at") or now + 1) > j["pickup_by"])

    util = [{"id": pid, "name": p["name"], "busy_s": round(busy.get(pid, 0.0)), "percent": round(100 * min(1.0, busy.get(pid, 0.0) / window), 1)}
            for pid, p in printers.items() if p.get("enabled", True)]
    def rate(d):
        n = d["finished"] + d["failed"]
        return round(100 * d["failed"] / n, 1) if n else None
    return {
        "days": days, "generated": now,
        "utilisation": sorted(util, key=lambda u: -u["percent"]),
        "prints": {"finished": sum(d["finished"] for d in daily.values()), "failed": sum(d["failed"] for d in daily.values())},
        "daily": [{"day": k, **v} for k, v in daily.items()],
        "failures_by_printer": sorted(({"name": v["name"], **{k: v[k] for k in ("finished", "failed")}, "rate": rate(v)} for v in per_printer.values()
                                       if v["finished"] + v["failed"]), key=lambda r: -(r["rate"] or 0)),
        "failures_by_material": sorted(({"material": m, **v, "rate": rate(v)} for m, v in per_material.items()), key=lambda r: -(r["rate"] or 0)),
        "failure_reasons": sorted(({"reason": k, "count": n} for k, n in reasons.items()), key=lambda r: -r["count"]),
        "queue": {"samples": len(waits), "median_s": _median(waits), "p90_s": _p90(waits), "max_s": max(waits) if waits else None,
                  "waiting_now": sum(1 for j in jobs if j.get("phase") == "pooled"), "at_risk_now": sum(1 for j in jobs if j.get("at_risk") and j.get("phase") in ("pooled", "assigned", "slicing", "ready", "printing")),
                  "pickups": len(pickups), "late_pickups": late},
        "filament": {"total_g": round(sum(grams_material.values()), 1),
                     "by_material": sorted(({"material": k, "grams": round(v, 1)} for k, v in grams_material.items()), key=lambda r: -r["grams"]),
                     "by_person": sorted(({"person": k, "grams": round(v, 1)} for k, v in grams_person.items()), key=lambda r: -r["grams"])[:10]},
    }
