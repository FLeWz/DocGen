"""Milestone 8: login throttling, import/snippet delete, unreferenced file cleanup."""
import os
import uuid

import pytest
from sqlalchemy import text

from app import files, throttle

from .conftest import PW, add_member, new_client, signup
from .test_documents import put, ref
from .test_editor import PNG
from .test_git import commit, doc, mk, save
from .test_import import imp


@pytest.fixture(autouse=True)
def _reset_throttle():
    throttle._fails.clear()


def test_login_throttle(client):
    signup(client, "a@a.co")
    c = new_client()
    bad = {"email": "a@a.co", "password": "wrong-password-1"}
    for _ in range(throttle.MAX_PER_IP):
        assert c.post("/api/auth/login", json=bad).status_code == 401
    r = c.post("/api/auth/login", json=bad)
    assert r.status_code == 429 and "retry-after" in r.headers
    assert c.post("/api/auth/login", json={"email": "a@a.co", "password": PW}).status_code == 429  # even the right one
    assert c.post("/api/auth/login", json={"email": "other@a.co", "password": PW}).status_code == 401  # others unaffected
    throttle._fails.clear()
    assert c.post("/api/auth/login", json={"email": "a@a.co", "password": PW}).status_code == 200


def test_login_success_resets_counter(client):
    signup(client, "a@a.co")
    c = new_client()
    for _ in range(throttle.MAX_PER_IP - 1):
        c.post("/api/auth/login", json={"email": "a@a.co", "password": "wrong-password-1"})
    assert c.post("/api/auth/login", json={"email": "a@a.co", "password": PW}).status_code == 200
    assert c.post("/api/auth/login", json={"email": "a@a.co", "password": "wrong-password-1"}).status_code == 401


@pytest.fixture
def team(client, outbox):
    signup(client, "owner@x.co", "Acme")
    t = {r: add_member(client, outbox, f"{r}@x.co", r) for r in ("admin", "editor", "viewer")}
    t["owner"] = client
    return t


def test_import_delete_roles_and_files(team):
    iid = imp(team["editor"]).json()["id"]
    path = files.import_path(team["owner"].get("/api/orgs/current").json()["id"], iid)
    assert path.exists()
    assert team["viewer"].delete(f"/api/imports/{iid}").status_code == 403
    assert path.exists()
    assert team["editor"].delete(f"/api/imports/{iid}").status_code == 204
    assert not path.exists() and team["owner"].get("/api/imports").json() == []
    assert team["editor"].delete(f"/api/imports/{iid}").status_code == 404


def test_snippet_delete(team):
    c, v = team["editor"], team["viewer"]
    fo = c.post("/api/folders", json={"name": "F"}).json()["id"]
    gone, used, fresh = mk(c, fo, "Gone"), mk(c, fo, "Used"), mk(c, fo, "Fresh")
    for s in (gone, used):
        save(c, s, "text")
    commit(c, fo, [gone, used])
    assert v.delete(f"/api/snippets/{gone}").status_code == 403
    # referenced by a document draft -> refused; by a committed document version -> refused
    d = c.post("/api/documents", json={"folder_id": fo, "title": "D"}).json()["id"]
    sha = c.get(f"/api/snippets/{used}").json()["sha"]
    put(c, d, [ref(fo, used, "commit", sha)])
    assert c.delete(f"/api/snippets/{used}").status_code == 409
    c.post(f"/api/folders/{fo}/commit", json={"message": "d", "document_ids": [d]})
    put(c, d, [])
    assert c.delete(f"/api/snippets/{used}").status_code == 409  # still in a committed version (used-by index)
    # unreferenced: committed one leaves HEAD but stays in history; uncommitted one just disappears
    assert c.delete(f"/api/snippets/{gone}").status_code == 204
    assert c.delete(f"/api/snippets/{fresh}").status_code == 204
    assert {s["id"] for s in c.get(f"/api/folders/{fo}/snippets").json()} == {used}
    assert c.get(f"/api/snippets/{gone}").status_code == 404
    log = c.get(f"/api/folders/{fo}/history").json()
    assert log[0]["message"].startswith("Delete snippet gone") and len(log) == 3
    assert c.get("/api/library/snippets").json()[0]["snippets"][0]["id"] == used


def test_cleanup_images_and_orphans(team, owner_engine):
    a, c = team["admin"], team["editor"]
    org = a.get("/api/orgs/current").json()["id"]
    up = lambda: c.post("/api/images", files={"file": ("x.png", PNG, "image/png")}).json()["id"]
    unused, in_draft, committed, fresh = up(), up(), up(), up()
    fo = c.post("/api/folders", json={"name": "F"}).json()["id"]
    sid = mk(c, fo, "S")
    img = lambda i: {"type": "image", "attrs": {"imageId": i}}
    c.put(f"/api/snippets/{sid}/draft", json={"content": {"type": "doc", "content": [img(committed)]}})
    commit(c, fo, [sid])
    c.put(f"/api/snippets/{sid}/draft", json={"content": {"type": "doc", "content": [img(in_draft)]}})
    with owner_engine.begin() as e:  # age all but `fresh` past the 24 h grace
        e.execute(text("update images set created_at = now() - interval '3 days' where id <> :f"), {"f": fresh})
    old = files.import_path(org, uuid.uuid4())  # orphan file with no row
    old.parent.mkdir(parents=True, exist_ok=True)
    old.write_bytes(b"x")
    os.utime(old, (1, 1))
    assert c.post("/api/maintenance/cleanup").status_code == 403
    r = a.post("/api/maintenance/cleanup?dry_run=true").json()
    assert (r["images_deleted"], r["orphan_files_deleted"]) == (1, 1) and old.exists()
    assert a.get(f"/api/images/{unused}").status_code == 200
    r = a.post("/api/maintenance/cleanup").json()
    assert (r["images_deleted"], r["orphan_files_deleted"]) == (1, 1) and not old.exists()
    assert a.get(f"/api/images/{unused}").status_code == 404
    for kept in (in_draft, committed, fresh):
        assert a.get(f"/api/images/{kept}").status_code == 200
