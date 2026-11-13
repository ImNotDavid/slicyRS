"""Record what a printer reported when a print it was given ended, so a failed print explains itself.

Called for every status update of a printer. When the printer reports FINISH or FAILED for a print this app started
(matched by the job's remote file name), the result is stored once on the job and written to the audit log.
"""
import time
from typing import Any

from . import jobstate, store

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
        if (j.get("printer_id") == printer_id and j.get("printed") and not j.get("result") and remote and remote == subtask_name
                and not j.get("batch_lead")):
            return j
    return None


MOVED_ON_AFTER_S = 600       # a print that started this long ago and is no longer the printer's current one has ended
NOT_SEEN_AFTER_DAYS = 7      # a printed job with no result at all after this long will not get one


def _audit(action: str, detail: str) -> None:
    from .auth import audit  # local import: auth imports the store and mailer, and must not import this module
    audit(None, action, detail)


def mark_unknown(job: dict[str, Any], reason: str) -> None:
    """The print ended (or is long over) but its outcome was never seen: say so instead of showing it as in progress forever."""
    result = {"outcome": "unknown", "summary": reason, "hms": [], "layer": None, "total_layers": None, "percent": None,
              "print_error": 0, "printer_state": None, "started_at": job.get("started_at"), "ended_at": time.time(), "duration_s": None}
    store.jobs.update(job["id"], result=result)
    jobstate.transition(job["id"], "unknown", None, force=True, state="done")
    _audit("print_result_unknown", f"{job['name']} on {job.get('printer_name')}: {reason}")


def _unresolved(printer_id: str | None = None) -> list[dict[str, Any]]:
    return [j for j in store.jobs.all() if j.get("printed") and not j.get("result") and not j.get("batch_lead")
            and (printer_id is None or j.get("printer_id") == printer_id)]


def sweep(now: float | None = None) -> int:
    """Resolve printed jobs that can no longer be matched to a printer report. Run at start-up and periodically."""
    now = now or time.time()
    n = 0
    for j in _unresolved():
        if not j.get("remote_name"):
            mark_unknown(j, "Printed before result tracking was added, so the printer's outcome was not recorded.")
            n += 1
        elif now - (j.get("started_at") or j.get("created", now)) > NOT_SEEN_AFTER_DAYS * 86400:
            mark_unknown(j, "The printer's result was never seen (the server may have been stopped or the printer offline while it ended).")
            n += 1
    return n


def _resolve_moved_on(printer_id: str, current_name: str) -> None:
    """If the printer now reports a different print, any older print of ours on it is over; its result was missed."""
    now = time.time()
    for j in _unresolved(printer_id):
        remote = (j.get("remote_name") or "").removesuffix(".gcode.3mf")
        if remote and remote != current_name and now - (j.get("started_at") or now) > MOVED_ON_AFTER_S:
            mark_unknown(j, "The printer moved on to another print before this result was seen (the server may have been stopped "
                            "or the printer offline when it ended). Check the printer's history for the outcome.")


def on_status(printer_id: str, status: dict[str, Any]) -> None:
    name = status.get("subtask_name")
    if name:
        _record_final(printer_id, status, name)
        _resolve_moved_on(printer_id, name)


def _record_final(printer_id: str, status: dict[str, Any], name: str) -> None:
    outcome = FINAL.get(status.get("state", ""))
    if not outcome:
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
    jobstate.transition(job["id"], "printed" if outcome == "finished" else "failed", None, force=True, state="done")
    if outcome == "failed":
        _tell_failed(job, result)
        from . import upkeep         # owner decision (2.6): a failed print takes the printer out of the pool until someone checks it
        upkeep.flag_attention(printer_id, f"the last print ({job['name']}) failed" + (f" at layer {result['layer']}" if result.get("layer") is not None else ""), job["id"])

    _audit("print_result", f"{job['name']} on {job.get('printer_name')}: {outcome}"
          + (f" at layer {result['layer']}/{result['total_layers']}" if outcome == "failed" and result["layer"] is not None else "")
          + (f" [{', '.join(h['code'] for h in hms)}]" if hms else ""))


def _tell_failed(job: dict[str, Any], result: dict[str, Any]) -> None:
    from . import notify
    where = f" at layer {result['layer']} of {result['total_layers']}" if result.get("layer") is not None and result.get("total_layers") else ""
    owners = [j["owner_email"] for j in [job, *(store.jobs.get(m) or {} for m in job.get("members") or [])] if j.get("owner_email")]
    notify.emit("failed", f"Your print {job['name']} failed{where}", f"{job['name']} stopped on {job.get('printer_name')}{where}.\n\n{result['summary']}",
                owners=owners, link="/#/jobs", job=job,
                operator_body=f"{job.get('printer_name')}: {job['name']} ({job.get('owner_email')}) failed{where}.")

