"""Applying a status report from a printer (wherever it was read from) to the stored printer record."""
import time
from typing import Any

from . import bedstate, outcomes, store


def apply(printer_id: str, update: dict[str, Any]) -> None:
    p = store.printers.get(printer_id)
    if not p:
        return
    prev_state = (p.get("status") or {}).get("state")
    status = dict(p.get("status", {}))
    status.update(update)
    status["updated"] = time.time()
    store.printers.update(printer_id, status=status)
    bedstate.on_status(printer_id, prev_state, status)  # a finished print leaves the plate occupied until someone confirms it is clear
    outcomes.on_status(printer_id, status)  # records the result of a print this app started, once the printer reports it
