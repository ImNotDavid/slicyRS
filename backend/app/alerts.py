"""Mid-print alerts (2.16): a print that is running pauses, reports an error, or its printer drops off the network.

Called for every status report. Each event is sent once: a pause or a lost connection when the printer *enters* that state, an
error code the first time it appears for the print. The people told are the owners on the plate and the operators.
"""
from typing import Any

from . import bedstate, notify, store


def _running_jobs(printer_id: str) -> list[dict[str, Any]]:
    return [j for j in bedstate.last_plate(printer_id) if j.get("phase") == "printing"]


def _owners(jobs: list[dict[str, Any]]) -> list[str]:
    return [j["owner_email"] for j in jobs if j.get("owner_email")]


def _alert(kind: str, p: dict[str, Any], jobs: list[dict[str, Any]], what: str, detail: str = "") -> None:
    names = ", ".join(j["name"] for j in jobs)
    who = ", ".join(sorted(set(_owners(jobs))))
    notify.emit(kind, f"{p['name']}: {what}",
                f"{what.capitalize()} while printing {names}.{(' ' + detail) if detail else ''}\n\nGo to the printer and check it.",
                owners=_owners(jobs), link="/#/fleet", job=jobs[0],
                operator_body=f"{p['name']}: {what} ({who}, {names}).{(' ' + detail) if detail else ''}")


def on_status(printer_id: str, prev_state: str | None, status: dict[str, Any]) -> None:
    jobs = _running_jobs(printer_id)
    if not jobs:
        return
    p = store.printers.get(printer_id) or {"name": printer_id}
    state = status.get("state")
    if state == "PAUSE" and prev_state != "PAUSE":
        layer = status.get("layer_num")
        _alert("print_paused", p, jobs, "the print was paused", f"It stopped at layer {layer}." if layer is not None else "")
    elif state == "OFFLINE" and prev_state not in (None, "OFFLINE"):
        _alert("printer_lost", p, jobs, "the printer went offline", "It may still be printing; check the printer.")
    # printer error codes raised since the print started, each reported once per job
    started = min((j.get("started_at") or 0) for j in jobs)
    from .outcomes import describe_hms   # local import: outcomes imports this package's modules at load time
    new: list[dict[str, str]] = []
    seen = set(jobs[0].get("alerted_hms") or [])
    for e in status.get("hms", []) or []:
        if int(e.get("timestamp", 0)) < started - 60:
            continue
        d = describe_hms(e)
        if d["code"] not in seen:
            seen.add(d["code"])
            new.append(d)
    if new:
        for j in jobs:
            store.jobs.update(j["id"], alerted_hms=sorted(seen))
        _alert("print_error", p, jobs, "the printer reported an error", " ".join(d["text"] for d in new))
