"""Groups, roles and memberships (3.0): the person's own groups, group operators managing members, and admin set-up."""
import time
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .. import auth, groups as G, permissions, store
from ..auth import audit, require_admin
from ..config import ALLOWED_EMAIL_DOMAINS
from .common import User

router = APIRouter()


def me_extra(user: dict[str, Any]) -> dict[str, Any]:
    """What the web app needs to know about the person's groups and staff rights (menus, Submit dialog)."""
    return {"groups": G.describe(user), "main_group": G.main_group(user),
            "can": {"all_jobs": G.any_right(user, "see_all"), "queue": G.any_right(user, "queue"), "filament": G.any_right(user, "filament"),
                    "fleet": G.is_staff(user), "manage_groups": sorted(G.groups_with(user, "members")), "admin": G.is_admin(user)}}


@router.get("/api/me/groups")
def my_groups(user: dict = User):
    return me_extra(user)


class MainIn(BaseModel):
    group_id: str


@router.put("/api/me/main_group")
def set_main(body: MainIn, user: dict = User):
    if not G.in_group(user, body.group_id):
        raise HTTPException(400, "You are not in that group")
    store.users.update(user["id"], main_group=body.group_id)
    return me_extra(store.users.get(user["id"]))


# ------------------------------------------------------------------ group operators

def _member_view(u: dict[str, Any], gid: str) -> dict[str, Any]:
    m = next((m for m in G.memberships(u) if m["group_id"] == gid), None)
    r = G.role(m["role_id"]) if m else None
    jobs = sum(1 for j in store.jobs.all() if j.get("owner_id") == u["id"] and G.job_group(j) == gid and not j.get("draft"))
    return {"id": u["id"], "email": u["email"], "name": u.get("name") or u["email"], "role_id": r["id"] if r else None,
            "role": "Admin" if G.is_admin(u) else (r or {}).get("name"), "admin": G.is_admin(u), "active": u.get("active", True),
            "invited": not u.get("last_login"), "jobs": jobs, "last_login": u.get("last_login")}


def _members(gid: str) -> list[dict[str, Any]]:
    out = [u for u in store.users.all() if any(m["group_id"] == gid for m in G.memberships(u))]
    return sorted((_member_view(u, gid) for u in out), key=lambda m: (not m["admin"], m["role"] != "Operator", m["name"].lower()))


def _assignable(user: dict[str, Any], gid: str) -> list[dict[str, Any]]:
    """Roles this person may hand out in the group: never Admin, and never more rights than they hold there (no escalation)."""
    mine = G.rights_in(user, gid)
    return [r for r in store.roles.all() if r["id"] != "admin" and (G.is_admin(user) or set(r.get("rights") or []) <= mine)]


@router.get("/api/groups/managed")
def managed_groups(user: dict = User):
    out = []
    for gid in sorted(G.groups_with(user, "members")):
        g = store.groups.get(gid)
        printers = [p["name"] for p in store.printers.all() if gid in (p.get("groups") or [])]
        out.append({"id": gid, "name": g["name"], "default": bool(g.get("default")), "members": _members(gid), "only_printers": printers,
                    "roles": [{"id": r["id"], "name": r["name"]} for r in _assignable(user, gid)],
                    "waiting": sum(1 for j in store.jobs.all() if j.get("phase") == "pooled" and G.job_group(j) == gid)})
    return out


class MemberIn(BaseModel):
    email: str
    role_id: str = "member"


def _check_role(user: dict[str, Any], gid: str, role_id: str) -> None:
    if not any(r["id"] == role_id for r in _assignable(user, gid)):
        raise HTTPException(403, "You cannot give that role in this group")


@router.post("/api/groups/{gid}/members")
def add_member(gid: str, body: MemberIn, user: dict = User):
    """Invite someone into the group (creating their account if they have none yet; they join when they first sign in)."""
    if not store.groups.get(gid):
        raise HTTPException(404, "Unknown group")
    permissions.require(user, "group.members", gid, message="Only the group's operators can manage its members")
    _check_role(user, gid, body.role_id)
    email = auth.norm_email(body.email)
    if not email:
        raise HTTPException(400, "Enter a valid email address")
    target = auth.find_user_by_email(email)
    if target is None:
        if not auth.domain_ok(email):
            raise HTTPException(400, f"Only these email domains can be invited: {', '.join(sorted(ALLOWED_EMAIL_DOMAINS))}")
        target = auth.create_user(email, "user", "", invited_by=user["email"])
    G.set_membership(target["id"], gid, body.role_id)
    audit(user, "group_member_add", f"{email} to {store.groups.get(gid)['name']} as {G.role(body.role_id)['name']}")
    return _member_view(store.users.get(target["id"]), gid)


class RoleIn(BaseModel):
    role_id: str


@router.put("/api/groups/{gid}/members/{uid}")
def change_member(gid: str, uid: str, body: RoleIn, user: dict = User):
    permissions.require(user, "group.members", gid, message="Only the group's operators can manage its members")
    target = store.users.get(uid)
    if not target or not any(m["group_id"] == gid for m in G.memberships(target)):
        raise HTTPException(404, "Not a member of this group")
    if G.is_admin(target) and not G.is_admin(user):
        raise HTTPException(403, "Only an admin can change an admin")
    current = next(m for m in G.memberships(target) if m["group_id"] == gid)
    if not G.is_admin(user) and not set((G.role(current["role_id"]) or {}).get("rights") or []) <= G.rights_in(user, gid):
        raise HTTPException(403, "You cannot change someone who holds rights you do not have")
    _check_role(user, gid, body.role_id)
    G.set_membership(uid, gid, body.role_id)
    audit(user, "group_member_role", f"{target['email']} in {store.groups.get(gid)['name']}: {G.role(body.role_id)['name']}")
    return _member_view(store.users.get(uid), gid)


@router.delete("/api/groups/{gid}/members/{uid}")
def remove_member(gid: str, uid: str, user: dict = User):
    permissions.require(user, "group.members", gid, message="Only the group's operators can manage its members")
    g = store.groups.get(gid)
    if g and g.get("default"):
        raise HTTPException(409, "Everyone stays in the default group; an admin can disable an account instead")
    target = store.users.get(uid)
    if not target or not any(m["group_id"] == gid for m in G.memberships(target)):
        raise HTTPException(404, "Not a member of this group")
    if G.is_admin(target) and not G.is_admin(user):
        raise HTTPException(403, "Only an admin can remove an admin")
    G.set_membership(uid, gid, None)
    audit(user, "group_member_remove", f"{target['email']} from {g['name']}")
    return {"ok": True}


# ------------------------------------------------------------------ admin: roles

class RoleDef(BaseModel):
    name: str = Field(min_length=1, max_length=40)
    desc: str = Field("", max_length=200)
    rights: list[str] = []


def _role_public(r: dict[str, Any]) -> dict[str, Any]:
    people = sum(1 for u in store.users.all() if (r["id"] == "admin" and G.is_admin(u)) or
                 (r["id"] != "admin" and not G.is_admin(u) and any(m["role_id"] == r["id"] for m in G.memberships(u))))
    return {**{k: r.get(k) for k in ("id", "name", "desc", "rights", "builtin", "locked")}, "people": people}


@router.get("/api/admin/roles")
def list_roles(admin: dict = Depends(require_admin)):
    G.ensure()
    order = {"admin": 0, "operator": 1, "member": 2}
    return {"roles": [_role_public(r) for r in sorted(store.roles.all(), key=lambda r: (order.get(r["id"], 3), r["name"].lower()))],
            "rights": [{"id": k, "label": v} for k, v in G.RIGHTS.items()]}


def _check_rights(rights: list[str]) -> None:
    bad = [r for r in rights if r not in G.RIGHTS]
    if bad:
        raise HTTPException(400, f"Unknown rights: {', '.join(bad)}")


@router.post("/api/admin/roles")
def add_role(body: RoleDef, admin: dict = Depends(require_admin)):
    _check_rights(body.rights)
    if any(r["name"].lower() == body.name.strip().lower() for r in store.roles.all()):
        raise HTTPException(409, "A role with that name exists")
    rid = uuid.uuid4().hex[:8]
    r = store.roles.put(rid, {"id": rid, "name": body.name.strip(), "desc": body.desc, "rights": body.rights, "builtin": False, "locked": False,
                              "created": time.time()})
    audit(admin, "role_add", r["name"])
    return _role_public(r)


@router.put("/api/admin/roles/{rid}")
def update_role(rid: str, body: RoleDef, admin: dict = Depends(require_admin)):
    r = store.roles.get(rid)
    if not r:
        raise HTTPException(404, "Unknown role")
    if r.get("locked"):
        raise HTTPException(409, "Admin is fixed and always has every right")
    _check_rights(body.rights)
    r = store.roles.update(rid, name=body.name.strip(), desc=body.desc, rights=body.rights)
    audit(admin, "role_update", f"{r['name']}: {', '.join(body.rights) or 'no rights'}")
    return _role_public(r)


@router.delete("/api/admin/roles/{rid}")
def delete_role(rid: str, admin: dict = Depends(require_admin)):
    r = store.roles.get(rid)
    if not r:
        raise HTTPException(404, "Unknown role")
    if r.get("builtin"):
        raise HTTPException(409, "Built-in roles cannot be deleted")
    for u in store.users.all():                       # people holding it fall back to Member
        for m in u.get("memberships") or []:
            if m.get("role_id") == rid:
                G.set_membership(u["id"], m["group_id"], "member")
    for g in store.groups.all():
        if g.get("default_role") == rid:
            store.groups.update(g["id"], default_role="member")
    store.roles.delete(rid)
    audit(admin, "role_delete", r["name"])
    return {"ok": True}


# ------------------------------------------------------------------ admin: groups

class GroupDef(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    default_role: str = "member"
    desc: str = Field("", max_length=200)


def _group_public(g: dict[str, Any]) -> dict[str, Any]:
    members = _members(g["id"])
    return {**{k: g.get(k) for k in ("id", "name", "desc", "default_role")}, "default": bool(g.get("default")), "members": len(members),
            "operators": [m["name"] for m in members if m["role"] == "Operator"],
            "only_printers": [{"id": p["id"], "name": p["name"]} for p in store.printers.all() if g["id"] in (p.get("groups") or [])]}


@router.get("/api/admin/groups")
def list_groups(admin: dict = Depends(require_admin)):
    G.ensure()
    return [_group_public(g) for g in sorted(store.groups.all(), key=lambda g: (not g.get("default"), g["name"].lower()))]


@router.post("/api/admin/groups")
def add_group(body: GroupDef, admin: dict = Depends(require_admin)):
    if not G.role(body.default_role) or body.default_role == "admin":
        raise HTTPException(400, "Unknown role")
    if any(g["name"].lower() == body.name.strip().lower() for g in store.groups.all()):
        raise HTTPException(409, "A group with that name exists")
    gid = uuid.uuid4().hex[:8]
    g = store.groups.put(gid, {"id": gid, "name": body.name.strip(), "desc": body.desc, "default_role": body.default_role, "default": False, "created": time.time()})
    audit(admin, "group_add", g["name"])
    return _group_public(g)


@router.put("/api/admin/groups/{gid}")
def update_group(gid: str, body: GroupDef, admin: dict = Depends(require_admin)):
    if not store.groups.get(gid):
        raise HTTPException(404, "Unknown group")
    if not G.role(body.default_role) or body.default_role == "admin":
        raise HTTPException(400, "Unknown role")
    g = store.groups.update(gid, name=body.name.strip(), desc=body.desc, default_role=body.default_role)
    audit(admin, "group_update", g["name"])
    return _group_public(g)


@router.post("/api/admin/groups/{gid}/default")
def make_default(gid: str, admin: dict = Depends(require_admin)):
    if not store.groups.get(gid):
        raise HTTPException(404, "Unknown group")
    for g in store.groups.all():
        store.groups.update(g["id"], default=g["id"] == gid)
    audit(admin, "group_default", store.groups.get(gid)["name"])
    return _group_public(store.groups.get(gid))


@router.delete("/api/admin/groups/{gid}")
def delete_group(gid: str, admin: dict = Depends(require_admin)):
    g = store.groups.get(gid)
    if not g:
        raise HTTPException(404, "Unknown group")
    if g.get("default"):
        raise HTTPException(409, "The default group cannot be deleted")
    for u in store.users.all():
        if any(m.get("group_id") == gid for m in u.get("memberships") or []):
            G.set_membership(u["id"], gid, None)
    for p in store.printers.all():
        if gid in (p.get("groups") or []):
            store.printers.update(p["id"], groups=[x for x in p["groups"] if x != gid])
    for j in store.jobs.all():                       # jobs stay; they count against the default group from now on
        if j.get("group_id") == gid:
            store.jobs.update(j["id"], group_id=None)
    store.groups.delete(gid)
    audit(admin, "group_delete", g["name"])
    return {"ok": True}


@router.post("/api/admin/groups/{gid}/members")
def admin_add_member(gid: str, body: MemberIn, admin: dict = Depends(require_admin)):
    """Admins appoint a group's first operators (or add anyone) the same way operators add members."""
    return add_member(gid, body, admin)
