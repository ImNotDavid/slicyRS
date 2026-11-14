"""Who may do what: the one place every route and module asks (2.15), now per group (3.0, docs/PERMISSIONS_DESIGN.md).

A person has one role in each group they belong to; a role is a set of rights (`groups.RIGHTS`). Admin is fixed and may do
everything. A job belongs to a group; a printer is shared (governed by the default group) or limited to some groups.

    action                  allowed when                                                      target
    admin                   site admin                                                        -
    job.see_all             "see_all" in any group (the All jobs page shows those groups)     -
    job.view                the job's owner, or "see_all" in the job's group                  job
    job.manage              the job's owner, or "manage_jobs" in the job's group              job
    print.pause             an owner on the plate, or "pause" in the job's group              job
    print.stop              as print.pause, but an owner only if the plate is not shared      job
    model.use               the model's uploader, or an admin                                 model
    plate.clear             an owner on the printer's latest plate, or "plates" for it        printer id
    plate.mark_occupied     "plates" for the printer                                          printer id (or none: any)
    printer.see_people      "people" for the printer (none: in any group)                     printer (optional)
    printer.see_connection  site admin                                                        -
    printer.maintain        "maintain" for the printer (none: in any group)                   printer id (optional)
    printer.pick            "pick_printer" in the group the print is for                      group id
    queue.manage            "queue" in any group                                              -
    filament.manage         "filament" in any group                                           -
    failures.view           "failures" in any group (owner decision: members see no advice)  -
    failures.explain        an owner on the plate, or "failures" in the job's group           job
    group.members           "members" in that group                                           group id
    preset.share            "share_presets" in any group                                      -
    preset.delete           the preset's owner, or an admin for a shared one                  preset
"""
from typing import Any

from fastapi import HTTPException

from . import groups as G

ACTIONS = {"admin", "job.see_all", "job.view", "job.manage", "print.pause", "print.stop", "model.use", "plate.clear",
           "plate.mark_occupied", "printer.see_people", "printer.see_connection", "printer.maintain", "printer.pick",
           "queue.manage", "filament.manage", "failures.view", "failures.explain", "group.members", "preset.share", "preset.delete"}


def is_admin(user: dict[str, Any]) -> bool:
    return G.is_admin(user)


def is_operator(user: dict[str, Any]) -> bool:
    """Has staff rights somewhere (used for menus and for who is told about printer problems)."""
    return bool(user) and user.get("active", True) and G.is_staff(user)


def _owns(user: dict[str, Any], rec: dict[str, Any] | None) -> bool:
    return bool(rec) and rec.get("owner_id") == user["id"]


def _printer_right(user: dict[str, Any], right: str, printer_id: str | None) -> bool:
    if printer_id is None:
        return G.any_right(user, right)
    from . import store
    p = store.printers.get(printer_id)
    return any(right in G.rights_in(user, g) for g in G.printer_groups(p))


def can(user: dict[str, Any], action: str, target: Any = None) -> bool:
    if action not in ACTIONS:
        raise ValueError(f"Unknown permission {action}")
    if not user or not user.get("active", True):
        return False
    if action in ("admin", "printer.see_connection"):
        return is_admin(user)
    if action == "job.see_all":
        return G.any_right(user, "see_all")
    if action in ("queue.manage", "filament.manage", "failures.view", "preset.share"):
        return G.any_right(user, {"queue.manage": "queue", "filament.manage": "filament", "failures.view": "failures",
                                  "preset.share": "share_presets"}[action])
    if action == "job.view":
        return bool(target) and (_owns(user, target) or "see_all" in G.rights_in(user, G.job_group(target)))
    if action == "job.manage":
        return bool(target) and (_owns(user, target) or "manage_jobs" in G.rights_in(user, G.job_group(target)))
    if action == "model.use":
        return bool(target) and (_owns(user, target) or is_admin(user))
    if action in ("print.pause", "print.stop", "failures.explain"):
        if not target:
            return False
        right = "failures" if action == "failures.explain" else "pause"
        if right in G.rights_in(user, G.job_group(target)):
            return True
        from .plates import jobs_on_plate   # local import: plates imports the store at load time
        plate = jobs_on_plate(target)
        mine = any(j.get("owner_id") == user["id"] for j in plate)
        if action == "print.stop":
            return mine and len({j.get("owner_id") for j in plate}) <= 1     # stopping ruins every part on a shared plate
        return mine
    if action == "plate.clear":
        if _printer_right(user, "plates", target):
            return True
        from .bedstate import last_plate     # local import: bedstate asks this module too
        return any(j.get("owner_id") == user["id"] for j in last_plate(target))   # any one owner may clear a shared plate
    if action == "plate.mark_occupied":
        return _printer_right(user, "plates", target)
    if action == "printer.see_people":
        return _printer_right(user, "people", (target or {}).get("id") if isinstance(target, dict) else target)
    if action == "printer.maintain":
        return _printer_right(user, "maintain", target)
    if action == "printer.pick":
        return bool(target) and "pick_printer" in G.rights_in(user, target)
    if action == "group.members":
        return bool(target) and "members" in G.rights_in(user, target)
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
