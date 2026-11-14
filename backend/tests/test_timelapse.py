"""Timelapse (2.8): recorded when the owner asks, copied off the SD card after the print, removed from the card, shown to owners and staff."""
import time
import uuid

from app import adapter, bambulan, store, timelapse
from app.storage import JOBS, files
from conftest import make_user
from fake_printer import FakeFTPS
from test_print import CODE, FakeMQTT, bundle, ftps, make_job, make_link, me  # noqa: F401


def test_the_print_command_records_a_timelapse_only_when_asked(client, ftps, bundle):
    pid, link = make_link(ftps.port)
    from app import bambu
    link.send_print(bundle, "a.gcode.3mf")
    bambu._set_status(pid, {"state": "IDLE"})
    link.send_print(bundle, "b.gcode.3mf", timelapse=True)
    cmds = [m["print"] for _, m in link.client.published if m.get("print", {}).get("command") == "project_file"]
    assert [c["timelapse"] for c in cmds] == [False, True]


def test_listing_fetching_and_deleting_on_the_card(client, tmp_path, ftps):
    pid, link = make_link(ftps.port)
    ftps.files["/timelapse/video_2026-11-14_10-00-00.mp4"] = b"old"
    ftps.files["/timelapse/video_2026-11-14_12-00-00.mp4"] = b"new video bytes"
    ftps.files["/timelapse/thumbnail.jpg"] = b"x"
    assert link.list_timelapses() == ["/timelapse/video_2026-11-14_10-00-00.mp4", "/timelapse/video_2026-11-14_12-00-00.mp4"]
    dest = tmp_path / "t.mp4"
    assert link.fetch_timelapse("/timelapse/video_2026-11-14_12-00-00.mp4", dest) == len(b"new video bytes")
    assert dest.read_bytes() == b"new video bytes" and "/timelapse/video_2026-11-14_12-00-00.mp4" not in ftps.files


def test_collect_waits_for_the_new_video_to_stop_growing_then_saves_it(client, me, ftps):
    pid, link = make_link(ftps.port)
    ftps.files["/timelapse/old.mp4"] = b"old"
    jid = make_job(me, pid)
    store.jobs.update(jid, timelapse_wanted=True, timelapse_before=["/timelapse/old.mp4"])
    ticks = []

    def sleep(s):
        ticks.append(s)
        if len(ticks) == 2:                                       # the printer finishes writing between two checks
            ftps.files["/timelapse/new.mp4"] = b"growing"
        if len(ticks) == 3:
            ftps.files["/timelapse/new.mp4"] = b"growing and done"
    import app.adapter as ad
    orig = ad.get
    ad.get = lambda p: link if p == pid else orig(p)
    try:
        name = timelapse.collect(jid, pid, first_wait=0, poll=0, give_up=5, sleep=sleep)
    finally:
        ad.get = orig
    assert name == "timelapse.mp4"
    assert files.path(JOBS, jid, name).read_bytes() == b"growing and done"
    assert "/timelapse/new.mp4" not in ftps.files and "/timelapse/old.mp4" in ftps.files
    page = client.get(f"/api/jobs/{jid}/page").json()
    assert page["timelapse"] == f"/api/jobs/{jid}/timelapse" and page["timelapse_type"] == "mp4"
    r = client.get(f"/api/jobs/{jid}/timelapse")
    assert r.status_code == 200 and r.content == b"growing and done" and r.headers["content-type"] == "video/mp4"
    assert make_user(client, "tl.stranger@imperial.ac.uk").get(f"/api/jobs/{jid}/timelapse").status_code == 404


def test_collect_gives_up_and_says_so(client, me, ftps):
    pid, link = make_link(ftps.port)
    jid = make_job(me, pid)
    store.jobs.update(jid, timelapse_wanted=True, timelapse_before=[])
    import app.adapter as ad
    orig = ad.get
    ad.get = lambda p: link if p == pid else orig(p)
    try:
        assert timelapse.collect(jid, pid, first_wait=0, poll=1, give_up=2, sleep=lambda s: None) is None
    finally:
        ad.get = orig
    from app import jobstate
    assert any(e["kind"] == "timelapse_missing" for e in jobstate.events_for(jid))


def test_submit_and_print_remember_the_request(client, me, ftps, bundle):
    pid, link = make_link(ftps.port)
    jid = make_job(me, pid)
    import app.adapter as ad
    orig = ad.get
    ad.get = lambda p: link if p == pid else orig(p)
    try:
        r = client.post(f"/api/jobs/{jid}/print?timelapse=true")
    finally:
        ad.get = orig
    assert r.status_code == 200, r.text
    j = store.jobs.get(jid)
    assert j["timelapse"] is True and j["timelapse_wanted"] is True
    cmd = [m["print"] for _, m in link.client.published if m.get("print", {}).get("command") == "project_file"][-1]
    assert cmd["timelapse"] is True
