"""Round 13: download a committed document version (as of that commit) + render cache."""
import io
import re
import threading
import time

from docx import Document

from app import export, files

from .conftest import add_member
from .test_documents import put, ref, ws  # noqa: F401  (ws is a fixture)
from .test_git import commit, save


def text_of(data: bytes) -> str:
    d = Document(io.BytesIO(data))
    return "\n".join([p.text for p in d.paragraphs] + [c.text for t in d.tables for r in t.rows for c in r.cells])


def vexport(c, did, sha, fmt="docx"):
    r = c.get(f"/api/documents/{did}/versions/{sha}/export", params={"format": fmt})
    assert r.status_code == 202, r.text
    j = r.json()
    for _ in range(200):
        st = c.get(f"/api/exports/{j['export_id']}").json()
        if st["status"] in ("done", "error"):
            break
        time.sleep(0.05)
    assert st["status"] == "done", st
    return j, st, c.get(f"/api/exports/{j['export_id']}/download").content


def doc_commit(c, ws, msg, blocks, **kw):
    put(c, ws["doc"], blocks, base_document=None, **kw)
    r = c.post(f"/api/folders/{ws['fb']}/commit", json={"message": msg, "document_ids": [ws["doc"]]})
    assert r.status_code == 201, r.text
    return r.json()["sha"]


def two_commits(ws):
    c = ws["c"]
    a = doc_commit(c, ws, "first doc", [ref(ws["fa"], ws["sid"], "commit", ws["v1"])],
                   variables={"declarations": [{"name": "client", "default": "Acme"}], "values": {"client": "Globex"}})
    b = doc_commit(c, ws, "second doc", [ref(ws["fa"], ws["sid"], "commit", ws["v2"])],
                   variables={"declarations": [{"name": "client", "default": "Acme"}], "values": {"client": "Initech"}})
    return a, b


def test_old_version_uses_its_own_pins_variables_and_builtins(ws):
    c = ws["c"]
    a, b = two_commits(ws)
    j, st, old = vexport(c, ws["doc"], a)
    _, st2, new = vexport(c, ws["doc"], b)
    to, tn = text_of(old), text_of(new)
    assert "old text" in to and "new text" not in to and "new text" in tn
    assert f"Version {a[:7]} " in to and f"Version {b[:7]} " in tn and "Alice A" in to  # doc.version = short sha, author = committer
    assert re.search(r"Version \w+ · \d\d\.\d\d\.\d{4} · Alice A", to)
    assert st["variables"]["values"] == {"client": "Globex"} and st2["variables"]["values"] == {"client": "Initech"}
    assert st["pins"]["document"]["head_sha"] == a and st["pins"]["snippets"][0]["sha"] == ws["v1"]
    assert "first doc" in to and "second doc" not in to and "second doc" in tn  # history only up to that commit
    assert not j["cached"]
    assert len(c.get(f"/api/documents/{ws['doc']}/exports").json()) == 2  # recorded


def test_cache_hit_miss_after_tag_and_tag_in_revision_table(ws):
    c = ws["c"]
    a, b = two_commits(ws)
    j1, _, d1 = vexport(c, ws["doc"], a)
    j2, _, d2 = vexport(c, ws["doc"], a)
    assert (j1["cached"], j2["cached"]) == (False, True) and d1 == d2 and j1["export_id"] != j2["export_id"]
    assert vexport(c, ws["doc"], a, "pdf")[0]["cached"] is False  # other format = other entry
    assert vexport(c, ws["doc"], b)[0]["cached"] is False
    assert c.post(f"/api/folders/{ws['fb']}/tags", json={"name": "rel-1", "sha": a}).status_code == 201
    j3, _, d3 = vexport(c, ws["doc"], a)
    assert j3["cached"] is False and "rel-1" in text_of(d3) and f"Version rel-1 " in text_of(d3) and d3 != d1
    assert vexport(c, ws["doc"], a)[0]["cached"] is True
    assert "rel-1" in text_of(vexport(c, ws["doc"], b)[2])  # b's history includes the tagged commit: its entry is stale too
    assert vexport(c, ws["doc"], b)[0]["cached"] is True


def test_latest_pin_is_not_stale_after_snippet_commit(ws):
    c = ws["c"]
    a = doc_commit(c, ws, "latest doc", [ref(ws["fa"], ws["sid"], "latest")])
    assert vexport(c, ws["doc"], a)[0]["cached"] is False and vexport(c, ws["doc"], a)[0]["cached"] is True
    save(c, ws["sid"], "newest text")
    commit(c, ws["fa"], [ws["sid"]], "v3")
    _, _, data = vexport(c, ws["doc"], a)  # a `latest` pin resolves to the snippet's HEAD at render time
    assert "newest text" in text_of(data)
    assert vexport(c, ws["doc"], a)[0]["cached"] is True


def test_concurrent_requests_share_one_render(ws, monkeypatch):
    c = ws["c"]
    a, _ = two_commits(ws)
    calls, real = [], export.RENDERERS["docx"]

    def slow(org, spec):
        calls.append(1)
        time.sleep(0.6)
        return real(org, spec)
    monkeypatch.setitem(export.RENDERERS, "docx", slow)
    out = []
    ts = [threading.Thread(target=lambda: out.append(c.get(f"/api/documents/{ws['doc']}/versions/{a}/export").json())) for _ in range(3)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert len({o["export_id"] for o in out}) == 1, out
    for _ in range(100):
        if c.get(f"/api/exports/{out[0]['export_id']}").json()["status"] == "done":
            break
        time.sleep(0.1)
    assert len(calls) == 1


def test_errors_roles_and_path_safety(ws):
    c = ws["c"]
    a, _ = two_commits(ws)
    viewer = add_member(c, ws["outbox"], "v@x.co", "viewer")
    assert vexport(viewer, ws["doc"], a)[1]["status"] == "done"  # viewers can download
    u = f"/api/documents/{ws['doc']}/versions"
    assert c.get(f"{u}/{'0' * 40}/export").status_code == 404
    assert c.get(f"{u}/{ws['v1']}/export").status_code == 404  # a snippet commit is not a version of this document
    assert c.get(f"{u}/{a[:7]}/export").status_code == 422 and c.get(f"{u}/..%2f..%2fetc/export").status_code in (404, 422)
    assert c.get(f"{u}/{a}/export", params={"format": "exe"}).status_code == 400
    assert c.get(f"/api/documents/{'0' * 8}-0000-0000-0000-{'0' * 12}/versions/{a}/export").status_code == 404
    with __import__("pytest").raises(AssertionError):
        files.cache_path(ws["org"], ws["doc"], "../" * 20, "0" * 64, "docx")
    p = files.cache_path(ws["org"], ws["doc"], a, "ab" * 32, "pdf")
    assert p.is_relative_to(files.ROOT / ws["org"] / "cache" / ws["doc"])


def test_missing_variables_is_422_and_cache_is_pruned(ws, monkeypatch):
    c = ws["c"]
    a = doc_commit(c, ws, "needs var", [ref(ws["fa"], ws["sid"], "commit", ws["v1"])],
                   variables={"declarations": [{"name": "client", "type": "text"}], "values": {}})
    r = c.get(f"/api/documents/{ws['doc']}/versions/{a}/export")
    assert r.status_code == 422 and r.json()["detail"]["variables"][0]["name"] == "client"
    d = files.cache_dir(ws["org"], ws["doc"])
    d.mkdir(parents=True, exist_ok=True)
    for i in range(4):
        (d / f"{'a' * 39}{i}-{'b' * 32}.docx").write_bytes(b"x")
        time.sleep(0.01)
    monkeypatch.setattr(export, "CACHE_KEEP", 2)
    export.cache_prune(ws["org"], ws["doc"])
    assert len(list(d.glob("*.docx"))) == 2
