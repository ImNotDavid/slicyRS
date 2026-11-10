"""Milestone 1.5: saved and shared settings presets."""
import pytest

from app import store
from conftest import make_user

BODY = {"name": "Brackets", "preset": "0.20mm Strength", "filament": "PETG", "settings": {"wall_loops": 5, "sparse_infill_density": 40}}


@pytest.fixture(autouse=True)
def clean(client):
    for p in store.presets.all():
        store.presets.delete(p["id"])


def test_save_list_and_delete_your_own_preset(client):
    r = client.post("/api/presets", json=BODY)
    assert r.status_code == 200 and r.json()["scope"] == "user" and "owner_id" not in r.json()
    listed = client.get("/api/presets").json()
    assert [p["name"] for p in listed] == ["Brackets"] and listed[0]["settings"]["wall_loops"] == 5 and listed[0]["filament"] == "PETG"
    assert client.delete(f"/api/presets/{r.json()['id']}").status_code == 200
    assert client.get("/api/presets").json() == []


def test_presets_are_private_until_an_admin_shares_one(client):
    ann = make_user(client, "preset.ann@imperial.ac.uk")
    bob = make_user(client, "preset.bob@imperial.ac.uk")
    mine = ann.post("/api/presets", json=BODY).json()
    assert bob.get("/api/presets").json() == [] and bob.delete(f"/api/presets/{mine['id']}").status_code == 404
    assert ann.post("/api/presets", json={**BODY, "name": "Everyone", "shared": True}).status_code == 400   # not an admin
    shared = client.post("/api/presets", json={**BODY, "name": "House standard", "shared": True}).json()
    assert shared["scope"] == "shared"
    names = lambda c: [p["name"] for p in c.get("/api/presets").json()]
    assert names(bob) == ["House standard"] and names(ann) == ["House standard", "Brackets"]     # shared first
    assert bob.delete(f"/api/presets/{shared['id']}").status_code == 403                          # visible but not removable
    assert client.delete(f"/api/presets/{mine['id']}").status_code == 404                         # even an admin cannot see private ones
    assert client.delete(f"/api/presets/{shared['id']}").status_code == 200


def test_names_are_unique_where_people_would_see_both(client):
    ann = make_user(client, "preset.ann2@imperial.ac.uk")
    bob = make_user(client, "preset.bob2@imperial.ac.uk")
    assert ann.post("/api/presets", json=BODY).status_code == 200
    assert ann.post("/api/presets", json={**BODY, "name": "brackets"}).status_code == 400          # same name, any case
    assert bob.post("/api/presets", json=BODY).status_code == 200                                  # another person's private name is fine
    assert client.post("/api/presets", json={**BODY, "shared": True}).status_code == 200
    assert ann.post("/api/presets", json={**BODY, "name": "BRACKETS "}).status_code == 400


def test_a_preset_must_be_made_of_known_things(client):
    for bad in ({"preset": "0.99mm Fantasy"}, {"filament": "WOOD"}, {"settings": {"made_up": 1}}, {"settings": {"wall_loops": [1]}}, {"name": "   "}):
        assert client.post("/api/presets", json={**BODY, **bad}).status_code == 400, bad
    assert client.post("/api/presets", json={**BODY, "name": ""}).status_code == 422
