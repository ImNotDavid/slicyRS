"""My Parts (2.17): every upload is kept, listed with search and folders, private to its owner."""
from app import store
from conftest import VALID, make_user, slice_model, upload


def test_parts_list_search_and_folders(client, fleet):
    c = make_user(client, "parts.owner@imperial.ac.uk")
    a = upload(c, VALID / "cube_binary.stl", name="Bracket left.stl").json()
    b = upload(c, VALID / "cube_binary.stl", name="Gear cover.stl").json()
    names = [p["name"] for p in c.get("/api/parts").json()["parts"]]
    assert set(names) >= {"Bracket left.stl", "Gear cover.stl"}
    assert [p["name"] for p in c.get("/api/parts?q=gear").json()["parts"]] == ["Gear cover.stl"]
    f = c.post("/api/parts/folders", json={"name": "Robot arm"}).json()
    assert c.post("/api/parts/folders", json={"name": "robot ARM"}).status_code == 409
    assert c.patch(f"/api/parts/{a['id']}", json={"folder_id": f["id"]}).json()["folder_id"] == f["id"]
    listing = c.get(f"/api/parts?folder={f['id']}").json()
    assert [p["id"] for p in listing["parts"]] == [a["id"]] and listing["folders"][0]["count"] == 1
    assert b["id"] in [p["id"] for p in c.get("/api/parts?folder=none").json()["parts"]]
    assert c.patch(f"/api/parts/{a['id']}", json={"name": "Bracket L v2.stl"}).json()["name"] == "Bracket L v2.stl"
    assert c.delete(f"/api/parts/folders/{f['id']}").status_code == 200
    assert store.models.get(a["id"])["folder_id"] is None


def test_parts_are_private(client):
    c = make_user(client, "parts.private@imperial.ac.uk")
    m = upload(c, VALID / "cube_binary.stl").json()
    other = make_user(client, "parts.nosy@imperial.ac.uk")
    assert m["id"] not in [p["id"] for p in other.get("/api/parts").json()["parts"]]
    assert other.patch(f"/api/parts/{m['id']}", json={"name": "mine now"}).status_code == 404
    assert other.get(f"/api/parts/{m['id']}/thumbnail.png").status_code == 404
    assert other.delete(f"/api/parts/{m['id']}").status_code == 404


def test_a_part_knows_its_jobs_and_has_a_picture(client, fleet):
    m = upload(client, VALID / "cube_binary.stl", name="Counted.stl").json()
    _, job = slice_model(client, m["id"])
    store.jobs.update(job["id"], draft=False)
    p = next(x for x in client.get("/api/parts").json()["parts"] if x["id"] == m["id"])
    assert p["jobs"] == 1 and p["last_job"]["id"] == job["id"] and p["available"]
    r = client.get(f"/api/parts/{m['id']}/thumbnail.png")
    assert r.status_code == 200 and r.content[:4] == b"\x89PNG"


def test_deleting_a_part_removes_it_from_the_list(client):
    m = upload(client, VALID / "cube_binary.stl", name="Gone.stl").json()
    assert client.delete(f"/api/parts/{m['id']}").status_code == 200
    assert m["id"] not in [p["id"] for p in client.get("/api/parts").json()["parts"]]
