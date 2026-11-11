"""Print goals (Milestone 1.3): "Fast draft", "Standard", "Strong", "Fine detail". One click chooses a process preset and a few
settings, adjusted for the material, so a beginner does not have to know which of the 500 settings matter. The result is
ordinary settings: everything stays editable afterwards."""
from typing import Any

from .catalog import ALLOWED_SETTING_KEYS, DEFAULT_NOZZLE, FILAMENTS, PROCESS_PRESETS
from .defaults import ui_defaults

# `presets`: the process preset for each nozzle size (a preset names its own nozzle, see catalog.py).
GOALS: list[dict[str, Any]] = [
    {"id": "draft", "name": "Fast draft", "presets": {0.4: "0.28mm Extra Draft", 0.2: "0.14mm Standard @0.2", 0.6: "0.42mm Standard @0.6", 0.8: "0.56mm Standard @0.8"},
     "description": "Thick layers and light infill: quick and rough, for checking fit and shape.",
     "settings": {"sparse_infill_density": 10}},
    {"id": "standard", "name": "Standard", "presets": {0.4: "0.20mm Standard", 0.2: "0.10mm Standard @0.2", 0.6: "0.30mm Standard @0.6", 0.8: "0.40mm Standard @0.8"},
     "description": "A good balance of quality and speed for most parts.", "settings": {}},
    {"id": "strong", "name": "Strong", "presets": {0.4: "0.20mm Strength", 0.2: "0.12mm Standard @0.2", 0.6: "0.30mm Strength @0.6", 0.8: "0.40mm Standard @0.8"},
     "description": "More walls and denser infill for parts that carry load.",
     "settings": {"sparse_infill_density": 35, "top_shell_layers": 6, "bottom_shell_layers": 5},
     "extra_for_nozzles_without_strength_preset": {"wall_loops": 6}},
    {"id": "detail", "name": "Fine detail", "presets": {0.4: "0.12mm Fine", 0.2: "0.06mm Standard @0.2", 0.6: "0.18mm Standard @0.6", 0.8: "0.24mm Standard @0.8"},
     "description": "Thin layers and slower outer walls for small features and smooth surfaces.",
     "settings": {"outer_wall_speed": 100, "top_surface_speed": 100}},
]

# Slow, flexible filament cannot be pushed fast: these are ceilings, so a goal that is already slower keeps its own value.
TPU_SPEED_CAPS = {"initial_layer_speed": 20, "outer_wall_speed": 30, "inner_wall_speed": 40, "sparse_infill_speed": 40, "top_surface_speed": 30}
WARP_BRIM = {"brim_type": "outer_only", "brim_width": 5}


def public() -> list[dict[str, Any]]:
    return [{"id": g["id"], "name": g["name"], "description": g["description"], "presets": {f"{n:g}": p for n, p in g["presets"].items()}} for g in GOALS]


def resolve(goal_id: str, filament: str, nozzle: float = DEFAULT_NOZZLE) -> dict[str, Any]:
    """The process preset and the full settings panel values for a goal and material, plus what was adjusted for the material."""
    goal = next((g for g in GOALS if g["id"] == goal_id), None)
    if goal is None:
        raise KeyError(goal_id)
    if filament not in FILAMENTS:
        raise ValueError("Unknown filament")
    preset = goal["presets"].get(nozzle)
    if preset is None:
        raise ValueError(f"No {nozzle:g} mm nozzle preset for this goal")
    if preset not in {p["id"] for p in PROCESS_PRESETS}:
        raise ValueError(f"Unknown process preset {preset}")
    settings = {**ui_defaults(preset), **goal["settings"], **(goal.get("extra_for_nozzles_without_strength_preset", {}) if "Strength" not in preset else {})}
    notes: list[str] = []
    if filament == "TPU":
        for k, cap in TPU_SPEED_CAPS.items():
            settings[k] = min(settings.get(k, cap), cap)
        notes.append("Slowed down for flexible filament.")
    if FILAMENTS[filament].get("enclosed"):
        settings.update(WARP_BRIM)
        notes.append("Brim added to keep the part from warping off the plate.")
    assert set(settings) <= ALLOWED_SETTING_KEYS
    return {"goal": goal_id, "preset": preset, "settings": settings, "notes": notes}
