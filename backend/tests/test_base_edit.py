"""Round 9A: editing a base document in the import editor: session per user, autosave, diff, commit as a new version
(author, placeholders), conflict, discard, restore, builds keep their pin until bumped, roles and isolation."""
import io
import subprocess

from docx import Document

from app.git import repo as git

from .test_base_docs import DOCX, upload
from .test_documents import put, ref
from .test_export import fetch, run_export
from .test_import import SAMPLE, team, zread  # noqa: F401  (team is a fixture)
from .test_isolation import world  # noqa: F401  (fixture)
from .test_git import mk
from .fixtures.sample import build


def base_with(admin, name="Manual base"):
    """A base document made from the sample file (header/footer, styles, tables) with a {{ CONTENT }} paragraph and a variable."""
    def mod(d):
        d.add_paragraph("{{ CONTENT }}")
        d.add_paragraph("Prepared for {{ client }}")
    import io as _io
    d = Document(_io.BytesIO(build()))
    mod(d)
    b = _io.BytesIO()
    d.save(b)
    r = upload(admin, b.getvalue(), name)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def open_edit(admin, bid):
    r = admin.post(f"/api/base-documents/{bid}/edit")
    assert r.status_code == 200, r.text
    return r.json()


def test_edit_commit_new_version_and_builds_keep_their_pin(team):
    admin, ed = team["admin"], team["editor"]
    bid = base_with(admin)
    v1 = admin.get(f"/api/base-documents/{bid}/versions").json()[0]
    fo = admin.post("/api/folders", json={"name": "F"}).json()["id"]
    did = admin.post("/api/documents", json={"folder_id": fo, "title": "Doc"}).json()["id"]
    put(admin, did, [], base_document={"id": bid}, variables={"declarations": [], "values": {"client": "Acme"}})
    s = open_edit(admin, bid)
    assert s["created"] and not s["stale"] and open_edit(admin, bid) == {**s, "created": False}  # resumes, no second session
    iid = s["import_id"]
    imp = admin.get(f"/api/imports/{iid}").json()
    blocks = imp["content"]["content"]
    assert any("{{ CONTENT }}" in str(b) for b in blocks) and imp["title"].startswith("Edit: ")
    info = admin.get(f"/api/imports/{iid}/base-edit").json()
    assert info["diff"] == {**info["diff"], "added": 0, "removed": 0, "changed": 0} and not info["stale"] and info["started_number"] == 1
    # add a paragraph before {{ CONTENT }} and change a heading
    k = next(i for i, b in enumerate(blocks) if "{{ CONTENT }}" in str(b))
    blocks.insert(k, {"type": "paragraph", "attrs": {"styleId": None}, "content": [{"type": "text", "text": "Added by round 9 edit"}]})
    assert admin.put(f"/api/imports/{iid}", json={"content": imp["content"]}).status_code == 200
    d = admin.get(f"/api/imports/{iid}/base-edit").json()["diff"]
    assert d["added"] == 1 and d["removed"] == 0 and d["changed"] == 0 and d["blocks"][0]["text"] == "Added by round 9 edit"
    # editors cannot commit; other users cannot even see the draft
    assert ed.post(f"/api/imports/{iid}/base-commit", json={"message": "x"}).status_code == 403
    assert ed.get(f"/api/imports/{iid}").status_code == 404
    assert admin.post(f"/api/imports/{iid}/base-commit", json={"message": ""}).status_code == 422
    r = admin.post(f"/api/imports/{iid}/base-commit", json={"message": "Add a paragraph"})
    assert r.status_code == 201, r.text
    out = r.json()
    assert out["version"] == 2 and out["placeholders"]["content"] and "client" in [v["name"] for v in out["variables"]]
    # a new version, same name, authored by the committing user; the draft is gone
    vs = admin.get(f"/api/base-documents/{bid}/versions").json()
    assert [v["number"] for v in vs] == [2, 1] and vs[0]["message"] == "Add a paragraph"
    assert admin.get("/api/base-documents").json()[0]["name"] == "Manual base"
    assert admin.get(f"/api/imports/{iid}").status_code == 404
    log = subprocess.run(["git", "-C", str(git.base_repo_path(admin.get("/api/orgs/current").json()["id"])), "log", "--format=%an <%ae>|%s"], capture_output=True, text=True).stdout.splitlines()
    assert log[0].endswith("|Add a paragraph") and log[0].split(" <")[0] != "" and "@" in log[0]
    new = admin.get(f"/api/base-documents/{bid}/download").content
    assert b"Added by round 9 edit" in zread(new, "word/document.xml") and b"{{ CONTENT }}" in zread(new, "word/document.xml")
    assert b"HEADER-MARKER" in zread(new, "word/header1.xml")  # package parts survive
    # unedited blocks come back as their original XML: the sample's tables are untouched in the new version
    old = admin.get(f"/api/base-documents/{bid}/download?version={v1['sha']}").content
    assert zread(old, "word/styles.xml") == zread(new, "word/styles.xml") and zread(old, "word/numbering.xml") == zread(new, "word/numbering.xml")
    assert zread(old, "word/document.xml").count(b"<w:tbl>") == zread(new, "word/document.xml").count(b"<w:tbl>")
    # the document still pins v1: its export has no new paragraph; after bumping to v2 it does
    eid, st = run_export(admin, did)
    assert st["status"] == "done" and b"Added by round 9 edit" not in zread(fetch(admin, eid), "word/document.xml")
    put(admin, did, [], base_document={"id": bid, "version": vs[0]["sha"]}, variables={"declarations": [], "values": {"client": "Acme"}})
    eid, st = run_export(admin, did)
    assert b"Added by round 9 edit" in zread(fetch(admin, eid), "word/document.xml")


def test_conflict_discard_override_and_restore(team):
    admin = team["admin"]
    bid = base_with(admin)
    v1 = admin.get(f"/api/base-documents/{bid}/versions").json()[0]
    iid = open_edit(admin, bid)["import_id"]
    # somebody uploads version 2 meanwhile
    files = {"file": ("b.docx", build(), DOCX)}
    assert team["owner"].post(f"/api/base-documents/{bid}/versions", files=files, data={"message": "Other change"}).status_code == 201
    assert admin.get(f"/api/imports/{iid}/base-edit").json()["stale"]
    assert open_edit(admin, bid)["stale"]
    r = admin.post(f"/api/imports/{iid}/base-commit", json={"message": "mine"})
    assert r.status_code == 409 and r.json()["detail"]["error"] == "base_changed" and r.json()["detail"]["head_number"] == 2
    assert admin.get(f"/api/base-documents/{bid}/versions").json()[0]["number"] == 2  # nothing was written
    r = admin.post(f"/api/imports/{iid}/base-commit", json={"message": "mine", "override": True})
    assert r.status_code == 201 and r.json()["version"] == 3
    # discard: a new session starts from the head again; restore v1 into the draft and commit it as v4
    iid = open_edit(admin, bid)["import_id"]
    assert admin.delete(f"/api/imports/{iid}").status_code == 204
    s = open_edit(admin, bid)
    assert s["created"]
    assert admin.post(f"/api/imports/{s['import_id']}/base-restore", json={"sha": "0" * 40}).status_code == 404
    assert admin.post(f"/api/imports/{s['import_id']}/base-restore", json={"sha": v1["sha"]}).status_code == 200
    assert admin.post(f"/api/imports/{s['import_id']}/base-commit", json={"message": "Restore v1"}).json()["version"] == 4
    assert admin.get(f"/api/imports").json() == []  # sessions are not in the import list


def test_roles_and_isolation_of_base_edit(team):
    admin, ed, viewer = team["admin"], team["editor"], team["viewer"]
    bid = base_with(admin)
    for c in (ed, viewer):
        assert c.post(f"/api/base-documents/{bid}/edit").status_code == 403
    iid = open_edit(admin, bid)["import_id"]
    for c in (ed, viewer):
        assert c.get(f"/api/imports/{iid}/base-edit").status_code in (403, 404)
        assert c.post(f"/api/imports/{iid}/base-restore", json={"sha": "0" * 40}).status_code in (403, 404)
    assert team["owner"].get(f"/api/imports/{iid}").status_code == 404  # per user, also for the owner
    assert admin.post(f"/api/imports/{iid}/base-edit").status_code == 405
    # a plain import is not a session
    from .test_import import imp
    plain = imp(ed).json()["id"]
    assert admin.get(f"/api/imports/{plain}/base-edit").status_code == 404


def test_b_cannot_touch_a_base_edit(world):
    a, b = world["a"], world["b"]
    bid = base_with(a)
    iid = open_edit(a, bid)["import_id"]
    calls = [("post", f"/api/base-documents/{bid}/edit", None), ("get", f"/api/imports/{iid}/base-edit", None),
             ("post", f"/api/imports/{iid}/base-commit", {"message": "x"}), ("post", f"/api/imports/{iid}/base-restore", {"sha": "0" * 40})]
    for method, url, body in calls:
        r = getattr(b, method)(url, **({"json": body} if body is not None else {}))
        assert r.status_code == 404, (method, url, r.status_code)
    assert a.get(f"/api/imports/{iid}/base-edit").status_code == 200
