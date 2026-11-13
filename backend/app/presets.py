"""Saved settings presets (Milestone 1.5): a person's own named settings, and presets an admin shares with everyone. A saved
preset is a process preset, a material and the full settings panel values; applying one fills the panel, and everything
stays editable."""
import time
import uuid
from typing import Any

from . import store
from .catalog import ALLOWED_SETTING_KEYS, FILAMENTS, PROCESS_PRESETS, validate_settings


class PresetError(ValueError):
    pass


def _clean(preset: str, filament: str, settings: dict[str, Any]) -> dict[str, Any]:
    if preset not in {p["id"] for p in PROCESS_PRESETS}:
        raise PresetError("Unknown process preset")
    if filament not in FILAMENTS:
        raise PresetError("Unknown filament")
    bad = sorted(set(settings) - ALLOWED_SETTING_KEYS)
    if bad:
        raise PresetError(f"Unknown settings: {bad}")
    if not all(isinstance(v, (int, float, str, bool)) for v in settings.values()):
        raise PresetError("Settings must be numbers, text or on/off values")
    if why := validate_settings(settings):
        raise PresetError(why)
    return dict(settings)


def visible_to(user: dict[str, Any]) -> list[dict[str, Any]]:
    mine_or_shared = [p for p in store.presets.all() if p["scope"] == "shared" or p["owner_id"] == user["id"]]
    return sorted(mine_or_shared, key=lambda p: (p["scope"] != "shared", p["name"].lower()))


def create(user: dict[str, Any], name: str, preset: str, filament: str, settings: dict[str, Any], shared: bool, may_share: bool) -> dict[str, Any]:
    name = name.strip()
    if not name:
        raise PresetError("Give the preset a name")
    if shared and not may_share:
        raise PresetError("Only an admin can share a preset with everyone")
    scope = "shared" if shared else "user"
    taken = [p for p in store.presets.all() if p["name"].lower() == name.lower()
             and (p["scope"] == "shared" or (scope == "user" and p["owner_id"] == user["id"]))]
    if taken:
        raise PresetError("A preset with that name already exists")
    pid = uuid.uuid4().hex[:10]
    return store.presets.put(pid, {"id": pid, "name": name, "scope": scope, "owner_id": user["id"], "owner_email": user["email"],
                                   "preset": preset, "filament": filament, "settings": _clean(preset, filament, settings), "created": time.time()})



