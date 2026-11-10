"""Catalog, goals and saved presets."""
import os
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .. import goals, presets, store
from ..auth import audit, is_admin
from ..defaults import ui_defaults
from ..catalog import BED_TYPES, DEFAULT_BED_TYPE, FILAMENTS, PRINTER_MODELS, PROCESS_PRESETS, SETTINGS_SCHEMA
from ..config import ORCA_BIN, SLICER_MODE
from .common import User

router = APIRouter()


@router.get("/api/health")
def health():
    return {"ok": True, "slicer_mode": SLICER_MODE}


@router.get("/api/catalog")
def catalog(user: dict = User):
    return {
        "printer_models": {k: {**v, "bed": list(v["bed"])} for k, v in PRINTER_MODELS.items()},
        "filaments": FILAMENTS,
        "process_presets": PROCESS_PRESETS,
        "goals": goals.public(),
        "settings_schema": SETTINGS_SCHEMA,
        "preset_defaults": {p["id"]: ui_defaults(p["id"]) for p in PROCESS_PRESETS},
        "bed_types": list(BED_TYPES),
        "default_bed_type": DEFAULT_BED_TYPE,
        "slicer_mode": SLICER_MODE,
        "engine": ("Mock slicer" if SLICER_MODE == "mock"
                   else os.environ.get("SLICER_NAME") or ("Bambu Studio" if "bambu" in ORCA_BIN.lower() else "OrcaSlicer")),
    }


class PresetIn(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    preset: str
    filament: str
    settings: dict[str, Any]
    shared: bool = False


@router.get("/api/presets")
def list_presets(user: dict = User):
    """Your saved presets and the ones an admin shared with everyone."""
    return [{k: v for k, v in p.items() if k not in ("owner_id",)} for p in presets.visible_to(user)]


@router.post("/api/presets")
def save_preset(body: PresetIn, user: dict = User):
    try:
        out = presets.create(user, body.name, body.preset, body.filament, body.settings, body.shared, is_admin(user))
    except presets.PresetError as e:
        raise HTTPException(400, str(e))
    audit(user, "preset_save", f"{out['name']}" + (" (shared)" if out["scope"] == "shared" else ""))
    return {k: v for k, v in out.items() if k != "owner_id"}


@router.delete("/api/presets/{preset_id}")
def delete_preset(preset_id: str, user: dict = User):
    p = store.presets.get(preset_id)
    if not p or not (p["scope"] == "shared" or p["owner_id"] == user["id"]):
        raise HTTPException(404, "Unknown preset")
    if not presets.can_delete(user, p, is_admin(user)):
        raise HTTPException(403, "Only an admin can remove a shared preset")
    store.presets.delete(preset_id)
    audit(user, "preset_delete", p["name"])
    return {"ok": True}


@router.get("/api/goals/{goal_id}")
def goal(goal_id: str, filament: str = "PLA", user: dict = User):
    """The preset and settings a print goal means for a material."""
    try:
        return goals.resolve(goal_id, filament)
    except KeyError:
        raise HTTPException(404, "Unknown goal")
    except ValueError as e:
        raise HTTPException(400, str(e))
