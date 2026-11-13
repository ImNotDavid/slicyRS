"""Who may do what: the one place every route and module asks (2.15).

The rules are the ones the app already had, gathered here so that pause, cancel and queue controls (2.2, 2.4) and the later roles
and groups rework (3.0) change one table instead of tests scattered through the code.

Roles today: an admin (role "admin"); an operator (an ordinary user with the operator flag: clears plates and is told about
printers); everyone else is a member. An admin can do everything an operator can.

    action                  who
    admin                   admins (users, printers, gateways, storage, pipeline, audit, notification channels, statistics)
    job.see_all             operators and admins (the All jobs page); everyone has My jobs
    job.view                the job's owner, an operator or an admin (the job page, its preview and history; read only)
    job.manage              the job's owner, or an admin (download, queue, print, submit, cancel, discard)
    model.use               the model's uploader, or an admin
    plate.clear             an operator or admin, or any owner of a part on the printer's latest plate
    plate.mark_occupied     an operator or admin
    printer.see_people      an operator or admin (who is using each printer)
    printer.see_connection  admins (address, serial, gateway)
    preset.share            admins (publish a preset to everyone)
    preset.delete           the preset's owner, or an admin for a shared one
"""
from typing import Any

from fastapi import HTTPException

ACTIONS = {"admin", "job.see_all", "job.view", "job.manage", "model.use", "plate.clear", "plate.mark_occupied", "printer.see_people",
           "printer.see_connection", "preset.share", "preset.delete"}


def is_admin(user: dict[str, Any]) -> bool:
    return user.get("role") == "admin"


def is_operator(user: dict[str, Any]) -> bool:
    """Has the operator's powers (an admin has them too)."""
    return is_admin(user) or bool(user.get("operator"))


def _owns(user: dict[str, Any], rec: dict[str, Any] | None) -> bool:
    return bool(rec) and rec.get("owner_id") == user["id"]


def can(user: dict[str, Any], action: str, target: Any = None) -> bool:
    """`target` is what the action is about: a job, a model or a preset record, or a printer id for plate actions."""
    if action not in ACTIONS:
        raise ValueError(f"Unknown permission {action}")
    if not user or not user.get("active", True):
        return False
    if action in ("admin", "printer.see_connection", "preset.share"):
        return is_admin(user)
    if action in ("job.see_all", "plate.mark_occupied", "printer.see_people"):
        return is_operator(user)
    if action == "job.view":
        return bool(target) and (_owns(user, target) or is_operator(user))
    if action in ("job.manage", "model.use"):
        return bool(target) and (_owns(user, target) or is_admin(user))     # nothing to act on is never allowed, even for admins
    if action == "plate.clear":
        if is_operator(user):
            return True
        from .bedstate import last_plate     # local import: bedstate asks this module too
        return any(j.get("owner_id") == user["id"] for j in last_plate(target))   # any one owner may clear a shared plate
    if action == "preset.delete":
        return _owns(user, target) or (is_admin(user) and (target or {}).get("scope") == "shared")
    return False


def require(user: dict[str, Any], action: str, target: Any = None, *, hide: bool = False, message: str = "") -> None:
    """Raise if not allowed. `hide` answers 404, as if the thing did not exist, so people cannot probe for other people's jobs
    and models; otherwise 403 with `message`."""
    if not can(user, action, target):
        if hide:
            raise HTTPException(404, message or "Not found")
        raise HTTPException(403, message or "You are not allowed to do that")
