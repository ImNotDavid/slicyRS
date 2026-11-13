"""The job page (2.9): one answer with everything about a job, readable by its owner, operators and admins."""
from app import store
from conftest import VALID, make_user, slice_model, upload


def page(c, jid):
    return c.get(f"/api/jobs/{jid}/page")


def test_owner_sees_settings_changed_from_the_preset_and_the_goal(client, fleet):
    m = upload(client, VALID / "cube_binary.stl").json()
    _, job = slice_model(client, m["id"], settings={"wall_loops": 5}, goal="strong")
    p = page(client, job["id"]).json()
    assert p["mine"] and p["can_manage"] and p["goal"]["name"] == "Strong"
    changed = {s["key"]: s for s in p["changed_settings"]}
    assert changed["wall_loops"]["value"] == 5 and changed["wall_loops"]["label"]
    assert p["parts"][0]["available"] and p["parts"][0]["model_id"] == m["id"] and p["gcode"] is True
    assert p["printer"]["name"] and p["nozzle_mm"] == 0.4 and p["photo"] is None and p["events"]


def test_members_cannot_open_other_peoples_jobs_but_operators_can_read_them(client, fleet):
    m = upload(client, VALID / "cube_binary.stl").json()
    _, job = slice_model(client, m["id"])
    other = make_user(client, "jp.member@imperial.ac.uk")
    assert page(other, job["id"]).status_code == 404 and other.get(f"/api/jobs/{job['id']}/gcode").status_code == 404
    op = make_user(client, "jp.operator@imperial.ac.uk")
    u = next(x for x in store.users.all() if x["email"] == "jp.operator@imperial.ac.uk")
    store.users.update(u["id"], operator=True)
    p = page(op, job["id"]).json()
    assert not p["mine"] and not p["can_manage"] and p["owner"]
    assert p["parts"][0]["model_id"] is None and not p["parts"][0]["available"]       # the model file stays its owner's
    assert op.get(f"/api/jobs/{job['id']}/gcode").status_code == 200                 # but the toolpaths can be looked at
    assert op.post(f"/api/jobs/{job['id']}/cancel").status_code == 404               # reading is not managing


def test_a_deleted_model_is_reported_as_unavailable(client, fleet):
    m = upload(client, VALID / "cube_binary.stl").json()
    _, job = slice_model(client, m["id"])
    store.models.update(m["id"], files_deleted_at=1)
    assert page(client, job["id"]).json()["parts"][0]["available"] is False


def test_model_info_for_reuse(client):
    m = upload(client, VALID / "cube_binary.stl").json()
    info = client.get(f"/api/models/{m['id']}").json()
    assert info["name"] == m["name"] and len(info["dims"]) == 3
    assert make_user(client, "jp.stranger@imperial.ac.uk").get(f"/api/models/{m['id']}").status_code == 404


def test_unknown_job_page_is_404(client):
    assert page(client, "nope").status_code == 404
