import time

from app import seed


def test_seed_is_idempotent_and_complete():
    assert seed.seed() == "demo data created"
    assert seed.seed() == "demo data already present"
    for role, email in seed.USERS.items():  # documented demo logins work
        c = seed.client()
        r = c.post("/api/auth/login", json={"email": email, "password": seed.PW})
        assert r.status_code == 200 and r.json()["org"]["role"] == role, r.text
    c = seed.client()
    c.post("/api/auth/login", json={"email": seed.USERS["viewer"], "password": seed.PW})
    folders = c.get("/api/folders").json()
    assert {f["name"] for f in folders} == {"Security", "Operations", "Runbooks"}
    lib = {g["folder_name"]: g for g in c.get("/api/library/snippets").json()}
    assert [s["title"] for s in lib["Security"]["snippets"]] == ["Access Policy", "Introduction"]
    (d,) = c.get("/api/documents").json()
    assert len(c.get(f"/api/documents/{d['id']}/history").json()) == 2
    ups = c.get(f"/api/documents/{d['id']}/updates").json()
    assert [u["update_available"] for u in ups] == [True, False]  # Security/Introduction pinned at v1.0, Escalation at head
    assert len(c.get("/api/imports").json()) == 1 and len(c.get("/api/base-documents").json()) == 1
    eid = c.get(f"/api/documents/{d['id']}/export").json()["export_id"]
    for _ in range(100):
        st = c.get(f"/api/exports/{eid}").json()
        if st["status"] in ("done", "error"):
            break
        time.sleep(0.1)
    assert st["status"] == "done", st
    assert not st["warnings"], st["warnings"]
