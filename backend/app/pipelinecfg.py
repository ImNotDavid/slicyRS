"""Admin-set numbers for the job pipeline. Stored in the database; defaults here."""
from . import store

DEFAULTS = {
    "cooling_margin_min": 10,      # time added after a print ends before the parts can be handled
    "operator_delay_min": 30,      # a finished print waits this long for its owner before operators are told
    "ready_hold_min": 30,          # a sliced job not started by its owner goes back to the pool after this long
    "estimate_buffer_pct": 15,     # slack on the slicer's time estimate when judging a pickup time
    "max_hold_h": 24,              # longest a job may wait for batching partners (batching itself: A.6, not built yet)
}
LIMITS = {"cooling_margin_min": (0, 240), "operator_delay_min": (0, 1440), "ready_hold_min": (1, 1440),
          "estimate_buffer_pct": (0, 100), "max_hold_h": (1, 168)}


def get() -> dict[str, int]:
    saved = store.settings.get("pipeline") or {}
    return {k: int(saved.get(k, v)) for k, v in DEFAULTS.items()}


def put(values: dict) -> dict[str, int]:
    merged = {**get(), **{k: int(v) for k, v in values.items() if k in DEFAULTS}}
    for k, v in merged.items():
        lo, hi = LIMITS[k]
        if not lo <= v <= hi:
            raise ValueError(f"{k} must be between {lo} and {hi}")
    store.settings.put("pipeline", merged)
    return merged
