"""Static catalogs: Bambu Lab printer models, filament types and the settings
schema the UI renders. Keeping the schema here makes it the single source of
truth for both the form and the OrcaSlicer key overrides."""
from typing import Any

# bed: (x, y, z) mm usable build volume. process_suffix is the "@BBL xxx" tag
# OrcaSlicer uses for process presets; machine is the machine preset name.
PRINTER_MODELS: dict[str, dict[str, Any]] = {
    "A1 mini": {"vendor": "Bambu Lab", "bed": (180, 180, 180), "enclosed": False, "ams": "ams-lite",
                "process_suffix": "A1M", "machine": "Bambu Lab A1 mini 0.4 nozzle", "max_nozzle_c": 300},
    "A1": {"vendor": "Bambu Lab", "bed": (256, 256, 256), "enclosed": False, "ams": "ams-lite",
           "process_suffix": "A1", "machine": "Bambu Lab A1 0.4 nozzle", "max_nozzle_c": 300},
    "P1P": {"vendor": "Bambu Lab", "bed": (256, 256, 256), "enclosed": False, "ams": "ams",
            "process_suffix": "X1C", "machine": "Bambu Lab P1P 0.4 nozzle", "max_nozzle_c": 300},
    "P1S": {"vendor": "Bambu Lab", "bed": (256, 256, 256), "enclosed": True, "ams": "ams",
            "process_suffix": "X1C", "machine": "Bambu Lab P1S 0.4 nozzle", "max_nozzle_c": 300},
    "X1C": {"vendor": "Bambu Lab", "bed": (256, 256, 256), "enclosed": True, "ams": "ams",
            "process_suffix": "X1C", "machine": "Bambu Lab X1 Carbon 0.4 nozzle", "max_nozzle_c": 300},
    "X1E": {"vendor": "Bambu Lab", "bed": (256, 256, 256), "enclosed": True, "ams": "ams",
            "process_suffix": "X1C", "machine": "Bambu Lab X1E 0.4 nozzle", "max_nozzle_c": 320},
    "H2D": {"vendor": "Bambu Lab", "bed": (325, 320, 325), "enclosed": True, "ams": "ams",
            "process_suffix": "H2D", "machine": "Bambu Lab H2D 0.4 nozzle", "max_nozzle_c": 350},
}

# enclosed=True means the material needs an enclosure (warps/fumes otherwise).
FILAMENTS: dict[str, dict[str, Any]] = {
    "PLA": {"orca": "Generic PLA", "nozzle": 220, "bed": 55, "enclosed": False},
    "PLA-CF": {"orca": "Generic PLA-CF", "nozzle": 230, "bed": 55, "enclosed": False, "hardened": True},
    "PETG": {"orca": "Generic PETG", "nozzle": 250, "bed": 70, "enclosed": False},
    "TPU": {"orca": "Generic TPU", "nozzle": 230, "bed": 35, "enclosed": False, "external_spool": True},
    "ABS": {"orca": "Generic ABS", "nozzle": 270, "bed": 100, "enclosed": True},
    "ASA": {"orca": "Generic ASA", "nozzle": 270, "bed": 100, "enclosed": True},
    "PC": {"orca": "Generic PC", "nozzle": 270, "bed": 100, "enclosed": True},
    "PA": {"orca": "Generic PA", "nozzle": 280, "bed": 100, "enclosed": True},
    "PA-CF": {"orca": "Generic PA-CF", "nozzle": 290, "bed": 100, "enclosed": True, "hardened": True},
}

# Bambu AMS "tray_type" values mapped onto our filament keys.
AMS_TYPE_ALIASES = {"PLA": "PLA", "PLA-CF": "PLA-CF", "PETG": "PETG", "TPU": "TPU", "ABS": "ABS",
                    "ASA": "ASA", "PC": "PC", "PA": "PA", "PA-CF": "PA-CF", "PA6-CF": "PA-CF"}

# Build plates: slicer name (curr_bed_type) -> value the printer's MQTT start command uses.
BED_TYPES: dict[str, str] = {
    "Textured PEI Plate": "textured_plate",
    "Cool Plate": "cool_plate",
    "Engineering Plate": "eng_plate",
    "High Temp Plate": "hot_plate",
}
DEFAULT_BED_TYPE = "Textured PEI Plate"

PROCESS_PRESETS = [
    {"id": "0.08mm Extra Fine", "layer_height": 0.08},
    {"id": "0.12mm Fine", "layer_height": 0.12},
    {"id": "0.16mm Optimal", "layer_height": 0.16},
    {"id": "0.20mm Standard", "layer_height": 0.20},
    {"id": "0.20mm Strength", "layer_height": 0.20},
    {"id": "0.24mm Draft", "layer_height": 0.24},
    {"id": "0.28mm Extra Draft", "layer_height": 0.28},
]


def _f(key, label, unit=None, kind="number", options=None, level="simple", step=None):
    return {"key": key, "label": label, "unit": unit, "type": kind, "options": options,
            "level": level, "step": step}


# level: "basic" settings show in every mode, "simple" in Simple+Advanced, "advanced" only in Advanced.
SETTINGS_SCHEMA: list[dict[str, Any]] = [
    {"tab": "Quality", "groups": [
        {"name": "Layer height", "fields": [
            _f("layer_height", "Layer height", "mm", level="basic", step=0.02),
            _f("initial_layer_print_height", "First layer height", "mm", level="advanced", step=0.02)]},
        {"name": "Line width", "fields": [
            _f("line_width", "Default", "mm", level="advanced", step=0.01),
            _f("outer_wall_line_width", "Outer wall", "mm", level="advanced", step=0.01),
            _f("inner_wall_line_width", "Inner wall", "mm", level="advanced", step=0.01)]},
        {"name": "Seam", "fields": [
            _f("seam_position", "Seam position", None, "select",
               ["nearest", "aligned", "back", "random"], level="simple")]},
        {"name": "Precision", "fields": [
            _f("resolution", "Slice gap closing / resolution", "mm", level="advanced", step=0.001),
            _f("ironing_type", "Ironing type", None, "select",
               ["no ironing", "top", "topmost", "solid"], level="simple")]},
    ]},
    {"tab": "Strength", "groups": [
        {"name": "Walls", "fields": [
            _f("wall_loops", "Wall loops", None, level="basic", step=1),
            _f("detect_thin_wall", "Detect thin wall", None, "bool", level="advanced")]},
        {"name": "Top/bottom shells", "fields": [
            _f("top_shell_layers", "Top shell layers", None, level="simple", step=1),
            _f("bottom_shell_layers", "Bottom shell layers", None, level="simple", step=1)]},
        {"name": "Sparse infill", "fields": [
            _f("sparse_infill_density", "Sparse infill density", "%", level="basic", step=1),
            _f("sparse_infill_pattern", "Sparse infill pattern", None, "select",
               ["grid", "gyroid", "cubic", "honeycomb", "adaptivecubic", "lightning", "line",
                "triangles", "3dhoneycomb"], level="simple")]},
    ]},
    {"tab": "Speed", "groups": [
        {"name": "Print speed", "fields": [
            _f("initial_layer_speed", "First layer", "mm/s", level="simple", step=5),
            _f("outer_wall_speed", "Outer wall", "mm/s", level="simple", step=5),
            _f("inner_wall_speed", "Inner wall", "mm/s", level="simple", step=5),
            _f("sparse_infill_speed", "Sparse infill", "mm/s", level="simple", step=5),
            _f("top_surface_speed", "Top surface", "mm/s", level="advanced", step=5),
            _f("travel_speed", "Travel", "mm/s", level="advanced", step=5)]},
        {"name": "Acceleration", "fields": [
            _f("default_acceleration", "Normal printing", "mm/s²", level="advanced", step=100),
            _f("outer_wall_acceleration", "Outer wall", "mm/s²", level="advanced", step=100)]},
    ]},
    {"tab": "Support", "groups": [
        {"name": "Support", "fields": [
            _f("enable_support", "Enable support", None, "bool", level="basic"),
            _f("support_type", "Type", None, "select",
               ["normal(auto)", "tree(auto)", "normal(manual)", "tree(manual)"], level="simple"),
            _f("support_threshold_angle", "Threshold angle", "°", level="simple", step=1),
            _f("support_on_build_plate_only", "On build plate only", None, "bool", level="simple")]},
        {"name": "Raft", "fields": [
            _f("raft_layers", "Raft layers", None, level="advanced", step=1)]},
        {"name": "Bed adhesion", "fields": [
            _f("brim_type", "Brim type", None, "select",
               ["auto_brim", "outer_only", "inner_only", "outer_and_inner", "no_brim"], level="basic"),
            _f("brim_width", "Brim width", "mm", level="advanced", step=0.5),
            _f("skirt_loops", "Skirt loops", None, level="advanced", step=1)]},
    ]},
    {"tab": "Multimaterial", "groups": [
        {"name": "Prime tower", "fields": [
            _f("enable_prime_tower", "Enable prime tower", None, "bool", level="simple"),
            _f("prime_tower_width", "Width", "mm", level="advanced", step=1)]},
        {"name": "Flush", "fields": [
            _f("flush_multiplier", "Flush volume multiplier", None, level="advanced", step=0.1)]},
    ]},
    {"tab": "Others", "groups": [
        {"name": "Special mode", "fields": [
            _f("print_sequence", "Print sequence", None, "select", ["by layer", "by object"], level="advanced"),
            _f("spiral_mode", "Spiral vase", None, "bool", level="advanced")]},
        {"name": "Fuzzy skin", "fields": [
            _f("fuzzy_skin", "Fuzzy skin", None, "select", ["none", "external", "all"], level="advanced")]},
    ]},
]

# Defaults used for the UI when no preset values have been resolved yet.
SETTING_DEFAULTS: dict[str, Any] = {
    "layer_height": 0.2, "initial_layer_print_height": 0.2, "line_width": 0.42,
    "outer_wall_line_width": 0.42, "inner_wall_line_width": 0.45, "seam_position": "aligned",
    "resolution": 0.012, "ironing_type": "no ironing", "wall_loops": 2, "detect_thin_wall": False,
    "top_shell_layers": 5, "bottom_shell_layers": 3, "sparse_infill_density": 15,
    "sparse_infill_pattern": "grid", "initial_layer_speed": 50, "outer_wall_speed": 200,
    "inner_wall_speed": 300, "sparse_infill_speed": 270, "top_surface_speed": 200, "travel_speed": 500,
    "default_acceleration": 10000, "outer_wall_acceleration": 5000, "enable_support": False,
    "support_type": "tree(auto)", "support_threshold_angle": 30, "support_on_build_plate_only": False,
    "raft_layers": 0, "curr_bed_type": "Textured PEI Plate", "brim_type": "auto_brim", "brim_width": 5, "skirt_loops": 0,
    "enable_prime_tower": True, "prime_tower_width": 35, "flush_multiplier": 1.0,
    "print_sequence": "by layer", "spiral_mode": False, "fuzzy_skin": "none",
}

# Strength preset tweaks applied on top of the default values for the UI.
PRESET_OVERRIDES = {
    "0.20mm Strength": {"wall_loops": 6, "sparse_infill_density": 25},
}

ALLOWED_SETTING_KEYS = {f["key"] for t in SETTINGS_SCHEMA for g in t["groups"] for f in g["fields"]}


def preset_defaults(preset_id: str) -> dict[str, Any]:
    values = dict(SETTING_DEFAULTS)
    for p in PROCESS_PRESETS:
        if p["id"] == preset_id:
            values["layer_height"] = p["layer_height"]
    values.update(PRESET_OVERRIDES.get(preset_id, {}))
    return values
