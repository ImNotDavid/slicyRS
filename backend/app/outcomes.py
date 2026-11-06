"""Record what a printer reported when a print it was given ended, so a failed print explains itself.

Called for every status update of a printer. When the printer reports FINISH or FAILED for a print this app started
(matched by the job's remote file name), the result is stored once on the job and written to the audit log.
"""
import time
from typing import Any

from . import store

FINAL = {"FINISH": "finished", "FAILED": "failed"}
HMS_LIST_URL = "https://wiki.bambulab.com/en/hms/error-code"

# Only codes whose meaning is confirmed. Anything else is shown with its code and a pointer to Bambu's list, never a guess.
KNOWN_HMS = {
    "0500-0500-0001-0007": "MQTT command verification failed: the printer only accepts print commands from authorized software "
                           "unless Developer Mode is on (printer screen: Settings, Network, LAN Only Mode, then Developer Mode).",
}


def hms_code(entry: dict[str, Any]) -> str:
    a, c = int(entry.get("attr", 0)), int(entry.get("code", 0))
    return "%04X-%04X-%04X-%04X" % ((a >> 16) & 0xFFFF, a & 0xFFFF, (c >> 16) & 0xFFFF, c & 0xFFFF)


def describe_hms(entry: dict[str, Any]) -> dict[str, str]:
    code = hms_code(entry)
    return {"code": code, "text": KNOWN_HMS.get(code, f"Printer error {code}. Look the code up in Bambu's error list: {HMS_LIST_URL}")}


def _fmt(seconds: float) -> str:
    seconds = int(seconds)
    h, m = seconds // 3600, (seconds % 3600) // 60
    return f"{h}h {m:02d}m" if h else f"{m}m"


def _summary(outcome: str, job: dict[str, Any], r: dict[str, Any]) -> str:
    est = (job.get("stats") or {}).get("time_s")
    if outcome == "finished":
        s = f"Finished in {_fmt(r['duration_s'])}"
        return s + (f" (estimate {_fmt(est)})." if est else ".")
    where = ""
    if r.get("layer") is not None and r.get("total_layers"):
        where = f" at layer {r['layer']} of {r['total_layers']}"
    if r["hms"]:
        return f"The printer stopped{where} and reported: " + " ".join(h["text"] for h in r["hms"])
    if r.get("print_error"):
        return (f"The printer stopped{where} with error number {r['print_error']}. "
                f"Look it up in Bambu's error list: {HMS_LIST_URL}")
    s = (f"The printer stopped{where} without reporting an error code. That usually means the print was cancelled at the printer, "
         "or the printer's own detection (for example the spaghetti detector) stopped it. Check the printer's screen.")
    if r.get("layer") is not None and r["layer"] <= 3:
        s += (" Stopping in the first layers usually means the part did not stick: check the plate is clean, that the build plate "
              "setting matches the plate fitted, and the printer's Z offset and bed calibration.")
    return s


def _find_job(printer_id: str, subtask_name: str) -> dict[str, Any] | None:
    for j in store.jobs.all():
        remote = (j.get("remote_name") or "").removesuffix(".gcode.3mf")
        if j.get("printer_id") == printer_id and j.get("printed") and not j.get("result") and remote and remote == subtask_name:
            return j
    return None


def on_status(printer_id: str, status: dict[str, Any]) -> None:
    outcome = FINAL.get(status.get("state", ""))
    name = status.get("subtask_name")
    if not outcome or not name:
        return
    job = _find_job(printer_id, name)
    if job is None:
        return
    started = job.get("started_at") or 0
    now = time.time()
    # only error entries raised since this print was started (older ones belong to earlier events)
    hms = [describe_hms(e) for e in status.get("hms", []) if int(e.get("timestamp", 0)) >= started - 60]
    result = {
        "outcome": outcome, "printer_state": status.get("state"), "print_error": int(status.get("print_error") or 0),
        "hms": hms, "layer": status.get("layer_num"), "total_layers": status.get("total_layer_num") or (job.get("stats") or {}).get("layers"),
        "percent": status.get("progress"), "started_at": started or None, "ended_at": now,
        "duration_s": round(now - started) if started else None,
    }
    result["summary"] = _summary(outcome, job, result)
    store.jobs.update(job["id"], result=result)
    from .auth import audit  # local import: auth imports the store and mailer, and must not import this module
    audit(None, "print_result", f"{job['name']} on {job.get('printer_name')}: {outcome}"
          + (f" at layer {result['layer']}/{result['total_layers']}" if outcome == "failed" and result["layer"] is not None else "")
          + (f" [{', '.join(h['code'] for h in hms)}]" if hms else ""))
