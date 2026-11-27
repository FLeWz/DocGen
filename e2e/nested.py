"""Round 24 e2e: a synthetic DOCX with a nested table, a text box, a block content control and FORMTEXT form fields is imported; the nested
cell, the text box (inline, not in a modal), the content control and a form field are edited in the live editor; the exported DOCX
carries exactly those edits (canonical XML of every other body child unchanged). Logs in as the throwaway user `nested`."""
import io
import os
import re
import tempfile
import zipfile

from docx import Document
from docx.oxml import parse_xml
from lxml import etree
from playwright.sync_api import expect, sync_playwright

from common import ART, BASE, editor_ready, login, saved, user

email, password, _ = user("nested")
os.makedirs(ART, exist_ok=True)
W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
NS = f'xmlns:w="{W}" xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006" xmlns:wps="http://schemas.microsoft.com/office/word/2010/wordprocessingShape" ' \
     'xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing" xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:v="urn:schemas-microsoft-com:vml"'
P = lambda t: f"<w:p {NS}><w:r><w:t>{t}</w:t></w:r></w:p>"
GRID = lambda ws: "<w:tblGrid>" + "".join(f'<w:gridCol w:w="{w}"/>' for w in ws) + "</w:tblGrid>"
TC = lambda w, body, extra="": f'<w:tc><w:tcPr><w:tcW w:w="{w}" w:type="dxa"/>{extra}</w:tcPr>{body}</w:tc>'
cell = lambda t: f"<w:p><w:r><w:t>{t}</w:t></w:r></w:p>"
INNER = ('<w:tbl><w:tblPr><w:tblStyle w:val="TableGrid"/><w:tblW w:w="3600" w:type="dxa"/></w:tblPr>' + GRID([1800, 1800])
         + f'<w:tr>{TC(1800, cell("inner-a"), "<w:shd w:val=\"clear\" w:color=\"auto\" w:fill=\"FFFF00\"/>")}{TC(1800, cell("inner-b"))}</w:tr>'
         + f'<w:tr>{TC(3600, cell("inner-merged"), "<w:gridSpan w:val=\"2\"/>")}</w:tr></w:tbl>')
OUTER = (f'<w:tbl {NS}><w:tblPr><w:tblStyle w:val="TableGrid"/><w:tblW w:w="9000" w:type="dxa"/></w:tblPr>' + GRID([4500, 4500])
         + f'<w:tr>{TC(4500, INNER + "<w:p/>")}{TC(4500, cell("outer-b"))}</w:tr></w:tbl>')
BOX = (f'<w:p {NS}><w:r><mc:AlternateContent><mc:Choice Requires="wps"><w:drawing><wp:anchor distT="0" distB="0" distL="0" distR="0" simplePos="0" relativeHeight="1" '
       'behindDoc="0" locked="0" layoutInCell="1" allowOverlap="1"><wp:simplePos x="0" y="0"/><wp:positionH relativeFrom="column"><wp:posOffset>0</wp:posOffset></wp:positionH>'
       '<wp:positionV relativeFrom="paragraph"><wp:posOffset>0</wp:posOffset></wp:positionV><wp:extent cx="2000000" cy="500000"/><wp:wrapSquare wrapText="bothSides"/>'
       '<wp:docPr id="1" name="Box"/><a:graphic><a:graphicData uri="http://schemas.microsoft.com/office/word/2010/wordprocessingShape"><wps:wsp><wps:spPr/><wps:txbx>'
       '<w:txbxContent><w:p><w:r><w:t>box-text</w:t></w:r></w:p></w:txbxContent></wps:txbx></wps:wsp></a:graphicData></a:graphic></wp:anchor></w:drawing></mc:Choice>'
       '<mc:Fallback><w:pict><v:shape><v:textbox><w:txbxContent><w:p><w:r><w:t>box-text</w:t></w:r></w:p></w:txbxContent></v:textbox></v:shape></w:pict></mc:Fallback>'
       '</mc:AlternateContent></w:r></w:p>')
SDT = (f'<w:sdt {NS}><w:sdtPr><w:alias w:val="Block"/><w:tag w:val="T1"/></w:sdtPr><w:sdtContent><w:p><w:r><w:t>sdt-text</w:t></w:r></w:p></w:sdtContent></w:sdt>')
FORM = (f'<w:p {NS}><w:r><w:fldChar w:fldCharType="begin"><w:ffData><w:name w:val="Sl_Title"/><w:enabled/><w:textInput><w:default w:val="D"/></w:textInput></w:ffData></w:fldChar></w:r>'
        '<w:bookmarkStart w:id="1" w:name="Sl_Title"/><w:r><w:instrText xml:space="preserve"> FORMTEXT </w:instrText></w:r><w:r><w:fldChar w:fldCharType="separate"/></w:r>'
        '<w:r><w:t>form-text</w:t></w:r><w:r><w:fldChar w:fldCharType="end"/></w:r><w:bookmarkEnd w:id="1"/></w:p>')


def fixture(path):
    d = Document()
    body = d.element.body
    sect = body.find(f"{{{W}}}sectPr")
    for xml in (P("start"), FORM, OUTER, P("between"), BOX, SDT, P("end")):
        sect.addprevious(parse_xml(xml))
    for p in d.element.body.findall(f"{{{W}}}p")[:1]:
        pass
    d.save(path)


def body_kids(data):
    root = etree.fromstring(zipfile.ZipFile(io.BytesIO(data)).read("word/document.xml"))
    return [etree.tostring(c, method="c14n") for c in root.find(f"{{{W}}}body")]


with sync_playwright() as pw:
    browser = pw.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 1500, "height": 1100})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    try:
        login(page, email, password)
        path = os.path.join(tempfile.mkdtemp(), "nested.docx")
        fixture(path)
        orig = open(path, "rb").read()
        page.get_by_role("link", name="Imports").first.click()
        page.get_by_label("DOCX file").set_input_files(path)
        expect(page).to_have_url(re.compile("/imports/"))
        iid = page.url.rsplit("/", 1)[1]
        ed = editor_ready(page)
        page.get_by_label("Zoom").select_option("1")
        model = page.evaluate("(id) => fetch('/api/imports/' + id).then(r => r.json())", iid)["content"]["content"]
        assert not [b for b in model if b["type"] == "opaque"], [b["type"] for b in model]
        assert {"textbox", "sdtBlock", "table"} <= {b["type"] for b in model}

        # nested table: edit a cell, Table tab acts on the inner table, Outer table moves the commands to the outer one
        inner = ed.locator("td table").first
        inner.get_by_text("inner-b").click()
        page.keyboard.press("End")
        page.keyboard.type("-EDIT")
        expect(page.get_by_role("tab", name="Table")).to_have_attribute("aria-selected", "true")
        expect(page.get_by_test_id("outer-table")).to_be_enabled()
        expect(page.get_by_test_id("inner-table")).to_be_disabled()
        page.get_by_test_id("outer-table").click()
        expect(page.get_by_test_id("inner-table")).to_be_enabled()
        page.get_by_test_id("inner-table").click()
        page.keyboard.press("Alt+ArrowUp")
        expect(page.get_by_test_id("inner-table")).to_be_enabled()
        ed.get_by_text("between").click()

        # text box inline (no modal), content control, form field
        ed.locator(".txbx p").first.click()
        page.keyboard.press("End")
        page.keyboard.type("-BOX")
        expect(page.get_by_role("dialog")).to_have_count(0)
        ed.locator(".sdtb p").first.click()
        page.keyboard.press("End")
        page.keyboard.type("-SDT")
        ed.locator(".fld-FORMTEXT").first.dblclick()
        dlg = page.get_by_role("dialog")
        dlg.get_by_label("Text").fill("form-NEW")
        dlg.get_by_role("button", name="Save").click()
        saved(page)
        page.screenshot(path=os.path.join(ART, "nested-edit.png"))

        r = page.request.get(f"{BASE}/api/imports/{iid}/export")
        assert r.ok, r.status
        out = r.body()
        Document(io.BytesIO(out))
        root = etree.fromstring(zipfile.ZipFile(io.BytesIO(out)).read("word/document.xml"))
        ns = {"w": W}
        text = lambda e: "".join(e.itertext())
        inner_t = root.xpath("//w:tc//w:tbl", namespaces=ns)
        assert len(inner_t) == 1 and "inner-b-EDIT" in text(inner_t[0]) and "inner-a" in text(inner_t[0]) and "inner-merged" in text(inner_t[0])
        assert inner_t[0].xpath(".//w:shd/@w:fill", namespaces=ns) == ["FFFF00"] and inner_t[0].xpath(".//w:gridSpan", namespaces=ns)
        assert all(tc[-1].tag == f"{{{W}}}p" for tc in root.iter(f"{{{W}}}tc"))
        boxes = root.xpath("//w:txbxContent", namespaces=ns)
        assert len(boxes) == 2 and all(text(b) == "box-text-BOX" for b in boxes), [text(b) for b in boxes]
        assert text(root.xpath("//w:sdtContent", namespaces=ns)[0]) == "sdt-text-SDT" and root.xpath("//w:sdt/w:sdtPr/w:tag/@w:val", namespaces=ns) == ["T1"]
        assert root.xpath("//w:fldChar/w:ffData/w:name/@w:val", namespaces=ns) == ["Sl_Title"] and "form-NEW" in text(root) and "form-text" not in text(root)
        a, b = body_kids(orig), body_kids(out)
        same = [i for i, (x, y) in enumerate(zip(a, b)) if x == y]
        assert len(a) == len(b) and {0, 3, 6} <= set(same), (len(a), len(b), same)  # start, between, end paragraphs are byte-identical
        print("nested e2e ok: nested cell, text box, content control and form field edited; unrelated blocks unchanged")
        assert not errors, errors
    except Exception:
        page.screenshot(path=os.path.join(ART, "nested-fail.png"))
        raise
    finally:
        browser.close()
