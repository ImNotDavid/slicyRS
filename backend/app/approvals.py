"""Approval workflow (3.3). Owner decisions: a print waits for a group operator's approval when the person's role in the group
has the "prints need approval" right, when it is longer or uses more filament than the group's set limits, or when it is in a
material the group has listed. Approvers are people with the "approve" right in the print's group.

Admins and approvers never wait for approval of their own prints. An approved print joins the pool with its original
submission time, so the wait for approval does not push it back in the queue.
"""
import time
from typing import Any

from . import groups as G, jobstate, notify, store


def _estimate(job: dict[str, Any]) -> tuple[float, float]:
    est = job.get("preview") or job.get("stats") or {}
    return float(est.get("time_s") or 0) / 3600, float(est.get("filament_g") or 0)


def reasons(user: dict[str, Any], job: dict[str, Any], group_id: str) -> list[str]:
    """Why this print needs approval in that group (empty: it does not)."""
    rights = G.rights_in(user, group_id)
    if G.is_admin(user) or "approve" in rights:
        return []
    g = store.groups.get(group_id) or {}
    hours, grams = _estimate(job)
    out = []
    if "needs_approval" in rights:
        out.append(f"your role in {g.get('name')} needs approval for every print")
    if g.get("approve_over_h") and hours > float(g["approve_over_h"]):
        out.append(f"it takes longer than {g['approve_over_h']:g} h")
    if g.get("approve_over_g") and grams > float(g["approve_over_g"]):
        out.append(f"it uses more than {g['approve_over_g']:g} g of filament")
    if job.get("filament") in (g.get("approve_materials") or []):
        out.append(f"{job['filament']} prints need approval")
    return out


def approvers(group_id: str) -> list[dict[str, Any]]:
    """People whose role in the group lets them approve; the admins only if the group has nobody else."""
    people = [u for u in store.users.all() if u.get("active", True) and not G.is_admin(u) and "approve" in G.rights_in(u, group_id)]
    return people or [u for u in store.users.all() if u.get("active", True) and G.is_admin(u)]


def request(job: dict[str, Any], why: list[str], user: dict[str, Any]) -> None:
    gid = G.job_group(job)
    store.jobs.update(job["id"], approval={"reasons": why, "requested": time.time()})
    jobstate.event(job["id"], "approval_requested", user["email"], reasons=why)
    g = store.groups.get(gid) or {}
    hours, grams = _estimate(job)
    notify.emit("approval_needed", f"Approval needed: {job['name']}",
                f"{user.get('name') or user['email']} sent {job['name']} for {g.get('name')} ({hours:.1f} h, {grams:.0f} g of {job.get('filament')}). "
                f"It needs approval because {' and '.join(why)}.", link="/#/approvals", staff=[u["id"] for u in approvers(gid)], skip=user["email"])


def waiting_for(user: dict[str, Any]) -> list[dict[str, Any]]:
    """Prints waiting in the groups where this person can approve, oldest first."""
    mine = G.groups_with(user, "approve")
    out = [j for j in store.jobs.all() if j.get("phase") == "awaiting_approval" and G.job_group(j) in mine]
    return sorted(out, key=lambda j: j.get("submitted_at") or 0)


def decide(job: dict[str, Any], approve: bool, by: dict[str, Any], note: str = "") -> dict[str, Any]:
    if job.get("phase") != "awaiting_approval":
        raise ValueError("This print is not waiting for approval")
    owner = store.users.get(job.get("owner_id") or "") or {}
    decision = {**(job.get("approval") or {}), "decision": "approved" if approve else "rejected", "by": by["email"],
                "by_name": by.get("name") or by["email"], "at": time.time(), "note": note.strip()[:300]}
    if approve:
        out = jobstate.transition(job["id"], "pooled", by["email"], state="pooled", pooled=True, approval=decision, reason="approved")
        notify.emit("approved", f"Approved: {job['name']}", f"{decision['by_name']} approved {job['name']}. It is in the pool and starts "
                    "by itself when a printer that fits is free." + (f"\n\nNote: {decision['note']}" if decision["note"] else ""),
                    owners=[owner.get("email")] if owner else [], link=f"/#/job/{job['id']}")
        from . import allocator
        allocator.tick()
    else:
        out = jobstate.transition(job["id"], "rejected", by["email"], approval=decision, error=f"Rejected by {decision['by_name']}" +
                                  (f": {decision['note']}" if decision["note"] else ""), reason="rejected")
        notify.emit("rejected", f"Rejected: {job['name']}", f"{decision['by_name']} rejected {job['name']}." +
                    (f"\n\nReason: {decision['note']}" if decision["note"] else ""), owners=[owner.get("email")] if owner else [],
                    link=f"/#/job/{job['id']}")
    return out
