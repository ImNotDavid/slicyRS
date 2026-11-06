"""Resolve OrcaSlicer BBL preset JSONs (which use `inherits`) into flat dicts
that `orca-slicer --load-settings` accepts."""
import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from .config import ORCA_PROFILES_DIR

_BASE_KEYS_TO_DROP = {"instantiation", "inherits"}


@lru_cache(maxsize=1)
def _index() -> dict[str, Path]:
    idx: dict[str, Path] = {}
    root = ORCA_PROFILES_DIR / "BBL"
    if not root.exists():
        return idx
    for p in root.rglob("*.json"):
        try:
            name = json.loads(p.read_text(encoding="utf-8")).get("name")
        except (ValueError, OSError):
            continue
        if name:
            idx[name] = p
    return idx


def find(*candidates: str) -> str | None:
    idx = _index()
    return next((c for c in candidates if c in idx), None)


def _templates(name: str) -> list[Path]:
    if not name:
        return []
    prefix = f"{name} template "
    return [p for n, p in sorted(_index().items()) if n.startswith(prefix)]


def resolve(name: str) -> dict[str, Any]:
    idx = _index()
    if name not in idx:
        raise KeyError(f"Preset '{name}' not found under {ORCA_PROFILES_DIR}/BBL")
    chain: list[dict[str, Any]] = []
    cur: str | None = name
    while cur:
        data = json.loads(idx[cur].read_text(encoding="utf-8"))
        chain.append(data)
        cur = data.get("inherits")
        if cur and cur not in idx:
            break
    merged: dict[str, Any] = {}
    for data in reversed(chain):
        merged.update(data)
        # Newer Bambu profiles keep the printer-specific G-code (start, end, layer change, filament change,
        # time-lapse) in sibling files named "<preset> template <key>". Without them a machine silently falls back to
        # the generic base start G-code, which does none of the printer's real preparation.
        for tpl in _templates(data.get("name", "")):
            merged.update({k: v for k, v in json.loads(tpl.read_text(encoding="utf-8")).items()
                           if k not in ("name", "instantiation", "inherits")})
    for k in _BASE_KEYS_TO_DROP:
        merged.pop(k, None)
    merged["name"] = name
    if "compatible_printers" in merged or "compatible_printers_condition" in merged:
        # we choose machine/process/filament ourselves, so drop the vendor compatibility gate
        merged["compatible_printers"] = []
        merged["compatible_printers_condition"] = ""
    merged["from"] = "system"  # the CLI only accepts flattened presets marked as user presets
    return merged


def process_name(preset: str, process_suffix: str) -> str | None:
    return find(f"{preset} @BBL {process_suffix}", f"{preset} @BBL X1C")


def filament_name(orca_name: str, process_suffix: str) -> str | None:
    return find(f"{orca_name} @BBL {process_suffix}", f"{orca_name} @BBL X1C", orca_name)
