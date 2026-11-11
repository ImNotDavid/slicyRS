"""What the settings panel shows as defaults, and which settings the user actually changed.

The slicer's own printer profile must stay in charge of everything the user did not touch (speeds, accelerations,
temperatures, start G-code ...). So the panel's baseline comes from a real reference profile when one is installed,
and only values that differ from that baseline are sent to the slicer as overrides.
"""
from functools import lru_cache
from typing import Any

from . import profiles
from .catalog import ALLOWED_SETTING_KEYS, FIELDS, preset_defaults
from .config import SLICER_MODE

REFERENCE_PRINTER_SUFFIX = "P2S"  # same generic machine the preview slice uses (falls back to X1C if the preset is missing)


def _parse(raw: Any, like: Any) -> Any:
    """Convert a profile value (always text, sometimes a one-item list, "15%" for percentages) to the type of `like`."""
    if isinstance(raw, list):
        raw = raw[0] if raw else like
    if isinstance(like, bool):
        return str(raw).strip().lower() in ("1", "true", "yes")
    if isinstance(like, (int, float)):
        try:
            v = float(str(raw).rstrip("%"))
        except ValueError:
            return like
        return int(v) if isinstance(like, int) and v == int(v) else v
    return str(raw)


def _typed(raw: Any, field: dict[str, Any], like: Any) -> Any:
    """A profile value as the settings panel holds it, by the setting's type in the schema."""
    if isinstance(raw, list):
        raw = raw[0] if raw else like
    kind = field["type"]
    if kind == "bool":
        return str(raw).strip().lower() in ("1", "true", "yes")
    if kind in ("number", "percent"):
        try:
            v = float(str(raw).rstrip("%"))
        except ValueError:
            return like
        return int(v) if field.get("integer") or v == int(v) and kind == "percent" else v
    return str(raw)


@lru_cache(maxsize=64)
def _ui_defaults(preset_id: str) -> tuple[tuple[str, Any], ...]:
    base = dict(preset_defaults(preset_id))
    if SLICER_MODE == "orca":
        try:
            name = profiles.process_name(preset_id, REFERENCE_PRINTER_SUFFIX)
            real = profiles.resolve(name) if name else {}
        except (KeyError, OSError, ValueError):
            real = {}
        for key, like in list(base.items()):
            if key in real:
                base[key] = _typed(real[key], FIELDS[key], like) if key in FIELDS else _parse(real[key], like)
    return tuple(base.items())


def ui_defaults(preset_id: str) -> dict[str, Any]:
    """Values shown in the settings panel for a preset (only keys the panel can show)."""
    return {k: v for k, v in _ui_defaults(preset_id) if k in ALLOWED_SETTING_KEYS}


def effective_overrides(preset_id: str, settings: dict[str, Any], bed_type: str) -> dict[str, Any]:
    """Overrides handed to the slicer: only what the user changed, plus the printer's build plate."""
    defaults = ui_defaults(preset_id)
    edits = {k: v for k, v in settings.items() if k in ALLOWED_SETTING_KEYS and v != defaults.get(k)}
    # the mock slicer has no profiles to fall back on, so it needs the full set of values
    base = {} if SLICER_MODE == "orca" else preset_defaults(preset_id)
    return {**base, **edits, "curr_bed_type": bed_type}
