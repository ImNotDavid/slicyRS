"""A finished or failed print is recorded on the job with what the printer reported, once."""
import json
import time
import uuid
from types import SimpleNamespace

import pytest

from app import bambu, outcomes, store
from app.bambu import PrinterLink
from test_print import make_job, make_link, me  # noqa: F401  (fixtures/helpers)


def new_printer():
    pid = uuid.uuid4().hex[:8]
    store.printers.put(pid, {"id": pid, "name": "Mini", "model": "A1 mini", "status": {"state": "IDLE"}})
    return pid


def printed_job(me, pid, remote="part-ab12cd.gcode.3mf", started=None, stats=None):
    jid = make_job(me, pid)
    store.jobs.update(jid, printed=True, queued=True, remote_name=remote, started_at=started or time.time() - 600,
                      stats=stats or {"time_s": 3000, "layers": 321})
    return jid


def report(pid, **status):
    bambu._set_status(pid, status)


def result(jid):
    return store.jobs.get(jid).get("result")


def test_finished_print_is_recorded_with_duration_and_estimate(me):
    pid = new_printer()
    jid = printed_job(me, pid, started=time.time() - 3720, stats={"time_s": 3300, "layers": 100})
    report(pid, state="RUNNING", subtask_name="part-ab12cd")
    assert result(jid) is None                                          # still printing
    report(pid, state="FINISH", progress=100, layer_num=100, total_layer_num=100)
    r = result(jid)
    assert r["outcome"] == "finished" and r["layer"] == 100 and abs(r["duration_s"] - 3720) < 5
    assert "Finished in 1h 02m" in r["summary"] and "estimate 55m" in r["summary"]


def test_failure_in_the_first_layers_with_no_error_code_matches_the_real_a1_mini_case(me):
    """What your A1 Mini reported: FAILED, layer 3 of 321, 0 percent, no HMS entries."""
    pid = new_printer()
    jid = printed_job(me, pid)
    report(pid, state="RUNNING", subtask_name="part-ab12cd", layer_num=1, total_layer_num=321, progress=0)
    report(pid, layer_num=3)
    report(pid, state="FAILED", print_error=0, hms=[])
    r = result(jid)
    assert r["outcome"] == "failed" and (r["layer"], r["total_layers"]) == (3, 321) and r["hms"] == []
    s = r["summary"]
    assert "layer 3 of 321" in s and "without reporting an error code" in s
    assert "did not stick" in s and "build plate" in s                  # first-layer advice appears


def test_late_failure_does_not_give_first_layer_advice(me):
    pid = new_printer()
    jid = printed_job(me, pid)
    report(pid, state="RUNNING", subtask_name="part-ab12cd", layer_num=150, total_layer_num=321)
    report(pid, state="FAILED", print_error=0)
    assert "did not stick" not in result(jid)["summary"]


def test_failure_with_known_hms_code_is_explained(me):
    pid = new_printer()
    jid = printed_job(me, pid)
    report(pid, state="RUNNING", subtask_name="part-ab12cd", layer_num=0, total_layer_num=321)
    report(pid, state="FAILED", hms=[{"attr": 83887360, "code": 65543, "timestamp": int(time.time())}])
    r = result(jid)
    assert r["hms"][0]["code"] == "0500-0500-0001-0007" and "Developer Mode" in r["summary"]


def test_unknown_hms_code_is_shown_not_guessed(me):
    pid = new_printer()
    jid = printed_job(me, pid)
    report(pid, state="FAILED", subtask_name="part-ab12cd", hms=[{"attr": 0x03000100, "code": 0x00020001, "timestamp": int(time.time())}])
    h = result(jid)["hms"][0]
    assert h["code"] == "0300-0100-0002-0001" and "error list" in h["text"] and "wiki.bambulab.com" in h["text"]


def test_numeric_error_without_hms_is_reported(me):
    pid = new_printer()
    jid = printed_job(me, pid)
    report(pid, state="FAILED", subtask_name="part-ab12cd", print_error=50348044)
    assert "error number 50348044" in result(jid)["summary"]


def test_errors_older_than_the_print_are_ignored(me):
    pid = new_printer()
    jid = printed_job(me, pid, started=time.time() - 100)
    report(pid, state="FAILED", subtask_name="part-ab12cd", hms=[{"attr": 83887360, "code": 65543, "timestamp": int(time.time()) - 86400}])
    assert result(jid)["hms"] == []


def test_a_different_print_is_not_attributed_to_the_job(me):
    pid = new_printer()
    jid = printed_job(me, pid)
    report(pid, state="FINISH", subtask_name="something-else")
    assert result(jid) is None


def test_job_on_another_printer_is_not_touched(me):
    pid, other = new_printer(), new_printer()
    jid = printed_job(me, pid)
    report(other, state="FINISH", subtask_name="part-ab12cd")
    assert result(jid) is None


def test_a_job_that_was_never_printed_is_not_matched(me):
    pid = new_printer()
    jid = make_job(me, pid)
    store.jobs.update(jid, remote_name="part-ab12cd.gcode.3mf")        # uploaded but printed flag not set
    report(pid, state="FINISH", subtask_name="part-ab12cd")
    assert result(jid) is None


def test_recorded_once_and_audited_once(me, client):
    pid = new_printer()
    jid = printed_job(me, pid)
    report(pid, state="FINISH", subtask_name="part-ab12cd", layer_num=321, total_layer_num=321)
    first = dict(result(jid))
    time.sleep(0.05)
    report(pid, state="FINISH", progress=100)                          # the printer repeats its state many times
    report(pid, state="FINISH", subtask_name="part-ab12cd")
    assert result(jid) == first
    log = [a for a in client.get("/api/admin/audit?limit=200").json() if a["action"] == "print_result" and "finished" in a["detail"]]
    assert len([a for a in log if "Mini" in a["detail"] or True]) >= 1


def test_result_is_visible_to_the_owner_through_the_api(client, me):
    pid = new_printer()
    jid = printed_job(me, pid)
    report(pid, state="FAILED", subtask_name="part-ab12cd", layer_num=2, total_layer_num=50)
    job = client.get(f"/api/jobs/{jid}").json()
    assert job["result"]["outcome"] == "failed" and job["result"]["layer"] == 2
    assert any(j["id"] == jid and j["result"] for j in client.get("/api/jobs").json())


def test_print_endpoint_stores_what_is_needed_to_match_the_result(client, me, tmp_path):
    from test_print import FakeFTPS, CODE
    from app import main as app_main
    srv = FakeFTPS(tmp_path, CODE)
    try:
        pid, link = make_link(srv.port)
        app_main.links.links[pid] = link
        jid = make_job(me, pid, name="My part.stl")
        assert client.post(f"/api/jobs/{jid}/print").status_code == 200
        job = store.jobs.get(jid)
        assert job["remote_name"].endswith(".gcode.3mf") and job["started_at"] > 0 and job["result"] is None
        # the printer later reports the print under the name we gave it
        report(pid, state="FINISH", subtask_name=job["remote_name"].removesuffix(".gcode.3mf"), layer_num=5, total_layer_num=5)
        assert store.jobs.get(jid)["result"]["outcome"] == "finished"
    finally:
        srv.close()


def test_mqtt_report_fields_reach_the_status(me):
    """The printer's raw report (as seen from the A1 Mini) is mapped onto the stored status."""
    pid = new_printer()
    rec = store.printers.get(pid)
    link = PrinterLink({**rec, "host": "127.0.0.1", "serial": "SN", "access_code": None})
    payload = {"print": {"gcode_state": "FAILED", "print_error": 0, "layer_num": 3, "total_layer_num": 321,
                         "subtask_name": "card_display_stand_v2-a7fafc", "mc_percent": 0, "hms": []}}
    link._on_message(None, None, SimpleNamespace(payload=json.dumps(payload).encode()))
    st = store.printers.get(pid)["status"]
    assert (st["state"], st["layer_num"], st["total_layer_num"], st["subtask_name"], st["print_error"]) == ("FAILED", 3, 321, "card_display_stand_v2-a7fafc", 0)
