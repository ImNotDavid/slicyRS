"""Static catalogs: Bambu Lab printer models, filament types and the settings
schema the UI renders. Keeping the schema here makes it the single source of
truth for both the form and the OrcaSlicer key overrides."""
import json
import re
from pathlib import Path
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

NOZZLES = (0.2, 0.4, 0.6, 0.8)    # mm; the sizes Bambu Lab ships profiles for
DEFAULT_NOZZLE = 0.4


def _presets() -> list[dict[str, Any]]:
    """Process presets by nozzle. A preset names its own nozzle, so choosing one also chooses which printers can print it. The 0.4 mm
    ones keep their original ids; the others carry "@<nozzle>". The slicer's own profile names differ by printer model for the same
    layer height ("Optimal", "Standard", ...), so the slicer profile is found by layer height (profiles.process_name)."""
    out = [{"id": i, "layer_height": h, "nozzle": 0.4, "label": i} for i, h in [
        ("0.08mm Extra Fine", 0.08), ("0.12mm Fine", 0.12), ("0.16mm Optimal", 0.16), ("0.20mm Standard", 0.20),
        ("0.20mm Strength", 0.20), ("0.24mm Draft", 0.24), ("0.28mm Extra Draft", 0.28)]]
    for nozzle, rows in ((0.2, [(0.06, "Standard"), (0.08, "Standard"), (0.10, "Standard"), (0.12, "Standard"), (0.14, "Standard")]),
                         (0.6, [(0.18, "Standard"), (0.24, "Standard"), (0.30, "Standard"), (0.30, "Strength"), (0.36, "Standard"), (0.42, "Standard")]),
                         (0.8, [(0.24, "Standard"), (0.32, "Standard"), (0.40, "Standard"), (0.48, "Standard"), (0.56, "Standard")])):
        for h, variant in rows:
            label = f"{h:.2f}mm {variant}"
            out.append({"id": f"{label} @{nozzle:g}", "layer_height": h, "nozzle": nozzle, "label": label})
    return out


PROCESS_PRESETS = _presets()


def preset_nozzle(preset_id: str | None) -> float:
    return next((p["nozzle"] for p in PROCESS_PRESETS if p["id"] == preset_id), DEFAULT_NOZZLE)


def job_position(job: dict[str, Any]) -> tuple[float, float]:
    """Where on the bed the job's part was placed by hand, as an offset (mm) from the bed centre. (0, 0) means "let the slicer place it"."""
    p = (job.get("spec") or {}).get("position") or (0.0, 0.0)
    return float(p[0]), float(p[1])


def job_nozzle(job: dict[str, Any]) -> float:
    """The nozzle size a job needs, from its process preset (jobs made before nozzle sizes existed are 0.4 mm)."""
    return preset_nozzle((job.get("spec") or {}).get("preset") or job.get("preset"))


# The settings panel's pages, groups, order, labels, units, ranges and choices come from Bambu Studio itself: settings_schema.json is
# generated from its source by scripts/gen_settings_schema.py (see that file). "basic" below is our own short list for beginners.
BASIC_KEYS = {"layer_height", "wall_loops", "sparse_infill_density", "enable_support", "brim_type"}


def _load_schema() -> list[dict[str, Any]]:
    raw = json.loads((Path(__file__).with_name("settings_schema.json")).read_text(encoding="utf-8"))
    for tab in raw["tabs"]:
        for group in tab["groups"]:
            for f in group["fields"]:
                if f["key"] in BASIC_KEYS:
                    f["level"] = "basic"       # level: "basic" shows in every mode, "simple" in Simple and Advanced, "advanced" only in Advanced
    return raw["tabs"]


SETTINGS_SCHEMA: list[dict[str, Any]] = _load_schema()
FIELDS: dict[str, dict[str, Any]] = {f["key"]: f for t in SETTINGS_SCHEMA for g in t["groups"] for f in g["fields"]}


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
    "0.30mm Strength @0.6": {"wall_loops": 6, "sparse_infill_density": 25},
}

ALLOWED_SETTING_KEYS = set(FIELDS)


def _schema_defaults() -> dict[str, Any]:
    """A value for every setting when no real profile is installed (mock mode): Bambu Studio's own default where the schema has
    one, the first choice for a list, else empty. The hand-set values above win, because they track the printer profiles."""
    out: dict[str, Any] = {}
    for key, f in FIELDS.items():
        if "default" in f:
            out[key] = f["default"]
        elif f["type"] == "select":
            out[key] = f["options"][0]["value"]
        elif f["type"] == "bool":
            out[key] = False
        elif f["type"] in ("number", "percent"):
            out[key] = f.get("min", 0)
        elif f["type"] == "number_or_percent":
            out[key] = "0"
        else:
            out[key] = ""
    return out


def validate_settings(settings: dict[str, Any]) -> str | None:
    """A message for the first setting whose value cannot be right (wrong type, not one of the choices, out of range), else None.
    Only known settings are checked; unknown ones are rejected elsewhere."""
    for key, v in settings.items():
        f = FIELDS.get(key)
        if f is None:
            continue
        label, kind = f["label"], f["type"]
        if kind == "bool":
            ok = isinstance(v, bool)
        elif kind in ("number", "percent"):
            ok = isinstance(v, (int, float)) and not isinstance(v, bool) and v == v
            if ok and f.get("integer") and v != int(v):
                return f"{label} must be a whole number"
        elif kind == "number_or_percent":
            ok = (isinstance(v, (int, float)) and not isinstance(v, bool)) or (isinstance(v, str) and re.fullmatch(r"-?\d+(\.\d+)?%?", v) is not None)
            if ok and isinstance(v, str):
                v = float(v.rstrip("%"))
        elif kind == "select":
            ok = isinstance(v, str) and v in {o["value"] for o in f["options"]}
        else:
            ok = isinstance(v, str) and len(v) <= 200
        if not ok:
            return f"{label} has an invalid value"
        if kind in ("number", "percent", "number_or_percent") and isinstance(v, (int, float)):
            if "min" in f and v < f["min"]:
                return f"{label} must be at least {f['min']:g}"
            if "max" in f and v > f["max"]:
                return f"{label} must be at most {f['max']:g}"
    return None


def preset_defaults(preset_id: str) -> dict[str, Any]:
    values = {**_schema_defaults(), **SETTING_DEFAULTS}
    for p in PROCESS_PRESETS:
        if p["id"] == preset_id:
            values["layer_height"] = p["layer_height"]
    values.update(PRESET_OVERRIDES.get(preset_id, {}))
    return values
