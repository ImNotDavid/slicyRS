"""Filament loaded in the pool (owner request): the print flow offers only materials and colours loaded in an AMS or on the
external spool of an in-service printer right now, and a chosen colour is matched when a printer is picked."""
import json
import time

from app import allocator, assign, bambulan, bedstate, store, upkeep
from test_pipeline import pooled_job

RED, BLUE = "#d32f2f", "#1565c0"


def _load(pid, *trays, spool=None, state="IDLE"):
    st = {"state": state, "ams": [{"slot": i, "type": t, "color": c, "remain": r} for i, (t, c, r) in enumerate(trays)]}
    if spool:
        st["spool"] = {"slot": assign.EXTERNAL_SLOT, "type": spool[0], "color": spool[1], "remain": -1}
    store.printers.update(pid, status=st)


def test_the_list_shows_what_in_service_printers_have_loaded(client, fleet):
    _load(fleet["p1s"], ("PLA", RED, 80), ("PETG", BLUE, 3), ("", "#000000", 0))      # nearly empty PETG and an empty tray: not offered
    _load(fleet["mini"], spool=("PLA", BLUE))                                         # no RFID: remain -1 still counts
    _load(fleet["x1c"], ("ABS", RED, 50), state="OFFLINE")                             # offline: its filament is not offered
    _load(fleet["h2d"], ("TPU", RED, 90))
    upkeep.set_maintenance(fleet["h2d"], True, "nozzle", {"email": "t@x"})            # in maintenance: not offered
    try:
        got = client.get("/api/filament/loaded").json()
        assert got == [{"material": "PLA", "colours": [{"colour": BLUE, "printers": ["mini"]}, {"colour": RED, "printers": ["p1s"]}]}]
    finally:
        upkeep.set_maintenance(fleet["h2d"], False, "", {"email": "t@x"})


def test_a_chosen_colour_goes_to_the_printer_that_has_it_and_its_tray(client, fleet):
    me = client.get("/api/me").json()
    for k in ("x1c", "h2d"):
        store.printers.update(fleet[k], enabled=False)
    _load(fleet["p1s"], ("PLA", RED, 80))
    _load(fleet["mini"], ("PETG", RED, 80), ("PLA", BLUE, 80))
    for k in ("p1s", "mini"):
        bedstate.set_state(fleet[k], "clear", "manual")
    jid = pooled_job(me, "blue", time.time())
    store.jobs.update(jid, colour=BLUE)
    allocator.assign_pool()
    j = store.jobs.get(jid)
    assert j["printer_id"] == fleet["mini"] and j["loaded_slot"] == 1
    bedstate.set_state(fleet["mini"], "clear", "manual")
    other = pooled_job(me, "green", time.time())
    store.jobs.update(other, colour="#00ff00")
    allocator.assign_pool()
    assert store.jobs.get(other)["phase"] == "pooled"
    assert "does not have PLA in the chosen colour loaded" in allocator.waiting_reason(store.jobs.get(other))
    for x in (jid, other):
        store.jobs.delete(x)


def test_submit_refuses_a_colour_nobody_has_loaded_and_keeps_the_one_chosen(client, fleet):
    me = client.get("/api/me").json()
    _load(fleet["p1s"], ("PLA", RED, 80))
    jid = pooled_job(me, "sub", time.time())
    store.jobs.update(jid, phase="previewed", state="previewed", draft=True, pooled=False, preview={"time_s": 600, "filament_g": 5})
    r = client.post(f"/api/jobs/{jid}/submit", json={"colour": BLUE})
    assert r.status_code == 409 and "colour" in r.json()["detail"]
    assert client.post(f"/api/jobs/{jid}/submit", json={"colour": RED.upper()}).json()["colour"] == RED
    store.jobs.delete(jid)


def test_the_printer_reports_its_external_spool_and_prints_from_the_chosen_slot():
    st = {}
    link = bambulan.LanLink.__new__(bambulan.LanLink)
    link.id, link.on_status = "x", lambda _id, u: st.update(u)
    report = {"vt_tray": {"tray_type": "PETG", "tray_color": "FF0000FF", "remain": -1},
              "ams": {"ams": [{"id": "0", "tray": [{"id": "1", "tray_type": "PLA", "tray_color": "1565C0FF", "remain": 70}]}]}}
    link._on_message(None, None, type("M", (), {"payload": json.dumps({"print": report})})())
    assert st["spool"] == {"slot": 254, "type": "PETG", "color": "#ff0000", "remain": -1}
    assert st["ams"] == [{"slot": 1, "type": "PLA", "color": BLUE, "remain": 70}]
    assert assign.loaded_slot(st, "PETG", "#ff0000") == 254 and assign.loaded_slot(st, "PLA", BLUE) == 1
