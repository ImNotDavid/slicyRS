"""Milestone 1.1: the settings panel's schema comes from Bambu Studio's own source."""
import json
import sys
from pathlib import Path

import pytest

from app import catalog, presets
from app.catalog import BASIC_KEYS, FIELDS, SETTINGS_SCHEMA, validate_settings

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import gen_settings_schema as gen  # noqa: E402


def test_the_pages_are_bambu_studios_in_order():
    assert [t["tab"] for t in SETTINGS_SCHEMA] == ["Quality", "Strength", "Speed", "Support", "Others"]
    groups = {t["tab"]: [g["name"] for g in t["groups"]] for t in SETTINGS_SCHEMA}
    assert groups["Quality"][:3] == ["Layer height", "Line width", "Seam"] and "Sparse infill" in groups["Strength"]
    assert "Acceleration" in groups["Speed"] and "Prime tower" in groups["Others"]
    assert len(FIELDS) > 200


def test_every_field_is_complete_and_unique():
    seen = set()
    for t in SETTINGS_SCHEMA:
        for g in t["groups"]:
            for f in g["fields"]:
                assert f["key"] not in seen and f["label"] and f["level"] in ("basic", "simple", "advanced")
                seen.add(f["key"])
                assert f["type"] in ("number", "percent", "number_or_percent", "bool", "select", "text")
                if f["type"] == "select":
                    assert f["options"] and all(o["value"] and o["label"] for o in f["options"])
    assert BASIC_KEYS <= set(FIELDS) and all(FIELDS[k]["level"] == "basic" for k in BASIC_KEYS)
    assert not ({"post_process", "process_notes"} & set(FIELDS))                      # nothing that runs a program


def test_known_options_look_like_bambu_studios():
    f = FIELDS
    assert f["layer_height"]["unit"] == "mm" and f["sparse_infill_density"]["type"] == "percent" and f["sparse_infill_density"]["max"] == 100
    assert f["wall_loops"]["integer"] is True and f["outer_wall_speed"]["unit"] == "mm/s" and f["enable_support"]["type"] == "bool"
    values = {o["value"] for o in f["sparse_infill_pattern"]["options"]}
    assert {"grid", "gyroid", "honeycomb", "lightning"} <= values
    assert {o["value"] for o in f["support_type"]["options"]} >= {"normal(auto)", "tree(auto)"}


def test_values_are_checked_before_they_reach_the_slicer():
    assert validate_settings({"wall_loops": 4, "sparse_infill_density": 25, "enable_support": True, "support_type": "tree(auto)"}) is None
    assert "whole number" in validate_settings({"wall_loops": 2.5})
    assert "at most 100" in validate_settings({"sparse_infill_density": 120})
    assert "at least" in validate_settings({"layer_height": -1})
    assert "invalid" in validate_settings({"support_type": "banana"}) and "invalid" in validate_settings({"enable_support": "yes"})
    assert validate_settings({"seam_slope_start_height": "10%"}) is None and "invalid" in validate_settings({"seam_slope_start_height": "ten"})
    assert validate_settings({"not_a_setting": 1}) is None                             # unknown keys are refused elsewhere


def test_bad_values_are_refused_by_the_api_and_by_saved_presets(client, fleet):
    from conftest import VALID, upload
    mid = upload(client, VALID / "cube_binary.stl").json()["id"]
    r = client.post("/api/slice", json={"model_id": mid, "filament": "PLA", "settings": {"wall_loops": 2.5}})
    assert r.status_code == 400 and "whole number" in r.json()["detail"]
    assert client.post("/api/jobs/preview", json={"model_id": mid, "filament": "PLA", "settings": {"support_type": "banana"}}).status_code == 400
    with pytest.raises(presets.PresetError, match="at most 100"):
        presets._clean("0.20mm Standard", "PLA", {"sparse_infill_density": 500})


def test_every_preset_has_a_value_for_every_setting_in_mock_mode(client):
    c = client.get("/api/catalog").json()
    for preset, vals in c["preset_defaults"].items():
        assert set(FIELDS) <= set(vals), preset
        assert validate_settings({k: v for k, v in vals.items() if k in FIELDS}) is None, preset      # the panel's own defaults are valid


# ---- the generator

TAB = """
void TabPrint::build()
{
    auto page = add_options_page(L("Quality"), "empty");
        auto optgroup = page->new_optgroup(L("Layer height"), L"param_layer_height");
        optgroup->append_single_option_line("layer_height", "layer-height");
        //optgroup->append_single_option_line("commented_out");
        optgroup->append_single_option_line("hidden_dev");
        optgroup = page->new_optgroup(L("Walls"), L"x");
        optgroup->append_single_option_line("wall_sequence");
        optgroup->append_single_option_line("post_process");
#if 0
        optgroup->append_single_option_line("never_shown");
#endif
    page = add_options_page(L("Others"), "advanced");
        optgroup = page->new_optgroup(L("Special mode"), L"y");
        optgroup->append_single_option_line("spiral_mode");
}
void TabPrint::other() {}
"""
CFG = """
    def = this->add("layer_height", coFloat);
    def->label = L("Layer height");
    def->tooltip = L("Not copied");
    def->sidetext = L("mm");
    def->min = 0;
    def->set_default_value(new ConfigOptionFloat(0.2));

    def          = this->add("wall_sequence", coEnum);
    def->label   = L("Order of walls");
    def->enum_values.push_back("inner wall/outer wall");
    def->enum_values.push_back("outer wall/inner wall");
    def->enum_labels.push_back(L("inner/outer"));
    def->enum_labels.push_back(L("outer/inner"));
    def->mode = comAdvanced;
    def->set_default_value(new ConfigOptionEnum<WallSequence>(WallSequence::InnerOuter));

    def = this->add("hidden_dev",coBool);
    def->label = L("Dev only");
    def->mode = comDevelop;

    def = this->add("spiral_mode", coBool);
    def->label = L("Spiral vase");
    def->set_default_value(new ConfigOptionBool(false));

    def = this->add("post_process", coStrings);
    def->label = L("Post-processing Scripts");
"""


def test_the_generator_reads_pages_groups_labels_choices_and_leaves_out_what_it_should():
    out = gen.build(TAB, CFG, "vTEST")
    assert [t["tab"] for t in out["tabs"]] == ["Quality", "Others"]
    quality = out["tabs"][0]["groups"]
    assert [g["name"] for g in quality] == ["Layer height", "Walls"]
    lh = quality[0]["fields"][0]
    assert lh == {"key": "layer_height", "label": "Layer height", "type": "number", "level": "simple", "unit": "mm", "min": 0.0, "default": 0.2}
    assert [f["key"] for f in quality[0]["fields"]] == ["layer_height"]                 # developer-mode option dropped, commented line ignored
    ws = quality[1]["fields"][0]
    assert ws["options"] == [{"value": "inner wall/outer wall", "label": "inner/outer"}, {"value": "outer wall/inner wall", "label": "outer/inner"}]
    assert ws["level"] == "advanced" and "default" not in ws                           # a guessed enum default would be wrong
    assert "post_process" not in json.dumps(out) and "never_shown" not in json.dumps(out) and "Not copied" not in json.dumps(out)
    assert out["source"].startswith("Bambu Studio vTEST")
