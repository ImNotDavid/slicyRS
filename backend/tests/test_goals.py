"""Milestone 1.3: print goals."""
import pytest

from app import goals
from app.catalog import ALLOWED_SETTING_KEYS, FILAMENTS, NOZZLES, PROCESS_PRESETS, preset_nozzle


@pytest.mark.parametrize("nozzle", NOZZLES)
@pytest.mark.parametrize("goal", [g["id"] for g in goals.GOALS])
@pytest.mark.parametrize("filament", list(FILAMENTS))
def test_every_goal_resolves_for_every_material_and_nozzle_to_known_settings(goal, filament, nozzle):
    r = goals.resolve(goal, filament, nozzle)
    assert preset_nozzle(r["preset"]) == nozzle
    assert r["preset"] in {p["id"] for p in PROCESS_PRESETS} and set(r["settings"]) <= ALLOWED_SETTING_KEYS
    layer = next(p["layer_height"] for p in PROCESS_PRESETS if p["id"] == r["preset"])
    assert r["settings"]["layer_height"] == layer


def test_goals_differ_in_the_ways_their_names_promise():
    s = {g["id"]: goals.resolve(g["id"], "PLA") for g in goals.GOALS}
    assert s["draft"]["settings"]["layer_height"] > s["standard"]["settings"]["layer_height"] > s["detail"]["settings"]["layer_height"]
    assert s["strong"]["settings"]["sparse_infill_density"] > s["standard"]["settings"]["sparse_infill_density"] > s["draft"]["settings"]["sparse_infill_density"]
    assert s["strong"]["settings"]["wall_loops"] > s["standard"]["settings"]["wall_loops"]
    assert s["detail"]["settings"]["outer_wall_speed"] < s["standard"]["settings"]["outer_wall_speed"]
    assert all(r["notes"] == [] for r in s.values())                       # nothing adjusted for plain PLA


def test_flexible_filament_is_slowed_but_never_sped_up():
    plain, tpu = goals.resolve("standard", "PLA"), goals.resolve("standard", "TPU")
    assert tpu["settings"]["outer_wall_speed"] == 30 < plain["settings"]["outer_wall_speed"]
    assert any("flexible" in n for n in tpu["notes"])
    slow = goals.resolve("detail", "TPU")["settings"]["outer_wall_speed"]
    assert slow == 30                                                       # 100 for detail, capped at the TPU ceiling
    assert goals.resolve("detail", "PLA")["settings"]["outer_wall_speed"] == 100


def test_warp_prone_materials_get_a_brim_and_pla_does_not():
    abs_ = goals.resolve("strong", "ABS")
    assert abs_["settings"]["brim_type"] == "outer_only" and any("Brim" in n for n in abs_["notes"])
    assert goals.resolve("strong", "PLA")["settings"]["brim_type"] != "outer_only"


def test_the_goal_endpoint_and_the_catalog(client):
    cat = client.get("/api/catalog").json()
    assert [g["id"] for g in cat["goals"]] == ["draft", "standard", "strong", "detail"] and all(g["description"] for g in cat["goals"])
    r = client.get("/api/goals/strong", params={"filament": "ABS"})
    assert r.status_code == 200 and r.json()["preset"] == "0.20mm Strength" and r.json()["notes"]
    assert client.get("/api/goals/standard", params={"nozzle": 0.6}).json()["preset"] == "0.30mm Standard @0.6"
    assert client.get("/api/goals/standard", params={"nozzle": 0.5}).status_code == 400
    assert client.get("/api/goals/nope").status_code == 404
    assert client.get("/api/goals/strong", params={"filament": "WOOD"}).status_code == 400
