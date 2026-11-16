"""Which filaments may print on which build plate, so printers whose fitted plate is wrong are never chosen.

The slicer refuses a filament on a plate when the filament profile has no bed temperature for that plate (value 0), so the
answer comes from the real profile of the printer model when profiles are installed. Without profiles (mock mode, tests) a
fixed table is used; a test checks the table against the real profiles whenever a slicer is available.
"""
from functools import lru_cache

from . import profiles
from .catalog import BED_TYPES, FILAMENTS, PRINTER_MODELS
from .config import SLICER_MODE

# slicer profile key holding the bed temperature for each plate
_TEMP_KEY = {"Cool Plate": "cool_plate_temp", "Engineering Plate": "eng_plate_temp",
             "High Temp Plate": "hot_plate_temp", "Textured PEI Plate": "textured_plate_temp"}

# Filaments refused per plate (bed temperature 0 in Bambu Studio 02.08 and 02.09 profiles), and per-model exceptions.
STATIC_INCOMPATIBLE: dict[str, set[str]] = {
    "Cool Plate": {"PETG", "ABS", "ASA", "PC", "PA", "PA-CF"},
    "Engineering Plate": {"PLA", "PLA-CF"},
    "High Temp Plate": set(),
    "Textured PEI Plate": set(),
}
STATIC_EXCEPTIONS: dict[tuple[str, str], set[str]] = {("H2D", "Engineering Plate"): set(), ("P2S", "Engineering Plate"): set()}


@lru_cache(maxsize=512)
def _profile_says(model: str, filament: str, bed_type: str) -> bool | None:
    """True/False from the printer model's filament profile, or None when profiles are not available."""
    if SLICER_MODE != "orca":
        return None
    try:
        suffix = PRINTER_MODELS[model]["process_suffix"]
        name = profiles.filament_name(FILAMENTS[filament]["orca"], suffix)
        if not name:
            return None
        raw = profiles.resolve(name)[_TEMP_KEY[bed_type]]
        return int(float(raw[0] if isinstance(raw, list) else raw)) > 0
    except (KeyError, ValueError, OSError, IndexError):
        return None


def compatible(model: str, filament: str, bed_type: str) -> bool:
    """May `filament` be printed on `bed_type` fitted to a printer of this model?"""
    if bed_type not in BED_TYPES or filament not in FILAMENTS:
        return True  # unknown names are reported elsewhere; do not block twice
    from_profile = _profile_says(model, filament, bed_type)
    if from_profile is not None:
        return from_profile
    refused = STATIC_EXCEPTIONS.get((model, bed_type), STATIC_INCOMPATIBLE.get(bed_type, set()))
    return filament not in refused


def explain(model: str, filament: str, bed_type: str) -> str | None:
    return None if compatible(model, filament, bed_type) else f"{filament} cannot be printed on the {bed_type} fitted to this printer"
