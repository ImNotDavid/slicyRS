"""Choose which printer a part should be sliced for.

Hard constraints (part must fit, material compatible, printer reachable) decide
eligibility; a score then ranks eligible printers so that idle printers with
the filament already loaded win, and small parts don't hog large printers.
"""
from dataclasses import dataclass, field
from typing import Any

from . import plate_compat
from .catalog import DEFAULT_BED_TYPE, FILAMENTS, PRINTER_MODELS

BED_MARGIN_MM = 3.0
BUSY_STATES = {"RUNNING", "PREPARE", "PAUSE", "SLICING"}


@dataclass
class Candidate:
    printer_id: str
    name: str
    model: str
    eligible: bool
    score: float = 0.0
    reasons: list[str] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)
    loaded_slot: int | None = None

    def asdict(self) -> dict[str, Any]:
        return self.__dict__.copy()


def fits(bed: tuple[float, float, float], size: tuple[float, float, float]) -> bool:
    bx, by = (b - 2 * BED_MARGIN_MM for b in bed[:2])
    bz = bed[2]
    x, y, z = size
    return z <= bz and ((x <= bx and y <= by) or (y <= bx and x <= by))


def evaluate(printer: dict[str, Any], size: tuple[float, float, float], filament: str) -> Candidate:
    model = PRINTER_MODELS.get(printer["model"])
    c = Candidate(printer["id"], printer["name"], printer["model"], True)
    if model is None:
        c.eligible = False
        c.blockers.append(f"Unknown model {printer['model']}")
        return c

    status = printer.get("status", {})
    state = status.get("state", "OFFLINE")
    fil = FILAMENTS.get(filament)
    if fil is None:
        c.eligible = False
        c.blockers.append(f"Unknown filament {filament}")
        return c

    if not fits(model["bed"], size):
        c.eligible = False
        c.blockers.append(
            f"Part {size[0]:.0f}×{size[1]:.0f}×{size[2]:.0f} mm exceeds "
            f"{model['bed'][0]}×{model['bed'][1]}×{model['bed'][2]} mm build volume")
    if fil["enclosed"] and not model["enclosed"]:
        c.eligible = False
        c.blockers.append(f"{filament} needs an enclosed printer")
    if fil.get("hardened") and not printer.get("hardened_nozzle"):
        c.eligible = False
        c.blockers.append(f"{filament} needs a hardened nozzle")
    plate = printer.get("bed_type") or DEFAULT_BED_TYPE
    why_not = plate_compat.explain(printer["model"], filament, plate)
    if why_not:
        c.eligible = False
        c.blockers.append(why_not)
    if fil["nozzle"] > model["max_nozzle_c"]:
        c.eligible = False
        c.blockers.append(f"Nozzle max {model['max_nozzle_c']}°C is below {fil['nozzle']}°C")
    if state == "OFFLINE" and not printer.get("assume_available"):
        c.eligible = False
        c.blockers.append("Printer is offline")
    if not c.eligible:
        return c

    # Everything below only ranks eligible printers.
    score = 100.0
    if state in BUSY_STATES:
        score -= 60
        c.reasons.append("Currently printing (job will queue)")
    elif state == "ERROR" or state == "FAILED":
        score -= 60
        c.reasons.append(f"Printer reports {state.lower()}")
    else:
        c.reasons.append("Idle and ready")

    score -= 8 * printer.get("queue_depth", 0)

    slot = _match_loaded(status.get("ams", []), filament)
    if slot is not None:
        score += 30
        c.loaded_slot = slot
        c.reasons.append(f"{filament} already loaded in AMS slot {slot + 1}")
    elif fil.get("external_spool"):
        c.reasons.append("Load on external spool")
    else:
        score -= 10
        c.reasons.append(f"{filament} not loaded — needs a filament change")

    # Prefer the smallest bed that fits so large printers stay free for large parts.
    volume = model["bed"][0] * model["bed"][1] * model["bed"][2]
    score -= volume / 1_000_000  # ~16.8 for 256³, ~34 for H2D
    if volume > 17_000_000:
        c.reasons.append("Large-format printer (only used when needed or alone)")

    c.score = round(score, 2)
    return c


def _match_loaded(ams: list[dict[str, Any]], filament: str) -> int | None:
    for t in ams:
        if t.get("type") == filament and t.get("remain", 100) > 5:
            return t["slot"]
    return None


def rank(printers: list[dict[str, Any]], size: tuple[float, float, float], filament: str) -> list[Candidate]:
    cands = [evaluate(p, size, filament) for p in printers]
    return sorted(cands, key=lambda c: (not c.eligible, -c.score))


def choose(printers: list[dict[str, Any]], size: tuple[float, float, float], filament: str) -> Candidate | None:
    ranked = rank(printers, size, filament)
    return ranked[0] if ranked and ranked[0].eligible else None
