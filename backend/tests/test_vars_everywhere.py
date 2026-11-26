"""Round 22: `{{ document_title }}` (the name imported covers use) resolves everywhere the export writes text: headings, TOC entries,
captions, headers, footers, text boxes; a declared variable of the same name wins; anything left over is reported with its location."""
import io
import zipfile

import pytest
from docx import Document
from docx.oxml import parse_xml
from docx.oxml.ns import qn

from app import docx_export

from .test_base_docs import make_docx
from .test_sections import H, W
from .test_export import P, T

PH = "{{ document_title }}"


def base():
    def mod(d):
        d.sections[0].header.paragraphs[0].text = f"head {PH}"
        d.sections[0].footer.paragraphs[0].text = f"foot {PH} v{{{{ document_version }}}}"
        d.add_paragraph("x").add_run()._r.append(parse_xml(f'<w:txbxContent xmlns:w="{W}"><w:p><w:r><w:t>box {PH}</w:t></w:r></w:p></w:txbxContent>'))
    return make_docx(mod)


def spec(declared=None, rows=()):
    v = dict(declarations=[dict(name="document_title", type="text", default=declared)] if declared else [], values={})
    return dict(title="T", blocks=[dict(type="snippet_ref", slot="CONTENT", pin=dict(snippet_id="s", sha="a" * 40, folder_id="f"))],
                variables=v, links=[], revision_history=list(rows),
                built_ins={"doc.title": "My Manual", "doc.version": "1.2", "doc.date": "2026-11-24", "org.name": "O", "author": "A"})


CONTENT = {"type": "doc", "content": [
    {"type": "toc", "attrs": {"kind": "toc", "title": "Contents", "levels": "1-2"}},
    H(PH), P(T(f"{PH} body")),
    P(T("Table "), {"type": "field", "attrs": {"code": "SEQ", "arg": "Table", "text": "1"}}, T(f": {PH}"), style="Caption")]}


def export(**kw):
    out, warn = docx_export.build(spec(**kw), base(), lambda pin: (CONTENT, "s"), lambda *a: None)
    z = zipfile.ZipFile(io.BytesIO(out))
    return {n: z.read(n).decode() for n in z.namelist() if n.endswith(".xml")}, warn, Document(io.BytesIO(out))


def texts(doc):
    return ["".join(t.text or "" for t in p.iter(qn("w:t"))) for p in doc.element.body.iter(qn("w:p"))]


@pytest.mark.parametrize("declared,want", [(None, "My Manual"), ("Declared Title", "Declared Title")])
def test_document_title_resolves_in_every_place(declared, want):
    parts, warn, doc = export(declared=declared)
    assert not any("{{" in x for x in parts.values()), [n for n, x in parts.items() if "{{" in x]
    assert not any("unresolved" in w or "undefined" in w for w in warn), warn
    body = texts(doc)
    assert body.count(want) >= 2  # TOC cached entry + the heading itself
    assert f"{want} body" in body and f"Table 1: {want}" in body and f"box {want}" in body
    assert any(f"head {want}" in x for n, x in parts.items() if "header" in n) and any(f"foot {want} v1.2" in x for n, x in parts.items() if "footer" in n)


def test_leftover_placeholders_are_reported_with_their_part():
    c = {"type": "doc", "content": [P(T("{{ nope }} and {{ nope.deep }}"))]}
    out, warn = docx_export.build(spec(), make_docx(lambda d: setattr(d.sections[0].footer.paragraphs[0], "text", "{{ gone }}")), lambda pin: (c, "s"), lambda *a: None)
    assert "undefined variables: gone, nope, nope.deep" in warn
    assert any(w.startswith("unresolved placeholder in document:") and "{{ nope }}" in w for w in warn)
    assert any(w.startswith("unresolved placeholder in word/footer") and "{{ gone }}" in w for w in warn)


def test_revision_cell_values_are_data_not_templates():
    rows = [dict(version="v1", date="2026-01-01", author="a", message=f"Rename {PH}", sections="1.1, Table 2")]
    ctx = docx_export.context(spec(rows=rows))
    assert ctx["revision_history"][0]["sections"] == "1.1, Table 2" and ctx["document_title"] == "My Manual"
    assert ctx["revision_history"][0]["pages_affected"] == "1.1, Table 2"  # the user's wording works as an alias
