"""Filament cost tracking (3.4). Owner decision: filament only (no machine time).

Each finished or failed print costs the grams it really used (a failed print: the share printed before it stopped; a shared
plate: each owner's share) times a price per kg. The price is the spool it was printed from, when the filament inventory
knows it (2.10), else the admin's default for that material. Prints with neither are counted in grams and shown as unpriced.
Reports group by person and by group over a term (3.2) or a date range; the CSV has one line per print.
"""
import csv
import io
import time
from typing import Any

from . import groups as G, store

KEY = "filament_prices"
COSTED = {"printed", "failed", "unknown"}


def prices() -> dict[str, float]:
    return dict((store.settings.get(KEY) or {}).get("per_kg") or {})


def set_prices(per_kg: dict[str, float | None]) -> dict[str, float]:
    clean = {m: round(float(v), 2) for m, v in per_kg.items() if v is not None and float(v) > 0}
    store.settings.put(KEY, {"per_kg": clean, "updated": time.time()})
    return clean


def _spool_rate_by_job() -> dict[str, tuple[float, str]]:
    """job id -> (price per kg, spool label) for prints the inventory recorded against a priced spool."""
    out: dict[str, tuple[float, str]] = {}
    for s in store.spools.all():
        if not (s.get("price") and s.get("start_g")):
            continue
        rate = float(s["price"]) / float(s["start_g"]) * 1000
        label = " ".join(x for x in (s.get("brand"), s.get("material"), s.get("colour_name")) if x)
        for u in s.get("used") or []:
            out[u["job_id"]] = (rate, label)
    return out


def _grams(job: dict[str, Any]) -> float:
    res = job.get("result") or {}
    g = float((job.get("stats") or {}).get("filament_g") or 0) * float(job.get("plate_share") or 1.0)
    if res.get("outcome") == "failed" or job.get("phase") == "failed":
        g *= max(0.0, min(1.0, float(res.get("percent") or 0) / 100))
    return g


def lines(start: float, end: float) -> list[dict[str, Any]]:
    """One line per print that ended in [start, end)."""
    rates, defaults = _spool_rate_by_job(), prices()
    users = {u["id"]: u for u in store.users.all()}
    groups = {g["id"]: g for g in store.groups.all()}
    out = []
    for j in store.jobs.all():
        if j.get("phase") not in COSTED or j.get("draft"):
            continue
        at = j.get("phase_at") or j.get("created") or 0
        if not start <= at < end:
            continue
        grams = _grams(j)
        lead = j.get("batch_lead") or j["id"]
        rate, source = rates.get(j["id"]) or rates.get(lead) or (None, "")
        if rate is None and j.get("filament") in defaults:
            rate, source = defaults[j["filament"]], "default price"
        owner = users.get(j.get("owner_id") or "") or {}
        gid = G.job_group(j)
        out.append({"job_id": j["id"], "at": at, "job": j["name"], "owner": owner.get("name") or owner.get("email") or "?",
                    "email": owner.get("email") or "", "group_id": gid, "group": (groups.get(gid) or {}).get("name") or "",
                    "material": j.get("filament") or "", "outcome": j.get("phase"), "grams": round(grams, 1),
                    "price_per_kg": round(rate, 2) if rate is not None else None, "source": source or "no price",
                    "cost": round(grams / 1000 * rate, 2) if rate is not None else None})
    return sorted(out, key=lambda r: r["at"])


def report(start: float, end: float) -> dict[str, Any]:
    rows = lines(start, end)

    def roll(key: str, label: str) -> list[dict[str, Any]]:
        acc: dict[str, dict[str, Any]] = {}
        for r in rows:
            a = acc.setdefault(r[key], {"key": r[key], "name": r[label], "prints": 0, "grams": 0.0, "cost": 0.0, "unpriced_g": 0.0})
            a["prints"] += 1
            a["grams"] += r["grams"]
            if r["cost"] is None:
                a["unpriced_g"] += r["grams"]
            else:
                a["cost"] += r["cost"]
        return sorted(({**a, "grams": round(a["grams"], 1), "cost": round(a["cost"], 2), "unpriced_g": round(a["unpriced_g"], 1)}
                       for a in acc.values()), key=lambda a: -a["cost"])

    return {"start": start, "end": end, "prints": len(rows), "grams": round(sum(r["grams"] for r in rows), 1),
            "cost": round(sum(r["cost"] or 0 for r in rows), 2), "unpriced_g": round(sum(r["grams"] for r in rows if r["cost"] is None), 1),
            "by_person": roll("email", "owner"), "by_group": roll("group_id", "group")}


def to_csv(rows: list[dict[str, Any]], columns: list[tuple[str, str]]) -> str:
    """CSV text with a header row; cells that a spreadsheet would run as a formula are made plain text."""
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([title for _, title in columns])
    for r in rows:
        cells = []
        for key, _ in columns:
            v = r.get(key)
            v = "" if v is None else time.strftime("%Y-%m-%d %H:%M", time.localtime(v)) if key == "at" else v
            if isinstance(v, str) and v[:1] in ("=", "+", "-", "@", "\t", "\r"):
                v = "'" + v
            cells.append(v)
        w.writerow(cells)
    return buf.getvalue()


CSV_COLUMNS = [("at", "Ended"), ("job", "Print"), ("owner", "Person"), ("email", "Email"), ("group", "Group"), ("material", "Material"),
               ("outcome", "Outcome"), ("grams", "Grams"), ("price_per_kg", "Price per kg"), ("source", "Price from"), ("cost", "Cost")]
