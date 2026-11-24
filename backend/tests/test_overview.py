"""GET /api/overview: counts per folder and the flat item list; viewers may read, other orgs never show up."""
from .conftest import new_client, signup
from .test_git import mk


def test_overview_counts_and_isolation(client, outbox):
    signup(client, "alice@a.co", "Org A")
    f = client.post("/api/folders", json={"name": "Docs"}).json()["id"]
    mk(client, f, "One"); mk(client, f, "Two")
    client.post("/api/documents", json={"folder_id": f, "title": "Manual"})
    o = client.get("/api/overview").json()
    assert o["counts"][f] == {"snippets": 2, "documents": 1}
    assert sorted(i["title"] for i in o["items"]) == ["Manual", "One", "Two"]
    other = new_client()
    signup(other, "bob@b.co", "Org B")
    assert other.get("/api/overview").json() == {"counts": {}, "items": []}
