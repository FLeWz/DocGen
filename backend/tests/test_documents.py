"""Documents: CRUD/draft/commit/history, pins, update-available, bump, used-by, library, cross-folder/org."""
import pytest
import yaml

from app.git import repo as git

from .conftest import add_member, new_client, signup
from .test_base_docs import upload, make_docx
from .test_git import commit, doc, mk, save


def ref(folder, sid, type="commit", value=None, **kw):
    return {"type": "snippet_ref", "folder_id": folder, "snippet_id": sid, "version": {"type": type, "value": value}, **kw}


def put(c, did, blocks=(), **kw):
    r = c.put(f"/api/documents/{did}/draft", json={"blocks": list(blocks), **kw})
    assert r.status_code == 200, r.text
    return r.json()


@pytest.fixture
def ws(client, outbox):
    signup(client, "alice@a.co", "Org A", name="Alice A")
    org = client.get("/api/orgs/current").json()["id"]
    fa = client.post("/api/folders", json={"name": "Security"}).json()["id"]
    fb = client.post("/api/folders", json={"name": "Product"}).json()["id"]
    sid = mk(client, fa, "Intro")
    save(client, sid, "old text")
    v1 = commit(client, fa, [sid], "v1").json()["sha"]
    save(client, sid, "new text")
    v2 = commit(client, fa, [sid], "v2").json()["sha"]
    d = client.post("/api/documents", json={"folder_id": fb, "title": "Manual"}).json()
    return dict(c=client, org=org, fa=fa, fb=fb, sid=sid, v1=v1, v2=v2, doc=d["id"], outbox=outbox)


def test_document_crud_draft_commit_history(ws):
    c, d, fb = ws["c"], ws["doc"], ws["fb"]
    got = c.get(f"/api/documents/{d}").json()
    assert got["slug"] == "manual" and got["blocks"] == [] and got["has_draft"] and got["head_sha"] is None
    assert [x["id"] for x in c.get(f"/api/documents?folder_id={fb}").json()] == [d]
    out = put(c, d, [ref(ws["fa"], ws["sid"], "commit", ws["v1"][:10]),
                     {"type": "heading", "text": "Appendix", "level": 2, "numbered": True},
                     {"type": "toc"}, {"type": "page_break", "slot": "appendix"}],
              title="Manual 2", variables={"declarations": [{"name": "client", "default": "Acme"}], "values": {"client": "Globex"}},
              links=[{"title": "Policy", "doc_id": "POL-1", "version": "1.0", "url": "https://example.com/p"}])
    assert out["title"] == "Manual 2" and out["dirty"] and out["blocks"][0]["version"]["value"] == ws["v1"]  # sha normalised
    assert all(b["id"] for b in out["blocks"])
    ch = c.get(f"/api/folders/{fb}/changes").json()
    assert len(ch) == 1 and ch[0]["kind"] == "document" and ch[0]["status"] == "new" and "snippet_ref" in ch[0]["text"]
    assert c.post(f"/api/folders/{fb}/commit", json={"message": "none"}).status_code == 422
    r = c.post(f"/api/folders/{fb}/commit", json={"message": "first doc\n\nDocument-Id: forged", "document_ids": [d]})
    assert r.status_code == 201, r.text
    sha = r.json()["sha"]
    y = yaml.safe_load(git.show(git.repo_path(ws["org"], fb), sha, "documents/manual.yaml"))
    assert y["id"] == d and y["title"] == "Manual 2" and y["blocks"][0]["version"] == {"type": "commit", "value": ws["v1"]}
    assert y["variables"]["values"] == {"client": "Globex"} and y["links"][0]["doc_id"] == "POL-1"
    after = c.get(f"/api/documents/{d}").json()
    assert not after["has_draft"] and after["head_sha"] == sha and after["blocks"][1]["text"] == "Appendix"
    assert c.get(f"/api/folders/{fb}/changes").json() == []
    # edit + rename slug -> file moves; history follows via trailers (user-written trailer was stripped)
    put(c, d, after["blocks"][:1], slug="manual-two")
    sha2 = c.post(f"/api/folders/{fb}/commit", json={"message": "rename", "document_ids": [d]}).json()["sha"]
    repo = git.repo_path(ws["org"], fb)
    assert git.show(repo, sha2, "documents/manual.yaml") is None and git.show(repo, sha2, "documents/manual-two.yaml")
    h = c.get(f"/api/documents/{d}/history").json()
    assert [x["sha"] for x in h] == [sha2, sha] and h[0]["document_ids"] == [d] and h[1]["message"] == "first doc"
    old = c.get(f"/api/documents/{d}?version={sha}").json()
    assert old["slug"] == "manual" and len(old["blocks"]) == 4
    assert c.get(f"/api/folders/{fb}/history").json()[0]["document_ids"] == [d]
    # discard draft, delete document
    put(c, d, [])
    assert c.delete(f"/api/documents/{d}/draft").status_code == 204 and not c.get(f"/api/documents/{d}").json()["has_draft"]
    assert c.delete(f"/api/documents/{d}").status_code == 204
    assert c.get(f"/api/documents/{d}").status_code == 404
    assert git.show(repo, git.head(repo), "documents/manual-two.yaml") is None


def test_pin_update_available_bump_used_by(ws):
    c, d, fa, sid = ws["c"], ws["doc"], ws["fa"], ws["sid"]
    out = put(c, d, [ref(fa, sid, "commit", ws["v1"]), ref(fa, sid, "latest"), ref(fa, sid, "commit", ws["v2"])])
    b0 = out["blocks"][0]["id"]
    ups = {u["block_id"]: u for u in c.get(f"/api/documents/{d}/updates").json()}
    u = ups[b0]
    assert (u["update_available"], u["newer_commits"], u["resolved_sha"], u["latest_sha"]) == (True, 1, ws["v1"], ws["v2"])
    assert u["folder_name"] == "Security" and u["title"] == "Intro"
    assert [ups[b["id"]]["update_available"] for b in out["blocks"][1:]] == [False, False]  # latest, and current pin
    assert ups[out["blocks"][1]["id"]]["resolved_sha"] == ws["v2"]
    pv = c.get(f"/api/documents/{d}/blocks/{b0}/bump").json()
    assert pv["from_sha"] == ws["v1"] and pv["to_sha"] == ws["v2"] and "-" in pv["text"] and "new text" in pv["text"]
    assert pv["summary"]["changed"] == 1
    assert c.post(f"/api/documents/{d}/blocks/{out['blocks'][1]['id']}/bump").status_code == 409  # latest: nothing to bump
    r = c.post(f"/api/documents/{d}/blocks/{b0}/bump")
    assert r.status_code == 200 and r.json()["blocks"][0]["version"] == {"type": "commit", "value": ws["v2"]}
    assert not any(u["update_available"] for u in c.get(f"/api/documents/{d}/updates").json())
    assert c.get(f"/api/documents/{d}/blocks/nope/bump").status_code == 404
    # tags work as pins and resolve to the commit
    assert c.post(f"/api/folders/{fa}/tags", json={"name": "rel-1", "sha": ws["v1"]}).status_code == 201
    out = put(c, d, [ref(fa, sid, "tag", "rel-1")])
    u = c.get(f"/api/documents/{d}/updates").json()[0]
    assert u["resolved_sha"] == ws["v1"] and u["update_available"]
    # used-by: committed versions only
    assert c.get(f"/api/snippets/{sid}/used-by").json() == []
    put(c, d, [ref(fa, sid, "commit", ws["v1"])])
    s1 = c.post(f"/api/folders/{ws['fb']}/commit", json={"message": "a", "document_ids": [d]}).json()["sha"]
    bid = c.get(f"/api/documents/{d}").json()["blocks"][0]["id"]
    c.post(f"/api/documents/{d}/blocks/{bid}/bump")
    s2 = c.post(f"/api/folders/{ws['fb']}/commit", json={"message": "bump", "document_ids": [d]}).json()["sha"]
    ub = c.get(f"/api/snippets/{sid}/used-by").json()
    assert len(ub) == 1 and ub[0]["title"] == "Manual" and ub[0]["folder_name"] == "Product"
    assert {v["commit_sha"]: v["current"] for v in ub[0]["versions"]} == {s1: False, s2: True}
    assert {v["pin"]["value"] for v in ub[0]["versions"]} == {ws["v1"], ws["v2"]}


def test_reference_validation(ws, client):
    c, d, fa, sid = ws["c"], ws["doc"], ws["fa"], ws["sid"]
    bad = "f" * 40
    assert c.put(f"/api/documents/{d}/draft", json={"blocks": [ref(fa, sid, "commit", bad)]}).status_code == 404
    assert c.put(f"/api/documents/{d}/draft", json={"blocks": [ref(fa, sid, "tag", "nope")]}).status_code == 404
    assert c.put(f"/api/documents/{d}/draft", json={"blocks": [ref(ws["fb"], sid, "latest")]}).status_code == 404  # wrong folder
    assert c.put(f"/api/documents/{d}/draft", json={"blocks": [ref(fa, sid, "commit")]}).status_code == 422
    assert c.put(f"/api/documents/{d}/draft", json={"blocks": [ref(fa, sid, "latest", "x")]}).status_code == 422
    fresh = mk(c, fa, "Uncommitted")
    assert c.put(f"/api/documents/{d}/draft", json={"blocks": [ref(fa, fresh, "latest")]}).status_code == 422
    assert c.put(f"/api/documents/{d}/draft", json={"blocks": [{"type": "heading", "text": "x", "level": 9}]}).status_code == 422
    assert c.put(f"/api/documents/{d}/draft", json={"base_document": {"id": "00000000-0000-0000-0000-000000000000"}}).status_code == 404
    # another org's folder/snippet can't be referenced (404, indistinguishable from "does not exist")
    b = new_client()
    signup(b, "carol@b.co", "Org B")
    bf = b.post("/api/folders", json={"name": "Mine"}).json()["id"]
    bd = b.post("/api/documents", json={"folder_id": bf, "title": "D"}).json()["id"]
    r = b.put(f"/api/documents/{bd}/draft", json={"blocks": [ref(fa, sid, "latest")]})
    r2 = b.put(f"/api/documents/{bd}/draft", json={"blocks": [ref(fa, "00000000-0000-0000-0000-000000000000", "latest")]})
    assert r.status_code == r2.status_code == 404 and r.json() == r2.json()
    assert b.post("/api/documents", json={"folder_id": fa, "title": "x"}).status_code == 404


def test_base_document_pin(ws):
    c, d = ws["c"], ws["doc"]
    bid = upload(c, make_docx()).json()["id"]
    head = c.get(f"/api/base-documents/{bid}").json()["head"]["sha"]
    out = put(c, d, base_document={"id": bid})
    assert out["base_document"] == {"id": bid, "version": head}  # default pin = current head
    new = c.post(f"/api/base-documents/{bid}/versions", files={"file": ("n.docx", make_docx(lambda x: x.add_paragraph("2")), "x")})
    assert new.status_code == 201
    assert put(c, d, base_document={"id": bid, "version": head})["base_document"]["version"] == head  # stays pinned
    assert c.put(f"/api/documents/{d}/draft", json={"base_document": {"id": bid, "version": "a" * 40}}).status_code == 404


def test_library_search_groups_by_folder_and_tags(ws):
    c, fa, fb = ws["c"], ws["fa"], ws["fb"]
    other = mk(c, fb, "Pricing")
    save(c, other, "p")
    commit(c, fb, [other])
    mk(c, fb, "Draft only")  # uncommitted: not pinnable, not listed
    lib = c.get("/api/library/snippets").json()
    assert [(g["folder_name"], [s["title"] for s in g["snippets"]]) for g in lib] == [("Product", ["Pricing"]), ("Security", ["Intro"])]
    assert [g["folder_name"] for g in c.get("/api/library/snippets?q=intro").json()] == ["Security"]
    assert c.get("/api/library/snippets?q=zzz").json() == []
    c.post(f"/api/folders/{fa}/tags", json={"name": "v1.0", "sha": ws["v1"]})
    t = c.get("/api/library/snippets?tag=v1.0").json()
    assert [g["folder_name"] for g in t] == ["Security"] and t[0]["tags"] == ["v1.0"]
    assert c.get("/api/library/snippets?tag=nope").json() == []
    assert [g["folder_name"] for g in c.get(f"/api/library/snippets?folder_id={fb}").json()] == ["Product"]


def test_stale_draft_conflicts(ws, outbox, owner_engine):
    c, d, fb = ws["c"], ws["doc"], ws["fb"]
    ed = add_member(c, outbox, "ed@a.co", "editor")
    put(c, d, [])
    sha0 = c.post(f"/api/folders/{fb}/commit", json={"message": "init", "document_ids": [d]}).json()["sha"]
    put(c, d, [], title="Owner title")
    assert c.post(f"/api/folders/{fb}/commit", json={"message": "o", "document_ids": [d]}).status_code == 201
    c.post(f"/api/locks/documents/{d}/release")
    put(ed, d, [], title="Editor title")  # shared drafts: a stale one only exists from old data, rewound by hand
    with owner_engine.begin() as cn:
        cn.execute(__import__("sqlalchemy").text("update document_drafts set base_sha=:s where document_id=:i"), {"s": sha0, "i": d})
    r = ed.post(f"/api/folders/{fb}/commit", json={"message": "e", "document_ids": [d]})
    assert r.status_code == 409 and r.json()["detail"]["conflicts"][0]["document_id"] == d
    assert ed.delete(f"/api/documents/{d}/draft").status_code == 204


def test_document_roles(ws, outbox):
    c, d, fb = ws["c"], ws["doc"], ws["fb"]
    v = add_member(c, outbox, "v@a.co", "viewer")
    put(c, d, [])
    c.post(f"/api/folders/{fb}/commit", json={"message": "i", "document_ids": [d]})
    assert v.get(f"/api/documents/{d}").status_code == 200 and v.get(f"/api/documents/{d}/history").status_code == 200
    assert v.get(f"/api/documents/{d}/updates").status_code == 200 and v.get("/api/library/snippets").status_code == 200
    for r in (v.post("/api/documents", json={"folder_id": fb, "title": "x"}), v.put(f"/api/documents/{d}/draft", json={}),
              v.delete(f"/api/documents/{d}/draft"), v.delete(f"/api/documents/{d}"),
              v.post(f"/api/folders/{fb}/commit", json={"message": "m", "document_ids": [d]})):
        assert r.status_code == 403
