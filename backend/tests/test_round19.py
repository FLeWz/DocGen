"""Round 19: paragraphs that only carry harmless wrappers import as editable text (not opaque blocks); unedited blocks are
written back byte for byte, edited ones keep everything else. Caption export (SEQ field, Caption style)."""
import copy
import io
import json
import zipfile

from lxml import etree

from app import docx_import as D

from .test_base_docs import make_docx, rezip

NS = ('xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006" '
      'xmlns:wps="http://schemas.microsoft.com/office/word/2010/wordprocessingShape" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"')
R = lambda t: f"<w:r><w:t>{t}</w:t></w:r>"
FIELD = lambda instr, shown: ('<w:r><w:fldChar w:fldCharType="begin"/></w:r><w:r><w:instrText xml:space="preserve"> %s </w:instrText></w:r>'
                              '<w:r><w:fldChar w:fldCharType="separate"/></w:r>%s<w:r><w:fldChar w:fldCharType="end"/></w:r>' % (instr, R(shown)))
PARAS = {
    "smarttag": f'<w:p><w:smartTag w:uri="u" w:element="place">{R("SMART-TEXT")}</w:smartTag></w:p>',
    "customxml": f'<w:p><w:customXml w:uri="u" w:element="e">{R("CUSTOM-TEXT")}</w:customXml></w:p>',
    "sdt": f'<w:p><w:sdt><w:sdtPr><w:alias w:val="A"/><w:text/></w:sdtPr><w:sdtContent>{R("SDT-TEXT")}</w:sdtContent></w:sdt></w:p>',
    "revision": f'<w:p>{R("Before ")}<w:ins w:id="1" w:author="e" w:date="2024-01-01T00:00:00Z">{R("INS-TEXT")}</w:ins><w:del w:id="2" w:author="e" w:date="2024-01-01T00:00:00Z"><w:r><w:delText>DEL-TEXT</w:delText></w:r></w:del></w:p>',
    "comment": f'<w:p><w:commentRangeStart w:id="0"/>{R("COMMENT-TEXT")}<w:commentRangeEnd w:id="0"/><w:r><w:commentReference w:id="0"/></w:r></w:p>',
    "ref": f'<w:p><w:bookmarkStart w:id="9" w:name="_Ref123"/>{R("See ")}{FIELD("REF _Ref123 \\h", "Table 1")}{R(" and ")}<w:bookmarkEnd w:id="9"/></w:p>',
    "seq": f'<w:p><w:pPr><w:pStyle w:val="Caption"/></w:pPr>{R("Table ")}{FIELD("SEQ Table \\* ARABIC", "1")}{R(": Cap")}</w:p>',
    "anchor": f'<w:p><w:hyperlink w:anchor="top">{R("ANCHOR-TEXT")}</w:hyperlink></w:p>',
    "textbox": ('<w:p><w:r><mc:AlternateContent><mc:Choice Requires="wps"><w:drawing><wps:wsp><wps:txbx><w:txbxContent>'
                f'<w:p>{R("BOX-TEXT")}</w:p></w:txbxContent></wps:txbx></wps:wsp></w:drawing></mc:Choice></mc:AlternateContent></w:r></w:p>'),
    "formtext": f'<w:p>{FIELD("FORMTEXT", "FORM-TEXT")}</w:p>',
}


def package():
    doc = f'<w:document {NS}><w:body>{"".join(PARAS.values())}<w:sectPr/></w:body></w:document>'
    return rezip(make_docx(), replace={"word/document.xml": doc.encode()})


def body_children(data):
    root = etree.fromstring(zipfile.ZipFile(io.BytesIO(data)).read("word/document.xml"))
    return [etree.tostring(c, method="c14n") for c in root.find("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}body")]


def text(n):
    return D.block_text(n)


def test_wrapped_text_imports_as_paragraphs_and_only_textbox_and_form_field_stay_opaque():
    content, warn = D.parse(package(), lambda b: None)
    blocks = content["content"]
    assert [b["type"] for b in blocks] == ["paragraph"] * 8 + ["opaque", "opaque"]
    assert [b["attrs"]["kind"] for b in blocks[8:]] == ["text box", "form field"]
    t = [text(b) for b in blocks[:8]]
    assert t[0] == "SMART-TEXT" and t[1] == "CUSTOM-TEXT" and t[2] == "SDT-TEXT" and t[3] == "Before INS-TEXT" and "DEL-TEXT" not in t[3]
    assert t[4] == "COMMENT-TEXT" and t[7] == "ANCHOR-TEXT"
    ref = next(x for x in D.walk(blocks[5]) if x.get("type") == "field")
    assert ref["attrs"] == {"code": "REF", "arg": "_Ref123", "text": "Table 1"}
    seq = next(x for x in D.walk(blocks[6]) if x.get("type") == "field")
    assert seq["attrs"]["arg"] == "Table" and seq["attrs"]["text"] == "1"  # label only: no "* ARABIC" in the model
    w = " | ".join(warn)
    assert "1× content control" in w and "1× smartTag" in w and "1× tracked insertion" in w and "× comment" in w and "1× text box" in w and "1× form field" in w


def test_unedited_round_trip_is_byte_identical_and_edit_changes_only_that_paragraph():
    orig = package()
    content, _ = D.parse(orig, lambda b: None)
    out, _ = D.export(orig, content, lambda *_: None)
    assert body_children(out)[:-1] == body_children(orig)[:-1]  # every block, sectPr aside
    for part in ("word/styles.xml", "word/numbering.xml", "word/settings.xml"):
        assert zipfile.ZipFile(io.BytesIO(out)).read(part) == zipfile.ZipFile(io.BytesIO(orig)).read(part) if part in zipfile.ZipFile(io.BytesIO(orig)).namelist() else True
    edited = copy.deepcopy(content)
    edited["content"][2]["content"] = [{"type": "text", "text": "SDT-CHANGED"}]  # the former content control
    out2, _ = D.export(orig, edited, lambda *_: None)
    a, b = body_children(orig), body_children(out2)
    assert b"SDT-CHANGED" in b[2] and b"SDT-TEXT" not in b[2]
    assert [x for i, x in enumerate(b) if i != 2][:-1] == [x for i, x in enumerate(a) if i != 2][:-1]  # all other blocks untouched


def test_inserted_caption_exports_seq_field_caption_style_keepnext_and_feeds_the_list():
    from docx.oxml.ns import qn
    from .test_rich import F, H, P, T, body, export, instrs, part
    table = {"type": "table", "content": [{"type": "tableRow", "content": [{"type": "tableHeader", "content": [P(T("h"))]}]}]}
    cap = lambda label, n, txt: {**P(T(f"{label} "), F("SEQ", label, str(n)), T(f": {txt}"), styleId="Caption"), "attrs": {"styleId": "Caption", "pfmt": {"keepNext": True}}}
    out, _ = export([H("Intro"), cap("Table", 1, "Sizes"), table, cap("Listing", 1, "Code"), P(T("x")),
                     {"type": "toc", "attrs": {"kind": "tot", "title": "", "label": "Table"}}, {"type": "toc", "attrs": {"kind": "tof", "title": "", "label": "Listing"}}])
    b = body(out)
    assert instrs(b).count("SEQ Table \\* ARABIC") == 1 and instrs(b).count("SEQ Listing \\* ARABIC") == 1  # custom label too
    cp = [p for p in b.iter(qn("w:p")) if p.find(f"{qn('w:pPr')}/{qn('w:pStyle')}") is not None and p.find(f"{qn('w:pPr')}/{qn('w:pStyle')}").get(qn("w:val")) == "Caption"]
    assert len(cp) == 2 and all(p.find(f"{qn('w:pPr')}/{qn('w:keepNext')}") is not None for p in cp)
    text = "".join(t.text or "" for t in b.iter(qn("w:t")))
    assert text.count("Table 1: Sizes") == 2 and text.count("Listing 1: Code") == 2  # caption + its list entry (cached numbers)
    assert "ARABIC" not in text  # the switch lives in the field code, never in visible text
    assert "Caption" in {s.get(qn("w:styleId")) for s in part(out, "word/styles.xml").iter(qn("w:style"))}
