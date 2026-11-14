"""Groups, roles and memberships (roadmap 3.0; design in docs/PERMISSIONS_DESIGN.md).

- A person belongs to one or more groups with one role in each; the default group always exists and everyone is in it.
- Roles are named sets of rights. Admin is fixed (every right, everywhere; it is the site-wide `role: "admin"` on the user).
  Operator and Member are built in and editable; admins can add more.
- A print belongs to a group; a printer is shared (no `groups`) or limited to some groups.
- The old per-user `operator` flag keeps meaning "Operator in the default group", so existing accounts and the Users tab
  behave as before (migration: nothing changes on the day).
"""
import time
import uuid
from typing import Any

from . import store

RIGHTS: dict[str, str] = {
    "see_all": "See every job in the group",
    "manage_jobs": "Cancel the group's jobs",
    "pause": "Pause, resume and stop the group's prints",
    "pick_printer": "Choose a specific printer when submitting",
    "plates": "Clear plates and mark them occupied",
    "maintain": "Maintenance and mark printers ready",
    "queue": "Reorder the queue and line jobs up",
    "people": "See who is using each printer",
    "members": "Manage the group's members",
    "filament": "Manage filament",
    "failures": "See failure patterns and record why prints failed",
    "share_presets": "Share presets with everyone",
}
STAFF_RIGHTS = {"see_all", "pause", "plates", "maintain", "queue", "people", "filament", "failures"}   # "is an operator somewhere"
OPERATOR_DEFAULT = ["see_all", "pause", "plates", "maintain", "queue", "people", "filament", "failures", "members"]   # today's operator + managing the group
BUILTIN = {
    "admin": {"name": "Admin", "locked": True, "desc": "Every right everywhere. Cannot be edited; at least one active admin must exist."},
    "operator": {"name": "Operator", "locked": False, "desc": "Runs printers and manages their group."},
    "member": {"name": "Member", "locked": False, "desc": "The default role for new people."},
}
DEFAULT_GROUP_NAME = "ICRS"


def ensure() -> None:
    """Create the built-in roles and the default group if they are missing (safe to call any time)."""
    for rid, b in BUILTIN.items():
        if not store.roles.get(rid):
            rights = list(RIGHTS) if rid == "admin" else OPERATOR_DEFAULT if rid == "operator" else []
            store.roles.put(rid, {"id": rid, "builtin": True, "rights": rights, "created": time.time(), **b})
    if not any(g.get("default") for g in store.groups.all()):
        gid = uuid.uuid4().hex[:8]
        store.groups.put(gid, {"id": gid, "name": DEFAULT_GROUP_NAME, "default": True, "default_role": "member", "created": time.time()})


def default_group() -> dict[str, Any]:
    g = next((g for g in store.groups.all() if g.get("default")), None)
    if g is None:
        ensure()
        g = next(g for g in store.groups.all() if g.get("default"))
    return g


def role(rid: str | None) -> dict[str, Any] | None:
    if rid in BUILTIN and not store.roles.get(rid):
        ensure()
    return store.roles.get(rid) if rid else None


def memberships(user: dict[str, Any]) -> list[dict[str, Any]]:
    """[{group_id, role_id}] with the default group always present (the legacy operator flag decides its role)."""
    dflt = default_group()
    out = [m for m in (user.get("memberships") or []) if store.groups.get(m.get("group_id"))]
    mine = next((m for m in out if m["group_id"] == dflt["id"]), None)
    if mine is None:
        mine = {"group_id": dflt["id"], "role_id": dflt.get("default_role") or "member"}
        out.insert(0, mine)
    if user.get("operator") and mine["role_id"] in ("member", None):
        mine = {**mine, "role_id": "operator"}
        out = [mine if m["group_id"] == dflt["id"] else m for m in out]
    return out


def is_admin(user: dict[str, Any]) -> bool:
    return user.get("role") == "admin"


def rights_in(user: dict[str, Any], group_id: str | None) -> set[str]:
    if not user or not user.get("active", True):
        return set()
    if is_admin(user):
        return set(RIGHTS)
    m = next((m for m in memberships(user) if m["group_id"] == group_id), None)
    r = role(m["role_id"]) if m else None
    return set(r.get("rights") or []) if r else set()


def groups_with(user: dict[str, Any], right: str) -> set[str]:
    if is_admin(user):
        return {g["id"] for g in store.groups.all()}
    return {m["group_id"] for m in memberships(user) if right in rights_in(user, m["group_id"])}


def any_right(user: dict[str, Any], right: str) -> bool:
    return is_admin(user) or bool(groups_with(user, right))


def is_staff(user: dict[str, Any]) -> bool:
    return is_admin(user) or any(rights_in(user, m["group_id"]) & STAFF_RIGHTS for m in memberships(user))


def in_group(user: dict[str, Any], group_id: str) -> bool:
    return is_admin(user) or any(m["group_id"] == group_id for m in memberships(user))


def main_group(user: dict[str, Any]) -> str:
    gid = user.get("main_group")
    return gid if gid and in_group(user, gid) and store.groups.get(gid) else default_group()["id"]


def job_group(job: dict[str, Any]) -> str:
    gid = job.get("group_id")
    return gid if gid and store.groups.get(gid) else default_group()["id"]


def printer_groups(printer: dict[str, Any] | None) -> list[str]:
    """The groups whose roles govern a printer: its own list, or the default group for a shared printer."""
    gs = [g for g in ((printer or {}).get("groups") or []) if store.groups.get(g)]
    return gs or [default_group()["id"]]


def printer_allows(printer: dict[str, Any], group_id: str | None) -> bool:
    """May a print for this group go to this printer? Shared printers take every group."""
    gs = [g for g in (printer.get("groups") or []) if store.groups.get(g)]
    return not gs or (group_id or default_group()["id"]) in gs


def user_can_use(user: dict[str, Any], printer: dict[str, Any]) -> bool:
    gs = [g for g in (printer.get("groups") or []) if store.groups.get(g)]
    return not gs or is_admin(user) or any(in_group(user, g) for g in gs)


def set_membership(user_id: str, group_id: str, role_id: str | None) -> dict[str, Any]:
    """Add, change (role_id) or remove (None) a membership. The default group's role also sets the legacy operator flag."""
    u = store.users.get(user_id)
    rest = [m for m in (u.get("memberships") or []) if m.get("group_id") != group_id]
    if role_id:
        rest.append({"group_id": group_id, "role_id": role_id})
    fields: dict[str, Any] = {"memberships": rest}
    if group_id == default_group()["id"] and role_id:
        fields["operator"] = role_id == "operator"
    if u.get("main_group") == group_id and not role_id:
        fields["main_group"] = None
    return store.users.update(user_id, **fields)


def describe(user: dict[str, Any]) -> list[dict[str, Any]]:
    """The person's groups for the web app: name, role and rights in each."""
    out = []
    main = main_group(user)
    for m in memberships(user):
        g = store.groups.get(m["group_id"])
        r = role(m["role_id"]) or {}
        out.append({"id": g["id"], "name": g["name"], "default": bool(g.get("default")), "main": g["id"] == main,
                    "role_id": r.get("id"), "role": r.get("name") or "Member", "rights": sorted(rights_in(user, g["id"]))})
    return out
