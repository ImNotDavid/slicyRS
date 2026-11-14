"""Filament inventory (2.10): spools, weight taken off by prints, cost, staff only."""
import time
import uuid

from app import filament, outcomes, store
from conftest import make_user


def test_staff_add_and_correct_spools_and_members_cannot(client, fleet):
    member = make_user(client, "fil.member@imperial.ac.uk")
    assert member.get("/api/filament").status_code == 403
    r = client.post("/api/filament/spools", json={"material": "PLA", "colour_name": "Blue", "colour_hex": "#2f6bff", "brand": "Bambu",
                                                  "start_g": 1000, "price": 24.0, "printer_id": fleet["mini"]})
    assert r.status_code == 200
    s = r.json()
    assert s["remaining_g"] == 1000 and s["percent"] == 100 and s["cost_per_kg"] == 24.0 and s["printer_name"] == "mini"
    assert client.post("/api/filament/spools", json={"material": "Unobtainium", "start_g": 1000}).status_code == 400
    assert client.post("/api/filament/spools", json={"material": "PLA", "start_g": 1000, "remaining_g": 2000}).status_code == 400
    u = client.put(f"/api/filament/spools/{s['id']}", json={"material": "PLA", "start_g": 1000, "remaining_g": 640, "colour_hex": "#2f6bff"}).json()
    assert u["remaining_g"] == 640 and u["percent"] == 64
    listing = client.get("/api/filament").json()
    assert listing["totals"]["PLA"]["remaining_g"] >= 640
    assert client.delete(f"/api/filament/spools/{s['id']}").status_code == 200


def test_a_finished_print_takes_its_grams_off_the_loaded_spool(client, fleet):
    s = filament.add({"material": "PLA", "start_g": 1000, "printer_id": fleet["mini"], "colour_hex": "#ffffff"}, {"email": "t@x"})
    pid = fleet["mini"]
    jid = uuid.uuid4().hex[:8]
    store.jobs.put(jid, {"id": jid, "name": "Used", "printer_id": pid, "printer_name": "mini", "phase": "printing", "printed": True, "filament": "PLA",
                         "remote_name": "used-1.gcode.3mf", "stats": {"filament_g": 42.5}, "started_at": time.time() - 100, "created": time.time(), "state": "done"})
    outcomes.on_status(pid, {"state": "FINISH", "subtask_name": "used-1"})
    assert store.spools.get(s["id"])["remaining_g"] == 957.5
    store.spools.delete(s["id"])


def test_a_failed_print_takes_only_what_it_printed_and_other_materials_are_untouched(client, fleet):
    pla = filament.add({"material": "PLA", "start_g": 1000, "printer_id": fleet["p1s"], "colour_hex": "#ffffff"}, {"email": "t@x"})
    petg = filament.add({"material": "PETG", "start_g": 1000, "printer_id": fleet["p1s"], "colour_hex": "#000000"}, {"email": "t@x"})
    filament.after_print({"id": "j", "filament": "PLA", "stats": {"filament_g": 100}}, fleet["p1s"], {"outcome": "failed", "percent": 25})
    assert store.spools.get(pla["id"])["remaining_g"] == 975 and store.spools.get(petg["id"])["remaining_g"] == 1000
    for s in (pla, petg):
        store.spools.delete(s["id"])
