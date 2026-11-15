"""Quotas (3.2). Owner decisions: print hours and filament per person, set per group; allowances start again every week
(Monday), every month (the 1st) or every term (admin-set dates), one choice for the whole site;
members are warned at 80%; a print that would go over is refused; group operators can top a person up.

A print counts against the group it is for, in the term it was submitted in: finished prints by what they really took,
failed ones by the share printed, and prints not finished yet by their estimate. A shared plate counts each owner's share.
"""
import time
import uuid
from datetime import datetime, timedelta
from typing import Any

from . import groups as G, store

WARN_AT = 0.8
CYCLES = {"week": "week", "month": "month", "term": "term"}     # how often allowances start again
COUNTED = {"awaiting_approval", "pooled", "assigned", "slicing", "ready", "printing", "printed", "failed", "unknown"}


def periods() -> list[dict[str, Any]]:
    return sorted((store.settings.get("periods") or {}).get("list") or [], key=lambda p: p["start"])


def set_periods(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    clean = []
    for p in items:
        if not p.get("name") or float(p["end"]) <= float(p["start"]):
            raise ValueError("Each term needs a name and must end after it starts")
        clean.append({"id": p.get("id") or uuid.uuid4().hex[:6], "name": p["name"].strip()[:40], "start": float(p["start"]), "end": float(p["end"])})
    clean.sort(key=lambda p: p["start"])
    for a, b in zip(clean, clean[1:]):
        if b["start"] < a["end"]:
            raise ValueError(f"{a['name']} and {b['name']} overlap")
    store.settings.put("periods", {"list": clean})
    return clean


def cycle() -> str:
    return (store.settings.get("quota_cycle") or {}).get("cycle") or "term"


def set_cycle(c: str) -> str:
    if c not in CYCLES:
        raise ValueError("The quota period must be week, month or term")
    store.settings.put("quota_cycle", {"cycle": c})
    return c


def current_period(now: float | None = None) -> dict[str, Any] | None:
    """The quota period that `now` falls in: a calendar week from Monday, a calendar month, or an admin-set term (local time)."""
    now = now or time.time()
    c = cycle()
    if c == "term":
        return next((p for p in periods() if p["start"] <= now < p["end"]), None)
    d = datetime.fromtimestamp(now).replace(hour=0, minute=0, second=0, microsecond=0)
    if c == "week":
        start = d - timedelta(days=d.weekday())
        end = start + timedelta(days=7)
        return {"id": "w" + start.strftime("%Y-%m-%d"), "name": f"Week of {start.day} {start.strftime('%b')}", "start": start.timestamp(), "end": end.timestamp()}
    start = d.replace(day=1)
    end = start.replace(year=start.year + 1, month=1) if start.month == 12 else start.replace(month=start.month + 1)
    return {"id": "m" + start.strftime("%Y-%m"), "name": start.strftime("%B %Y"), "start": start.timestamp(), "end": end.timestamp()}


def _cost(job: dict[str, Any]) -> tuple[float, float]:
    """(hours, grams) a job counts for its owner."""
    res = job.get("result") or {}
    est = job.get("stats") or job.get("preview") or {}
    share = job.get("plate_share") or 1.0
    grams = float(est.get("filament_g") or 0)
    secs = float(res.get("duration_s") or est.get("time_s") or 0)
    if res.get("outcome") == "failed":
        grams *= max(0.0, min(1.0, float(res.get("percent") or 0) / 100))
    return secs / 3600 * share, grams * share


def usage(user_id: str, group_id: str, period: dict[str, Any] | None) -> dict[str, float]:
    if not period:
        return {"hours": 0.0, "grams": 0.0}
    h = g = 0.0
    for j in store.jobs.all():
        if j.get("owner_id") != user_id or j.get("draft") or j.get("phase") not in COUNTED or G.job_group(j) != group_id:
            continue
        t = j.get("submitted_at") or j.get("created") or 0
        if period["start"] <= t < period["end"]:
            dh, dg = _cost(j)
            h, g = h + dh, g + dg
    return {"hours": round(h, 2), "grams": round(g, 1)}


def topups(user_id: str, group_id: str, period: dict[str, Any] | None) -> dict[str, float]:
    if not period:
        return {"hours": 0.0, "grams": 0.0}
    rows = [t for t in store.topups.all() if t["user_id"] == user_id and t["group_id"] == group_id and t["period_id"] == period["id"]]
    return {"hours": sum(float(t.get("hours") or 0) for t in rows), "grams": sum(float(t.get("grams") or 0) for t in rows)}


def status(user_id: str, group_id: str, now: float | None = None) -> dict[str, Any]:
    """Allowance, extra, used and left for a person in a group this term (None limits mean no quota)."""
    g = store.groups.get(group_id) or {}
    per = current_period(now)
    lim_h, lim_g = g.get("quota_hours"), g.get("quota_g")
    used, extra = usage(user_id, group_id, per), topups(user_id, group_id, per)
    out: dict[str, Any] = {"group_id": group_id, "group": g.get("name"), "period": per, "cycle": cycle(), "used": used, "extra": extra}
    for k, lim in (("hours", lim_h), ("grams", lim_g)):
        if lim is None or per is None:
            out[k] = None
            continue
        allow = float(lim) + extra[k]
        out[k] = {"allowance": allow, "used": used[k], "left": round(allow - used[k], 2), "share": (used[k] / allow) if allow else 1.0,
                  "warn": allow > 0 and used[k] / allow >= WARN_AT}
    return out


def check(user: dict[str, Any], group_id: str, hours: float, grams: float) -> str | None:
    """Why this print would go over the person's quota in that group, or None."""
    if G.is_admin(user):
        return None
    s = status(user["id"], group_id)
    for k, need, unit in (("hours", hours, "h"), ("grams", grams, "g")):
        q = s[k]
        if q and need > q["left"] + 1e-6:
            fmt = (lambda v: f"{v:.1f} h") if k == "hours" else (lambda v: f"{v:.0f} g")
            return (f"This print needs {fmt(need)} but you have {fmt(max(0.0, q['left']))} left of your {fmt(q['allowance'])} for "
                    f"{s['group']} this {s['cycle']} ({s['period']['name']}). Ask one of the group's operators for more.")
    return None


def top_up(user_id: str, group_id: str, hours: float, grams: float, by: dict[str, Any], note: str = "") -> dict[str, Any]:
    per = current_period()
    if not per:
        raise ValueError("There is no current quota period to add to (no term covers today)")
    tid = uuid.uuid4().hex[:8]
    return store.topups.put(tid, {"id": tid, "user_id": user_id, "group_id": group_id, "period_id": per["id"], "hours": hours, "grams": grams,
                                  "by": by["email"], "note": note[:200], "at": time.time()})
