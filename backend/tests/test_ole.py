"""Embedded documents (OLE objects): kept byte for byte through import/export, no placeholder text, carried by snippets,
dangerous ones neutralised (round 9)."""
import io
import zipfile

from docx import Document
from pypdf import PdfReader

from app import docx_import as D
from app.base_docs import validate
from app.ooxml import neutralize_ole

from .fixtures.ole import build
from .test_documents import put, ref
from .test_export import fetch, run_export
from .test_git import commit
from .test_import import export, folder, imp, team, zread  # noqa: F401  (team is a fixture)


def names(data):
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        return {n: z.read(n) for n in z.namelist()}


def test_embedded_document_is_kept_as_is(team):
    src = build()
    ed = team["editor"]
    r = imp(ed, src)
    assert r.status_code == 201, r.text
    assert not any("dropped" in w or "blocked" in w for w in r.json()["warnings"])
    assert any("embedded document kept as is" in w for w in r.json()["warnings"])
    iid = r.json()["id"]
    blocks = ed.get(f"/api/imports/{iid}").json()["content"]
    obj = next(b for b in blocks["content"] if b["type"] == "opaque")
    assert obj["attrs"]["kind"] == "embedded object" and "OLEObject" in obj["attrs"]["xml"]
    assert "removed" not in str(blocks)
    # edit another paragraph, export: the embedded part and its relationship are byte-identical
    blocks["content"][0]["content"][0]["text"] = "Edited before"
    assert ed.put(f"/api/imports/{iid}", json={"content": blocks}).status_code == 200
    out = export(ed, iid)
    a, b = names(src), names(out)
    for n in ("word/embeddings/oleObject1.bin", "word/media/preview1.png"):
        assert a[n] == b[n], n
    assert b"OLEObject" in b["word/document.xml"] and b"Edited before" in b["word/document.xml"]
    assert b"oleObject1.bin" in b["word/_rels/document.xml.rels"]


def test_snippet_with_the_object_exports_into_a_document(team):
    ed = team["editor"]
    iid = imp(ed, build()).json()["id"]
    fo = folder(ed)
    blocks = ed.get(f"/api/imports/{iid}").json()["content"]["content"]
    k = next(i for i, b in enumerate(blocks) if b["type"] == "opaque")
    r = ed.post(f"/api/imports/{iid}/selection-snippet", json={"folder_id": fo, "title": "Obj", "start": k, "end": k + 1})
    assert r.status_code == 201 and not r.json()["warnings"], r.text
    sid = r.json()["id"]
    assert ed.get(f"/api/snippets/{sid}/draft").json()["content"]["content"][0]["attrs"]["parts"]
    sha = commit(ed, fo, [sid]).json()["sha"]
    did = ed.post("/api/documents", json={"folder_id": fo, "title": "Doc"}).json()["id"]
    put(ed, did, [ref(fo, sid, "commit", sha), ref(fo, sid, "commit", sha)])  # twice: unique part names
    eid, st = run_export(ed, did)
    assert st["status"] == "done", st
    out = names(fetch(ed, eid))
    emb = [n for n in out if n.startswith("word/embeddings/")]
    assert len(emb) == 2 and out[emb[0]] == names(build())["word/embeddings/oleObject1.bin"]
    assert out["word/document.xml"].count(b"OLEObject") == 2
    rels = out["word/_rels/document.xml.rels"].decode()
    assert all(n.split("word/")[1] in rels for n in emb)
    assert "oleObject" in out["[Content_Types].xml"].decode()
    Document(io.BytesIO(fetch(ed, eid)))


def test_pdf_has_no_placeholder(team):
    ed = team["editor"]
    iid = imp(ed, build()).json()["id"]
    fo = folder(ed)
    blocks = ed.get(f"/api/imports/{iid}").json()["content"]["content"]
    k = next(i for i, b in enumerate(blocks) if b["type"] == "opaque")
    sid = ed.post(f"/api/imports/{iid}/selection-snippet", json={"folder_id": fo, "title": "Obj", "start": k, "end": k + 1}).json()["id"]
    sha = commit(ed, fo, [sid]).json()["sha"]
    did = ed.post("/api/documents", json={"folder_id": fo, "title": "Doc"}).json()["id"]
    put(ed, did, [ref(fo, sid, "commit", sha)])
    eid, st = run_export(ed, did, "pdf")
    assert st["status"] == "done", st
    pdf = PdfReader(io.BytesIO(fetch(ed, eid)))
    assert "embedded object removed" not in "\n".join(p.extract_text() for p in pdf.pages)
    text = "\n".join(p.extract_text() for p in pdf.pages)
    assert "embedded object removed" not in text and "not allowed" not in text and "CODE-BLOCK-MARKER" in text
    # (LibreOffice only draws the preview when the embedded part is a real OLE file; the real manual is checked locally)


def test_dangerous_objects_are_neutralised_preview_stays(team):
    cases = {
        "macros": build(blob=b"PK\x03\x04 word/vbaProject.bin"),
        "exe": build(blob=b"MZ\x90\x00" + b"\0" * 100),
        "package": build(prog="Package"),
        "link": build(typ="Link", external=True),
        "external": build(external=True),
    }
    for what, data in cases.items():
        out, warn = neutralize_ole(data)
        assert warn and "preview picture is kept" in warn[0], what
        parts = names(out)
        assert b"OLEObject" not in parts["word/document.xml"] and b"imagedata" in parts["word/document.xml"], what
        assert not any(n.startswith("word/embeddings/") for n in parts), what
        validate(out)
        r = imp(team["editor"], data)  # through the upload path: a warning in the list, nothing in the page
        assert r.status_code == 201 and any("dropped" in w for w in r.json()["warnings"]), what
        assert "removed" not in str(team["editor"].get(f"/api/imports/{r.json()['id']}").json()["content"])
    ok = build()
    assert neutralize_ole(ok) == (ok, [])  # normal ones: same bytes


def test_dde_field_is_blocked_without_visible_text():
    ns = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
    doc = f'<w:document {ns}><w:body><w:p><w:r><w:instrText> DDEAUTO calc </w:instrText></w:r></w:p></w:body></w:document>'
    from .test_base_docs import rezip
    content, warn = D.parse(rezip(build(), replace={"word/document.xml": doc.encode()}), lambda b: None)
    assert "content" not in content["content"][0] and any("blocked" in w for w in warn)


def test_forged_parts_are_revalidated(team):
    from app.docx_export import Builder
    from docx import Document as Doc
    a = {"kind": "embedded object", "xml": '<w:p xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><w:r><w:object><v:shape xmlns:v="urn:schemas-microsoft-com:vml"><v:imagedata r:id="x1"/></v:shape></w:object></w:r></w:p>',
         "parts": [{"rid": "x1", "kind": "image", "ext": "exe", "b64": "AAAA"}]}
    warn: list = []
    Builder(Doc(), None, warn).opaque(a, None)
    assert any("skipped" in w for w in warn)
