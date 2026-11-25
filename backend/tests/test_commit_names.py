"""Round 14: commit display-name overrides (git untouched): rename, reset, validation, roles, isolation, revision table, cache."""
from sqlalchemy import text

from app.git import repo as git

from .conftest import add_member, signup
from .test_base_docs import make_docx, upload
from .test_documents import ref, ws  # noqa: F401  (ws is a fixture)
from .test_version_export import doc_commit, text_of, two_commits, vexport

U = "/api/folders/{}/commits/{}"


def hist(c, fo):
    return {x["sha"]: x for x in c.get(f"/api/folders/{fo}/history").json()}


def test_rename_reset_and_every_history_shows_it(ws):
    c, fa = ws["c"], ws["fa"]
    r = c.patch(U.format(fa, ws["v1"]), json={"name": "  Nice name  "})
    assert r.status_code == 200, r.text
    j = r.json()
    assert (j["message"], j["name"], j["original_message"], j["renamed"], j["renamed_by"]) == ("Nice name", "Nice name", "v1", True, "Alice A")
    assert j["sha"] == ws["v1"] and j["renamed_at"]
    for items in (c.get(f"/api/folders/{fa}/history").json(), c.get(f"/api/snippets/{ws['sid']}/history").json()):
        m = {x["sha"]: x for x in items}
        assert m[ws["v1"]]["message"] == "Nice name" and not m[ws["v2"]]["renamed"] and m[ws["v2"]]["message"] == "v2"
    assert c.patch(U.format(fa, ws["v1"]), json={"name": "Again"}).json()["message"] == "Again"  # replace, still one row
    # git itself is untouched
    assert git._git(git.repo_path(ws["org"], fa), "log", "-1", "--format=%s", ws["v1"]).decode().strip() == "v1"
    for reset in (c.patch(U.format(fa, ws["v1"]), json={"name": "  "}), c.patch(U.format(fa, ws["v1"]), json={}), c.delete(U.format(fa, ws["v1"]))):
        assert reset.status_code == 200 and reset.json()["message"] == "v1" and not reset.json()["renamed"]
        c.patch(U.format(fa, ws["v1"]), json={"name": "X"})
    c.delete(U.format(fa, ws["v1"]))
    assert not hist(c, fa)[ws["v1"]]["renamed"]


def test_validation_and_roles(ws, outbox):
    c, fa = ws["c"], ws["fa"]
    for bad in ({"name": "a\nb"}, {"name": "a\x00b"}, {"name": "x" * 201}, {"name": "\tz"[:1] + "q\x07"}):
        assert c.patch(U.format(fa, ws["v1"]), json=bad).status_code == 422, bad
    assert c.patch(U.format(fa, ws["v1"]), json={"name": "é" * 200}).status_code == 200
    assert c.patch(U.format(fa, "a" * 40), json={"name": "x"}).status_code == 404  # well-formed, not a commit
    assert c.patch(U.format(fa, "nothex"), json={"name": "x"}).status_code == 422
    assert c.patch(U.format(ws["fb"], ws["v1"]), json={"name": "x"}).status_code == 404  # commit of another folder's repo
    assert c.patch(U.format("00000000-0000-0000-0000-000000000000", ws["v1"]), json={"name": "x"}).status_code == 404
    ed, vi = add_member(c, outbox, "e@a.co", "editor"), add_member(c, outbox, "v@a.co", "viewer")
    assert ed.patch(U.format(fa, ws["v2"]), json={"name": "by editor"}).json()["renamed_by"] == "N"
    assert vi.patch(U.format(fa, ws["v2"]), json={"name": "no"}).status_code == 403
    assert vi.delete(U.format(fa, ws["v2"])).status_code == 403
    assert vi.get(f"/api/folders/{fa}/history").json()[0]["message"] == "by editor"


def test_cross_org_is_404(ws, outbox):
    from .conftest import new_client
    c = ws["c"]
    c.patch(U.format(ws["fa"], ws["v1"]), json={"name": "secret"})
    b = new_client()
    signup(b, "bob@b.co", "Org B", name="Bob")
    assert b.patch(U.format(ws["fa"], ws["v1"]), json={"name": "pwn"}).status_code == 404
    assert b.delete(U.format(ws["fa"], ws["v1"])).status_code == 404
    assert hist(c, ws["fa"])[ws["v1"]]["message"] == "secret"


def test_revision_table_uses_override_and_cache_is_invalidated(ws):
    c = ws["c"]
    a, b = two_commits(ws)
    j1, _, d1 = vexport(c, ws["doc"], a)
    assert vexport(c, ws["doc"], a)[0]["cached"] is True
    assert "first doc" in text_of(d1)
    assert c.patch(U.format(ws["fb"], a), json={"name": "Renamed rev"}).status_code == 200
    j2, _, d2 = vexport(c, ws["doc"], a)
    t = text_of(d2)
    assert j2["cached"] is False and "Renamed rev" in t and "first doc" not in t
    assert vexport(c, ws["doc"], a)[0]["cached"] is True
    assert "Renamed rev" in text_of(vexport(c, ws["doc"], b)[2])  # b's table includes the renamed commit
    c.delete(U.format(ws["fb"], a))  # reset = original inputs again: the original render is a hit
    j3, _, d3 = vexport(c, ws["doc"], a)
    assert j3["cached"] is True and "first doc" in text_of(d3)
    assert "second doc" in [x["message"] for x in c.get(f"/api/documents/{ws['doc']}/revision-history").json()]
    c.patch(U.format(ws["fa"], ws["v1"]), json={"name": "snip rename"})  # snippet commits too (pinned snippet history)
    c.patch(U.format(ws["fb"], b), json={"name": "doc rename"})
    msgs = [r["message"] for r in c.get(f"/api/documents/{ws['doc']}/revision-history").json()]
    assert "doc rename" in msgs and "second doc" not in msgs


def test_base_document_versions(ws, outbox):
    c = ws["c"]
    bid = upload(c, make_docx(), "Corp").json()["id"]
    v = c.get(f"/api/base-documents/{bid}/versions").json()[0]
    u = f"/api/base-documents/{bid}/versions/{v['sha']}"
    r = c.patch(u, json={"name": "Corporate template"})
    assert r.status_code == 200 and r.json()["message"] == "Corporate template" and r.json()["renamed"]
    assert r.json()["original_message"] == v["message"]
    assert c.get(f"/api/base-documents/{bid}/versions").json()[0]["message"] == "Corporate template"
    assert c.get(f"/api/base-documents/{bid}").json()["head"]["name"] == "Corporate template"
    assert c.patch(f"/api/base-documents/{bid}/versions/{'b' * 40}", json={"name": "x"}).status_code == 404
    assert c.patch(u, json={"name": "a\nb"}).status_code == 422
    ed, vi = add_member(c, outbox, "e@a.co", "editor"), add_member(c, outbox, "v@a.co", "viewer")
    assert ed.patch(u, json={"name": "x"}).status_code == 403 and vi.patch(u, json={"name": "x"}).status_code == 403
    assert vi.get(f"/api/base-documents/{bid}/versions").json()[0]["name"] == "Corporate template"
    assert c.delete(u).json()["renamed"] is False
    c.patch(u, json={"name": "again"})
    assert c.delete(f"/api/base-documents/{bid}").status_code == 204


def rows(owner_engine):
    with owner_engine.connect() as cn:
        return cn.execute(text("select scope, sha from commit_names")).all()


def test_deleting_document_and_folder_cleans_overrides(ws, owner_engine):
    c = ws["c"]
    a, b = two_commits(ws)
    c.patch(U.format(ws["fb"], a), json={"name": "d1"})
    c.patch(U.format(ws["fa"], ws["v1"]), json={"name": "s1"})
    assert len(rows(owner_engine)) == 2
    assert c.delete(f"/api/documents/{ws['doc']}").status_code == 204
    assert [r[1] for r in rows(owner_engine)] == [ws["v1"]]  # snippet commit stays, document commit override gone
    assert c.delete(f"/api/folders/{ws['fa']}").status_code == 204
    assert rows(owner_engine) == []


def test_deleting_base_document_cleans_overrides(ws, owner_engine):
    c = ws["c"]
    bid = upload(c, make_docx(), "Corp").json()["id"]
    sha = c.get(f"/api/base-documents/{bid}/versions").json()[0]["sha"]
    c.patch(f"/api/base-documents/{bid}/versions/{sha}", json={"name": "n"})
    assert len(rows(owner_engine)) == 1
    assert c.delete(f"/api/base-documents/{bid}").status_code == 204
    assert rows(owner_engine) == []
