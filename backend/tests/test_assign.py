from app import assign


def printer(pid, model, state="IDLE", ams=None, **kw):
    return {"id": pid, "name": pid, "model": model, "status": {"state": state, "ams": ams or []}, **kw}


FLEET = [
    printer("mini", "A1 mini", ams=[{"slot": 0, "type": "PLA", "remain": 80}]),
    printer("p1s", "P1S", ams=[{"slot": 0, "type": "PLA", "remain": 80}]),
    printer("h2d", "H2D"),
    printer("off", "X1C", state="OFFLINE"),
]


def test_small_part_prefers_smallest_bed():
    assert assign.choose(FLEET, (50, 50, 50), "PLA").printer_id == "mini"


def test_large_part_skips_small_printers():
    assert assign.choose(FLEET, (200, 200, 200), "PLA").printer_id == "p1s"


def test_huge_part_needs_h2d():
    assert assign.choose(FLEET, (300, 300, 300), "PLA").printer_id == "h2d"


def test_enclosed_material_excludes_open_printers():
    assert assign.choose(FLEET, (50, 50, 50), "ABS").printer_id == "p1s"


def test_offline_never_chosen():
    ranked = {c.printer_id: c for c in assign.rank(FLEET, (50, 50, 50), "PLA")}
    assert not ranked["off"].eligible


def test_busy_printer_ranks_below_idle():
    fleet = [printer("busy", "P1S", state="RUNNING", ams=[{"slot": 0, "type": "PLA", "remain": 80}]),
             printer("idle", "P1S")]
    assert assign.choose(fleet, (50, 50, 50), "PLA").printer_id == "idle"


def test_too_big_everywhere_returns_none():
    assert assign.choose(FLEET, (400, 400, 400), "PLA") is None


def test_carbon_fibre_needs_hardened_nozzle():
    assert assign.choose(FLEET, (50, 50, 50), "PA-CF") is None
    fleet = [printer("x", "X1C", hardened_nozzle=True)]
    assert assign.choose(fleet, (50, 50, 50), "PA-CF").printer_id == "x"
