"""Failure learning (2.13). Owner decisions: after a failed print, its owner or the operator who clears the plate says why (either
can change it); the app looks for patterns and flags them to operators and admins only (it does not take printers out of the
pool, and members see no advice).
"""
import time
from collections import defaultdict
from typing import Any

from . import jobstate, plates, store
from .catalog import PROCESS_PRESETS

CAUSES = {
    "didnt_stick": "Did not stick to the plate",
    "spaghetti": "Spaghetti (came loose mid-print)",
    "warping": "Warping or lifting corners",
    "layer_shift": "Layer shift",
    "clog": "Clog or under-extrusion",
    "filament": "Filament ran out or tangled",
    "quality": "Finished, but the quality was bad",
    "on_purpose": "Stopped on purpose",
    "other": "Something else",
}
WINDOW_DAYS = 60
MIN_PRINTS = 4           # a combination needs at least this many prints...
MIN_FAILS = 3            # ...and this many failures...
MIN_RATE = 0.5           # ...failing at least this often to be flagged
STREAK = 5               # a printer whose last STREAK prints include MIN_FAILS failures is flagged too


def failed(job: dict[str, Any]) -> bool:
    return job.get("phase") == "failed" or (job.get("result") or {}).get("outcome") == "failed"


def set_cause(job: dict[str, Any], cause: str, note: str, user: dict[str, Any]) -> dict[str, Any]:
    if cause not in CAUSES:
        raise ValueError("Unknown cause")
    if not failed(job):
        raise ValueError("Only a failed print has a cause")
    rec = {"cause": cause, "label": CAUSES[cause], "note": (note or "").strip()[:300], "by": user["email"], "at": time.time()}
    for j in plates.jobs_on_plate(job):                       # one print, one cause: a shared plate shares it
        store.jobs.update(j["id"], cause=rec)
        jobstate.event(j["id"], "failure_cause", user["email"], cause=cause)
    return rec


def needs_cause(printer_id: str) -> str | None:
    """The failed job on a printer's latest plate that nobody has explained yet (asked when the plate is cleared)."""
    from .bedstate import last_plate
    for j in last_plate(printer_id):
        if failed(j) and not j.get("cause") and not (j.get("result") or {}).get("stopped_by"):
            return j["id"]
    return None


def _layer(job: dict[str, Any]) -> str | None:
    pid = (job.get("spec") or {}).get("preset") or job.get("preset")
    p = next((x for x in PROCESS_PRESETS if x["id"] == pid), None)
    return f"{p['layer_height']:g} mm layers" if p else None


def _spool_of(job_id: str, spools: list[dict[str, Any]]) -> dict[str, Any] | None:
    return next((s for s in spools if any(u.get("job_id") == job_id for u in s.get("used") or [])), None)


def patterns(now: float | None = None) -> dict[str, Any]:
    """Combinations that fail often, a printer's recent run of failures, and what people said the causes were."""
    now = now or time.time()
    since = now - WINDOW_DAYS * 86400
    printers = {p["id"]: p for p in store.printers.all()}
    spools = store.spools.all()
    ended = [j for j in store.jobs.all() if not j.get("batch_lead") and (j.get("result") or {}).get("outcome") in ("finished", "failed")
             and (j["result"].get("ended_at") or 0) >= since and not j["result"].get("stopped_by")
             and (j.get("cause") or {}).get("cause") != "on_purpose"]
    groups: dict[tuple, list] = defaultdict(list)
    for j in ended:
        pname = (printers.get(j.get("printer_id")) or {}).get("name") or j.get("printer_name") or "?"
        mat = j.get("filament") or "?"
        spec = j.get("spec") or {}
        keys = [("printer and material", f"{pname} · {mat}"), ("material", mat), ("build plate", j.get("bed_type") or "?")]
        if (lay := _layer(j)):
            keys.append(("layer height", lay))
        sup = (spec.get("settings") or {}).get("enable_support")
        if sup is not None:
            keys.append(("supports", "supports on" if sup else "supports off"))
        if (s := _spool_of(j["id"], spools)):
            keys.append(("spool", f"{s.get('brand') or 'spool'} {s.get('colour_name') or ''} {s['material']}".strip()))
        for k in keys:
            groups[k].append(j)
    flags = []
    for (kind, what), js in groups.items():
        n, f = len(js), sum(1 for j in js if j["result"]["outcome"] == "failed")
        if n >= MIN_PRINTS and f >= MIN_FAILS and f / n >= MIN_RATE:
            flags.append({"kind": kind, "what": what, "prints": n, "failed": f, "rate": round(100 * f / n),
                          "text": f"{what}: {f} of {n} prints failed in the last {WINDOW_DAYS} days"})
    for pid, p in printers.items():
        recent = sorted((j for j in ended if j.get("printer_id") == pid), key=lambda j: -j["result"]["ended_at"])[:STREAK]
        f = sum(1 for j in recent if j["result"]["outcome"] == "failed")
        if len(recent) >= MIN_FAILS and f >= MIN_FAILS:
            flags.append({"kind": "recent run", "what": p["name"], "prints": len(recent), "failed": f, "rate": round(100 * f / len(recent)),
                          "text": f"{p['name']}: {f} of its last {len(recent)} prints failed", "printer_id": pid})
    causes: dict[str, int] = defaultdict(int)
    unexplained = 0
    for j in ended:
        if j["result"]["outcome"] != "failed":
            continue
        if j.get("cause"):
            causes[j["cause"]["label"]] += 1
        else:
            unexplained += 1
    order = {"recent run": 0, "printer and material": 1, "spool": 2}
    return {"window_days": WINDOW_DAYS,
            "flags": sorted(flags, key=lambda x: (order.get(x["kind"], 3), -x["rate"], -x["failed"])),
            "causes": sorted(({"cause": k, "count": v} for k, v in causes.items()), key=lambda r: -r["count"]),
            "unexplained": unexplained}
