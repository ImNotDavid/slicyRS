"""Filament inventory (2.10). Owner decisions: spools with their remaining weight and cost; operators and admins manage them.

A spool may be marked as loaded on a printer. When a print ends on that printer in the spool's material, the grams the slicer
estimated (scaled by how far a failed print got) are taken off the spool, so the remaining weight stays roughly right without
anyone weighing it. Staff can correct it at any time.
"""
import time
import uuid
from typing import Any

from . import store
from .catalog import FILAMENTS


def public(s: dict[str, Any]) -> dict[str, Any]:
    start = float(s.get("start_g") or 0)
    rem = float(s.get("remaining_g") or 0)
    price = s.get("price")
    p = store.printers.get(s["printer_id"]) if s.get("printer_id") else None
    return {**{k: s.get(k) for k in ("id", "material", "colour_name", "colour_hex", "brand", "start_g", "remaining_g", "price", "notes",
                                     "printer_id", "created", "updated")},
            "printer_name": p["name"] if p else None, "percent": round(100 * rem / start) if start else None,
            "cost_per_kg": round(price / start * 1000, 2) if price and start else None,
            "used": (s.get("used") or [])[-20:], "empty": rem <= 0}


def check(material: str, start_g: float, remaining_g: float | None) -> None:
    if material not in FILAMENTS:
        raise ValueError("Unknown material")
    if start_g <= 0 or start_g > 20000:
        raise ValueError("The spool weight must be between 1 g and 20 kg")
    if remaining_g is not None and not 0 <= remaining_g <= start_g:
        raise ValueError("The remaining weight must be between 0 and the spool weight")


def add(fields: dict[str, Any], user: dict[str, Any]) -> dict[str, Any]:
    check(fields["material"], fields["start_g"], fields.get("remaining_g"))
    sid = uuid.uuid4().hex[:8]
    now = time.time()
    rec = {"id": sid, "created": now, "updated": now, "by": user["email"], "used": [], **fields}
    if rec.get("remaining_g") is None:
        rec["remaining_g"] = rec["start_g"]
    return store.spools.put(sid, rec)


def take(printer_id: str, material: str, grams: float, job_id: str) -> dict[str, Any] | None:
    """Subtract what a print used from the spool loaded on that printer (same material). Returns the spool, if any."""
    if grams <= 0:
        return None
    loaded = [s for s in store.spools.all() if s.get("printer_id") == printer_id and s.get("material") == material and float(s.get("remaining_g") or 0) > 0]
    if not loaded:
        return None
    s = max(loaded, key=lambda x: x.get("updated") or 0)        # the most recently loaded or touched one
    rem = max(0.0, float(s["remaining_g"]) - grams)
    used = (s.get("used") or []) + [{"job_id": job_id, "grams": round(grams, 1), "at": time.time()}]
    return store.spools.update(s["id"], remaining_g=round(rem, 1), used=used[-200:], updated=time.time())


def after_print(job: dict[str, Any], printer_id: str, result: dict[str, Any]) -> None:
    grams = float((job.get("stats") or {}).get("filament_g") or 0)
    if result.get("outcome") == "failed":
        grams *= max(0.0, min(1.0, float(result.get("percent") or 0) / 100))     # only what was printed before it stopped
    take(printer_id, job.get("filament") or "", grams, job["id"])


def totals() -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for s in store.spools.all():
        t = out.setdefault(s["material"], {"spools": 0, "remaining_g": 0.0, "value": 0.0})
        rem = float(s.get("remaining_g") or 0)
        if rem <= 0:
            continue
        t["spools"] += 1
        t["remaining_g"] = round(t["remaining_g"] + rem, 1)
        if s.get("price") and s.get("start_g"):
            t["value"] = round(t["value"] + float(s["price"]) * rem / float(s["start_g"]), 2)
    return out
