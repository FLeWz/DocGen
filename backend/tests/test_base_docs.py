"""Base documents: validation (non-docx, bombs, XXE, size), versions, placeholders, styles override, roles."""
import io
import os
import zipfile

import pytest
from docx import Document
from docx.enum.style import WD_STYLE_TYPE
from docx.shared import Pt, RGBColor

from .conftest import add_member, signup

DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def make_docx(mod=None, content=True) -> bytes:
    d = Document()
    if content:
        d.add_paragraph("{{ CONTENT }}")
    if mod:
        mod(d)
    b = io.BytesIO()
    d.save(b)
    return b.getvalue()


def rezip(data: bytes, replace: dict = {}, extra: dict = {}) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(data)) as zin, zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zout:
        for i in zin.infolist():
            zout.writestr(i.filename, replace.get(i.filename, zin.read(i)))
        for k, v in extra.items():
            zout.writestr(k, v)
    return out.getvalue()


def upload(c, data, name="Base", fn="b.docx"):
    return c.post("/api/base-documents", data={"name": name}, files={"file": (fn, data, DOCX)})


@pytest.fixture
def team(client, outbox):
    signup(client, "owner@x.co", "Acme")
    t = {r: add_member(client, outbox, f"{r}@x.co", r) for r in ("admin", "editor", "viewer")}
    t["owner"] = client
    return t


def test_default_base_doc_and_parsed_styles(team):
    a = team["admin"]
    r = a.post("/api/base-documents", data={"name": "Default"})
    assert r.status_code == 201, r.text
    head = r.json()["head"]
    assert head["number"] == 1 and head["placeholders"] == {"content": True, "slots": []} and len(head["sha"]) == 40
    d = a.get(f"/api/base-documents/{r.json()['id']}").json()
    ids = {s["style_id"] for s in d["styles"]}
    assert {"Normal", "Title", "Heading1", "Heading2", "Heading3", "Heading4", "TableGrid"} <= ids
    h1 = next(s for s in d["styles"] if s["style_id"] == "Heading1")
    assert h1["kind"] == "heading" and h1["level"] == 1 and h1["font_size"] == 18 and h1["color"] == "#1f3864"
    got = a.get(f"/api/base-documents/{r.json()['id']}/download")
    assert got.status_code == 200 and zipfile.is_zipfile(io.BytesIO(got.content))
    doc = Document(io.BytesIO(got.content))  # header/footer fields + both docxtpl-style tables + CONTENT
    assert len(doc.tables) == 2 and any(p.text == "{{ CONTENT }}" for p in doc.paragraphs)
    assert "PAGE" in doc.sections[0].footer._element.xml


def test_placeholders_and_slots_detected(team):
    def mod(d):
        d.add_paragraph("{{ SLOT:appendix }}")
        d.add_paragraph("{{SLOT:notes}}")
        d.add_paragraph("not {{ SLOT:inline }} alone")
    r = upload(team["admin"], make_docx(mod))
    assert r.json()["head"]["placeholders"] == {"content": True, "slots": ["appendix", "notes"]}
    r = upload(team["admin"], make_docx(content=False), "NoContent")
    assert r.status_code == 201 and r.json()["head"]["placeholders"]["content"] is False  # reported, not rejected


def test_rejects_non_docx(team):
    a = team["admin"]
    assert upload(a, b"hello, not a zip").status_code == 422
    assert upload(a, rezip(make_docx(), extra={"x": "y"}).replace(b"word/document.xml", b"word/documenX.xml")).status_code == 422
    plain = io.BytesIO()
    with zipfile.ZipFile(plain, "w") as z:
        z.writestr("a.txt", "x")
    assert upload(a, plain.getvalue()).status_code == 422
    macro = rezip(make_docx(), extra={"word/vbaProject.bin": b"x"})
    assert upload(a, macro).status_code == 422
    assert a.get("/api/base-documents").json() == []  # nothing half-created


def test_rejects_zip_bomb_and_oversize(team):
    a = team["admin"]
    bomb = rezip(make_docx(), extra={"word/bomb.bin": b"\0" * (60 * 1024 * 1024)})
    assert len(bomb) < 200_000
    r = upload(a, bomb)
    assert r.status_code == 422 and "zip bomb" in r.json()["detail"]
    many = rezip(make_docx(), extra={f"word/f{i}.txt": "x" for i in range(2100)})
    assert upload(a, many).status_code == 422
    assert upload(a, os.urandom(26 * 1024 * 1024)).status_code == 413


def test_rejects_xxe_and_dtd(team):
    a = team["admin"]
    evil = (b'<?xml version="1.0"?><!DOCTYPE s [<!ENTITY x SYSTEM "file:///etc/passwd">]>'
            b'<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">&x;</w:styles>')
    r = upload(a, rezip(make_docx(), {"word/styles.xml": evil}))
    assert r.status_code == 422 and "unsafe" in r.json()["detail"]
    bomb = (b'<?xml version="1.0"?><!DOCTYPE l [<!ENTITY a "aaaa"><!ENTITY b "&a;&a;&a;&a;">]><r>&b;</r>')
    assert upload(a, rezip(make_docx(), extra={"word/evil.xml": bomb})).status_code == 422  # billion-laughs shape
    assert a.get("/api/base-documents").json() == []


def test_versions_history_download(team):
    a = team["admin"]
    v1 = make_docx()
    bid = upload(a, v1, "Corp").json()["id"]
    v2 = make_docx(lambda d: d.add_paragraph("second"))
    r = a.post(f"/api/base-documents/{bid}/versions", data={"message": "tweak"}, files={"file": ("b2.docx", v2, DOCX)})
    assert r.status_code == 201 and r.json()["number"] == 2
    vs = a.get(f"/api/base-documents/{bid}/versions").json()
    assert [v["number"] for v in vs] == [2, 1] and vs[0]["message"] == "tweak"
    assert a.get(f"/api/base-documents/{bid}").json()["head"]["sha"] == vs[0]["sha"]
    old = a.get(f"/api/base-documents/{bid}/download", params={"version": vs[1]["sha"]}).content
    assert old == v1 and a.get(f"/api/base-documents/{bid}/download").content == v2
    assert a.get(f"/api/base-documents/{bid}/download", params={"version": "0" * 40}).status_code == 404
    assert upload(a, b"junk").status_code == 422
    assert a.post(f"/api/base-documents/{bid}/versions", files={"file": ("x.docx", b"junk", DOCX)}).status_code == 422


def test_base_styles_override_org_styles(team):
    def mod(d):
        h = d.styles["Heading 1"]
        h.font.color.rgb, h.font.size = RGBColor(0xAA, 0, 0), Pt(30)
        c = d.styles.add_style("Callout", WD_STYLE_TYPE.PARAGRAPH)
        c.font.bold = True
    a = team["admin"]
    bid = upload(a, make_docx(mod)).json()["id"]
    org = {s["style_id"]: s for s in a.get("/api/styles").json()}
    st = {s["style_id"]: s for s in a.get(f"/api/styles?base_doc={bid}").json()}
    assert org["Heading1"]["color"] == "#1f3864" and st["Heading1"]["color"] == "#aa0000" and st["Heading1"]["font_size"] == 30
    assert "Callout" in st and "Callout" not in org and st["Callout"]["bold"]
    assert "Quote" in st  # org styles fill ids the base doc lacks
    assert a.get("/api/styles?base_doc=00000000-0000-0000-0000-000000000000").status_code == 404


def test_base_doc_roles(team):
    data = make_docx()
    for r in ("editor", "viewer"):
        assert upload(team[r], data).status_code == 403
        assert team[r].get("/api/base-documents").status_code == 200
    bid = upload(team["admin"], data).json()["id"]
    for r in ("editor", "viewer"):
        assert team[r].post(f"/api/base-documents/{bid}/versions", files={"file": ("x.docx", data, DOCX)}).status_code == 403
        assert team[r].get(f"/api/base-documents/{bid}/download").status_code == 200
        assert team[r].get(f"/api/base-documents/{bid}/versions").status_code == 200
