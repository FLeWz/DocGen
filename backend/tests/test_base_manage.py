"""Base documents: unique names (409 name_exists), rename, delete (refused while pinned), usage, roles."""
import pytest
from sqlalchemy import text

from app.git import repo as git

from .test_base_docs import DOCX, make_docx, team, upload  # noqa: F401
from .test_documents import put


def mkdoc(c, bid, title="D"):
    fo = c.post("/api/folders", json={"name": f"F-{title}"}).json()["id"]
    did = c.post("/api/documents", json={"folder_id": fo, "title": title}).json()["id"]
    put(c, did, base_document={"id": bid})
    return fo, did


def test_name_unique_case_insensitive_and_trimmed(team):
    a = team["admin"]
    first = upload(a, make_docx(), "Corporate").json()
    for n in ("corporate", "  CORPORATE ", "Corporate"):
        r = upload(a, make_docx(), n)
        assert r.status_code == 409, n
        assert r.json()["error"] == "name_exists" and r.json()["base_document_id"] == first["id"]
        assert r.json()["name"] == "Corporate" and r.json()["current_version"] == 1
    assert a.post("/api/base-documents", data={"name": "corporate"}).status_code == 409  # default template too
    assert len(a.get("/api/base-documents").json()) == 1  # nothing half-created
    assert upload(a, make_docx(), "Corporate 2").status_code == 201
    assert upload(a, make_docx(), "   ").status_code == 422


def test_rename_and_new_version_path(team):
    a = team["admin"]
    b1, b2 = upload(a, make_docx(), "One").json()["id"], upload(a, make_docx(), "Two").json()["id"]
    r = a.patch(f"/api/base-documents/{b2}", json={"name": "one"})
    assert r.status_code == 409 and r.json()["base_document_id"] == b1
    assert a.patch(f"/api/base-documents/{b1}", json={"name": "ONE"}).json()["name"] == "ONE"  # own name, other case
    assert a.patch(f"/api/base-documents/{b2}", json={"name": " Three "}).json()["name"] == "Three"
    assert a.patch(f"/api/base-documents/{b2}", json={"name": "  "}).status_code == 422
    # the UI's "create new version instead" = POST versions on the id from the 409
    cid = upload(a, make_docx(), "ONE").json()["base_document_id"]
    v = a.post(f"/api/base-documents/{cid}/versions", files={"file": ("x.docx", make_docx(), DOCX)})
    assert v.status_code == 201 and v.json()["number"] == 2


def test_use_as_base_conflict(team):
    from .test_import import imp
    a = team["admin"]
    bid = upload(a, make_docx(), "Corporate").json()["id"]
    iid = imp(team["editor"]).json()["id"]
    r = a.post(f"/api/imports/{iid}/base-document", json={"name": "corporate"})
    assert r.status_code == 409 and r.json()["base_document_id"] == bid and r.json()["current_version"] == 1
    assert a.post(f"/api/imports/{iid}/base-document", json={"base_document_id": bid}).json()["version"] == 2


def test_list_has_counts_and_usage(team):
    a = team["admin"]
    bid = upload(a, make_docx(), "Corp").json()["id"]
    unused = upload(a, make_docx(), "Spare").json()["id"]
    assert [x["used_by"] for x in a.get("/api/base-documents").json()] == [0, 0]
    fo, did = mkdoc(team["editor"], bid)
    row = next(x for x in a.get("/api/base-documents").json() if x["id"] == bid)
    assert row["used_by"] == 1 and row["version_count"] == 1 and row["created_at"] and row["updated_at"]
    u = team["viewer"].get(f"/api/base-documents/{bid}/usage").json()
    assert u["documents"][0]["state"] == "draft" and u["documents"][0]["document_id"] == did and u["exports"] == 0
    assert team["viewer"].get(f"/api/base-documents/{unused}/usage").json() == {"documents": [], "exports": 0}


def test_delete_refused_while_pinned_then_allowed(team):
    a, ed = team["admin"], team["editor"]
    bid = upload(a, make_docx(), "Corp").json()["id"]
    fo, did = mkdoc(ed, bid)
    r = a.delete(f"/api/base-documents/{bid}")  # a draft pins it
    assert r.status_code == 409 and r.json()["detail"]["documents"][0]["document_id"] == did
    assert ed.post(f"/api/folders/{fo}/commit", json={"message": "m", "document_ids": [did]}).status_code == 201
    assert a.delete(f"/api/base-documents/{bid}").status_code == 409  # now HEAD pins it
    put(ed, did, base_document=None)  # unpin in the draft; HEAD still pins
    assert a.delete(f"/api/base-documents/{bid}").status_code == 409
    assert ed.post(f"/api/folders/{fo}/commit", json={"message": "m2", "document_ids": [did]}).status_code == 201
    org = a.get("/api/orgs/current").json()["id"]
    repo = git.base_repo_path(org)
    before = git.head(repo)
    assert a.delete(f"/api/base-documents/{bid}").status_code == 204
    assert a.get(f"/api/base-documents/{bid}").status_code == 404 and a.get("/api/base-documents").json() == []
    after = git.head(repo)
    assert after != before and git.show(repo, after, f"base/{bid}.docx") is None
    assert git.show(repo, before, f"base/{bid}.docx")  # history kept
    assert a.delete(f"/api/base-documents/{bid}").status_code == 404
    assert upload(a, make_docx(), "corp").status_code == 201  # the name is free again


def test_delete_keeps_other_docs_files_and_exports_render(team):
    a = team["admin"]
    keep, gone = upload(a, make_docx(), "Keep").json()["id"], upload(a, make_docx(), "Gone").json()["id"]
    assert a.delete(f"/api/base-documents/{gone}").status_code == 204
    repo = git.base_repo_path(a.get("/api/orgs/current").json()["id"])
    assert git.show(repo, git.head(repo), f"base/{keep}.docx")
    assert a.get(f"/api/base-documents/{keep}/download").status_code == 200


def test_roles(team):
    a = team["admin"]
    bid = upload(a, make_docx(), "Corp").json()["id"]
    for r in ("editor", "viewer"):
        c = team[r]
        assert c.patch(f"/api/base-documents/{bid}", json={"name": "x"}).status_code == 403
        assert c.delete(f"/api/base-documents/{bid}").status_code == 403
        assert c.get(f"/api/base-documents/{bid}/usage").status_code == 200
    assert team["owner"].delete(f"/api/base-documents/{bid}").status_code == 204


def test_migration_dedupes_existing_names(owner_engine):
    """The 0007 data step: later duplicates (any case/whitespace) get ' (n)', none are deleted."""
    import importlib.util
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    org = "00000000-0000-0000-0000-0000000000b1"
    spec = importlib.util.spec_from_file_location("m7", "alembic/versions/0007_base_doc_unique_names.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    with owner_engine.begin() as c:
        c.execute(text("drop index uq_base_documents_org_name"))
        c.execute(text("insert into organizations (id, name) values (:o, 'O')"), {"o": org})
        for i, n in enumerate(("Corp", "corp ", "CORP", "Other")):
            c.execute(text("insert into base_documents (id, org_id, name, created_at) values (gen_random_uuid(), :o, :n, now() + :i * interval '1 second')"),
                      {"o": org, "n": n, "i": i})
        with Operations.context(MigrationContext.configure(c)):
            mod.upgrade()  # also recreates the index
        assert sorted(r[0] for r in c.execute(text("select name from base_documents"))) == ["CORP (3)", "Corp", "Other", "corp (2)"]
        c.execute(text("delete from base_documents"))
