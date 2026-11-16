"""Choose which printer a part should be sliced for.

Hard constraints (part must fit, material compatible, printer reachable) decide
eligibility; a score then ranks eligible printers so that idle printers with
the filament already loaded win, and small parts don't hog large printers.
"""
from dataclasses import dataclass, field
from typing import Any

from . import plate_compat
from . import profiles
from .catalog import DEFAULT_BED_TYPE, DEFAULT_NOZZLE, FILAMENTS, PRINTER_MODELS
from .config import SLICER_MODE

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


def fits(bed: tuple[float, float, float], size: tuple[float, float, float], offset: tuple[float, float] = (0.0, 0.0)) -> bool:
    """Does the part fit the build volume? With an offset (a part placed by hand) the footprint must stay on the plate where it
    was put and cannot be turned; without one the slicer may turn it to fit."""
    if any(offset):
        x, y, z = size
        cx, cy = bed[0] / 2 + offset[0], bed[1] / 2 + offset[1]
        m = BED_MARGIN_MM
        return z <= bed[2] and cx - x / 2 >= m and cx + x / 2 <= bed[0] - m and cy - y / 2 >= m and cy + y / 2 <= bed[1] - m
    bx, by = (b - 2 * BED_MARGIN_MM for b in bed[:2])
    bz = bed[2]
    x, y, z = size
    return z <= bz and ((x <= bx and y <= by) or (y <= bx and x <= by))


def nozzle_of(printer: dict[str, Any]) -> float:
    """The nozzle size fitted to a printer: what a connected printer reports, else what an admin set, else 0.4 mm."""
    return float((printer.get("status") or {}).get("nozzle_mm") or printer.get("nozzle_mm") or DEFAULT_NOZZLE)


def evaluate(printer: dict[str, Any], size: tuple[float, float, float], filament: str, nozzle: float = DEFAULT_NOZZLE,
             offset: tuple[float, float] = (0.0, 0.0), colour: str | None = None) -> Candidate:
    """`colour` (a hex the person chose from what is loaded) must be loaded on the printer right now; with `assume_available`
    (could it ever print this?) a colour that is not loaded is only a reason to wait, not a blocker."""
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

    fitted = nozzle_of(printer)
    if abs(fitted - nozzle) > 1e-6:
        c.eligible = False
        c.blockers.append(f"This job needs a {nozzle:g} mm nozzle; this printer has a {fitted:g} mm one")
    elif SLICER_MODE == "orca" and not profiles.machine_name(model["machine"], nozzle):
        c.eligible = False
        c.blockers.append(f"The slicer has no profile for this printer with a {nozzle:g} mm nozzle")
    if not fits(model["bed"], size, offset):
        c.eligible = False
        if any(offset) and fits(model["bed"], size):
            c.blockers.append(f"At the position you chose the part would hang off this printer's {model['bed'][0]}×{model['bed'][1]} mm plate")
        else:
            c.blockers.append(f"Part {size[0]:.0f}×{size[1]:.0f}×{size[2]:.0f} mm exceeds "
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
    from .upkeep import unavailable          # maintenance or "needs attention": out of the pool for now, not for ever
    if (why_out := unavailable(printer)) and not printer.get("assume_available"):
        c.eligible = False
        c.blockers.append(why_out)
    want = colour if colour and colour != "any" else None
    slot = loaded_slot(status, filament, want)
    if want and slot is None and not printer.get("assume_available"):
        c.eligible = False
        c.blockers.append(f"{filament} in {want} is not loaded")
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

    if slot is not None:
        score += 30
        c.loaded_slot = slot
        c.reasons.append(f"{filament} already loaded " + ("on the external spool" if slot == EXTERNAL_SLOT else f"in AMS slot {slot + 1}"))
    elif not status.get("ams") and not fil.get("external_spool"):
        # No AMS (e.g. A1 mini): the printer reports nothing about the external spool, so we cannot tell what is loaded.
        c.reasons.append(f"No AMS — uses the external spool; {filament} must be loaded there")
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


EXTERNAL_SLOT = 254     # the external spool holder (same id as bambulan.EXTERNAL_SLOT)


def trays(status: dict[str, Any]) -> list[dict[str, Any]]:
    """What a printer reports as loaded: its AMS trays, then its external spool. Empty trays and nearly empty spools are left
    out; a spool without an RFID chip reports remain -1 (unknown), which counts as loaded."""
    out = [*(status.get("ams") or []), *([status["spool"]] if isinstance(status.get("spool"), dict) else [])]
    return [t for t in out if t.get("type") and not (0 <= (t.get("remain") if t.get("remain") is not None else 100) <= 5)]


def same_colour(a: str | None, b: str | None) -> bool:
    return (a or "").lower()[:7] == (b or "").lower()[:7]


def loaded_slot(status: dict[str, Any], filament: str, colour: str | None = None) -> int | None:
    """The slot holding this filament (and colour, if given), AMS before the external spool, or None."""
    for t in trays(status):
        if t["type"] == filament and (colour is None or same_colour(t.get("color"), colour)):
            return t["slot"]
    return None


def _match_loaded(ams: list[dict[str, Any]], filament: str) -> int | None:      # kept for older callers
    return loaded_slot({"ams": ams}, filament)


def rank(printers: list[dict[str, Any]], size: tuple[float, float, float], filament: str, nozzle: float = DEFAULT_NOZZLE,
         offset: tuple[float, float] = (0.0, 0.0), colour: str | None = None) -> list[Candidate]:
    cands = [evaluate(p, size, filament, nozzle, offset, colour) for p in printers]
    return sorted(cands, key=lambda c: (not c.eligible, -c.score))


def choose(printers: list[dict[str, Any]], size: tuple[float, float, float], filament: str, nozzle: float = DEFAULT_NOZZLE,
           offset: tuple[float, float] = (0.0, 0.0)) -> Candidate | None:
    ranked = rank(printers, size, filament, nozzle, offset)
    return ranked[0] if ranked and ranked[0].eligible else None
