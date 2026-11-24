"""Synthetic fixtures: table style conditional formatting / tblLook / borders / cell margins, tab stops, and
titlePg / evenAndOddHeaders / per-section headers and footers."""
import io
import zipfile

from lxml import etree  # noqa

from app import docx_import as D, docx_style, fmt

NS = ('xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
      'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"')
CT = ('<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
      '<Default Extension="xml" ContentType="application/xml"/>'
      '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
      '<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>'
      '<Override PartName="/word/settings.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.settings+xml"/>'
      '<Override PartName="/word/numbering.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.numbering+xml"/>'
      + "".join(f'<Override PartName="/word/{k}{i}.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.{k}+xml"/>' for k in ("header", "footer") for i in range(1, 7))
      + '</Types>')
THEME = ('<a:theme xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"><a:themeElements><a:clrScheme name="t">'
         '<a:dk1><a:srgbClr val="000000"/></a:dk1><a:lt1><a:srgbClr val="FFFFFF"/></a:lt1><a:dk2><a:srgbClr val="1F3864"/></a:dk2><a:lt2><a:srgbClr val="E7E6E6"/></a:lt2>'
         '<a:accent1><a:srgbClr val="4472C4"/></a:accent1></a:clrScheme></a:themeElements></a:theme>')
STYLES = f'''<w:styles {NS}><w:docDefaults><w:rPrDefault><w:rPr><w:sz w:val="22"/></w:rPr></w:rPrDefault></w:docDefaults>
<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/></w:style>
<w:style w:type="paragraph" w:styleId="Keep"><w:name w:val="Keep"/><w:basedOn w:val="Normal"/><w:pPr><w:keepNext/><w:pageBreakBefore/></w:pPr></w:style>
<w:style w:type="paragraph" w:styleId="Keep2"><w:name w:val="Keep2"/><w:basedOn w:val="Keep"/></w:style>
<w:style w:type="paragraph" w:styleId="Footer"><w:name w:val="footer"/><w:basedOn w:val="Normal"/><w:pPr><w:tabs><w:tab w:val="center" w:pos="4513"/><w:tab w:val="right" w:pos="9026"/></w:tabs></w:pPr></w:style>
<w:style w:type="table" w:default="1" w:styleId="TableNormal"><w:name w:val="Normal Table"/><w:tblPr><w:tblCellMar><w:left w:w="108" w:type="dxa"/><w:right w:w="108" w:type="dxa"/></w:tblCellMar></w:tblPr></w:style>
<w:style w:type="table" w:styleId="Base"><w:name w:val="Base"/><w:basedOn w:val="TableNormal"/><w:tblPr><w:jc w:val="center"/><w:tblInd w:w="-100" w:type="dxa"/>
 <w:tblBorders><w:top w:val="single" w:sz="8" w:color="112233"/><w:insideH w:val="single" w:sz="4" w:color="445566"/></w:tblBorders>
 <w:tblCellMar><w:top w:w="113" w:type="dxa"/><w:bottom w:w="113" w:type="dxa"/></w:tblCellMar></w:tblPr></w:style>
<w:style w:type="table" w:styleId="Fancy"><w:name w:val="Fancy"/><w:basedOn w:val="Base"/>
 <w:tblStylePr w:type="firstRow"><w:rPr><w:b/><w:color w:val="FFFFFF" w:themeColor="background1"/></w:rPr><w:tblPr/><w:tcPr><w:shd w:val="clear" w:color="auto" w:fill="909FA4"/></w:tcPr></w:tblStylePr>
 <w:tblStylePr w:type="firstCol"><w:pPr><w:jc w:val="center"/></w:pPr><w:tcPr><w:shd w:val="clear" w:color="auto" w:fill="CCCCCC"/></w:tcPr></w:tblStylePr>
 <w:tblStylePr w:type="band1Horz"><w:tcPr><w:shd w:val="clear" w:color="auto" w:fill="EEEEEE"/></w:tcPr></w:tblStylePr>
 <w:tblStylePr w:type="lastRow"><w:tcPr><w:tcBorders><w:top w:val="double" w:sz="6" w:color="000000"/></w:tcBorders></w:tcPr></w:tblStylePr>
 <w:tblStylePr w:type="neCell"><w:tcPr><w:shd w:val="clear" w:color="auto" w:fill="FF0000"/></w:tcPr></w:tblStylePr></w:style></w:styles>'''


def tbl(look_attrs, rows=4, cols=3, style="Fancy", extra_pr="", cell_pr=None):
    cp = cell_pr or {}
    tr = "".join("<w:tr>" + "".join(f'<w:tc><w:tcPr><w:tcW w:w="1000" w:type="dxa"/>{cp.get((r, c), "")}</w:tcPr><w:p><w:r><w:t>r{r}c{c}</w:t></w:r></w:p></w:tc>' for c in range(cols)) + "</w:tr>" for r in range(rows))
    return (f'<w:tbl><w:tblPr><w:tblStyle w:val="{style}"/>{extra_pr}<w:tblLook {look_attrs}/></w:tblPr><w:tblGrid>' + '<w:gridCol w:w="1000"/>' * cols + f"</w:tblGrid>{tr}</w:tbl>")


def pkg(body: str, *, headers=None, settings="", tail_sect="") -> bytes:
    """headers: {part name 'header1': xml}. The rels follow from the names rId<i>."""
    parts = {"[Content_Types].xml": CT, "_rels/.rels": '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>',
             "word/styles.xml": STYLES, "word/numbering.xml": f"<w:numbering {NS}/>", "word/theme/theme1.xml": THEME,
             "word/settings.xml": f"<w:settings {NS}>{settings}</w:settings>",
             "word/document.xml": f"<w:document {NS}><w:body>{body}{tail_sect or '<w:sectPr><w:pgSz w:w=\"11906\" w:h=\"16838\"/><w:pgMar w:top=\"1134\" w:right=\"1134\" w:bottom=\"1134\" w:left=\"1134\" w:header=\"425\" w:footer=\"329\"/></w:sectPr>'}</w:body></w:document>"}
    rels = ""
    for name, xml in (headers or {}).items():
        kind = "header" if name.startswith("header") else "footer"
        parts[f"word/{name}.xml"] = f'<w:{"hdr" if kind == "header" else "ftr"} {NS}>{xml}</w:{"hdr" if kind == "header" else "ftr"}>'
        rels += f'<Relationship Id="rId_{name}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/{kind}" Target="{name}.xml"/>'
    parts["word/_rels/document.xml.rels"] = f'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rIdS" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/><Relationship Id="rIdN" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/numbering" Target="numbering.xml"/>{rels}</Relationships>'
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        for k, v in parts.items():
            z.writestr(k, v)
    return b.getvalue()


def parse_tbl(xml, **kw):
    doc, _ = D.parse(pkg(xml, **kw), lambda b: None)
    return next(n for n in D.walk(doc) if n["type"] == "table")


def eff(t, r, c):
    return t["content"][r]["content"][c]["attrs"].get("eff") or {}


# ---- table styles ---------------------------------------------------------------------------------

def test_conditional_formatting_first_row_col_bands_corner():
    t = parse_tbl(tbl('w:firstRow="1" w:lastRow="1" w:firstColumn="1" w:lastColumn="1" w:noHBand="0" w:noVBand="1"', rows=5))
    assert eff(t, 0, 0)["shd"] == "909FA4" and eff(t, 0, 0)["r"] == {"b": True, "color": "FFFFFF"}  # firstRow beats firstCol; themeColor resolved
    assert eff(t, 0, 2)["shd"] == "FF0000"  # neCell on top of firstRow
    assert eff(t, 2, 0)["shd"] == "CCCCCC" and eff(t, 2, 0)["p"] == {"jc": "center"}  # firstCol beats band
    # banding starts after the header row: row 1 = band1 (shaded), row 2 = band2 (not), row 3 = band1
    assert eff(t, 1, 1)["shd"] == "EEEEEE" and "shd" not in eff(t, 2, 1) and eff(t, 3, 1)["shd"] == "EEEEEE"
    assert eff(t, 4, 1)["bdr"]["top"] == {"val": "double", "sz": 6, "color": "000000"}  # lastRow cell border
    assert "shd" not in eff(t, 4, 1)  # last row is not banded


def test_tbllook_flags_switch_conditions_off_val_bitmask_and_attributes():
    off = parse_tbl(tbl('w:val="0000" w:firstRow="0" w:lastRow="0" w:firstColumn="0" w:lastColumn="0" w:noHBand="1" w:noVBand="1"'))
    assert all("shd" not in eff(off, r, c) for r in range(4) for c in range(3))
    on = parse_tbl(tbl('w:val="04A0"'))  # 0x20 firstRow + 0x80 firstColumn + 0x400 noVBand, no explicit attributes
    assert eff(on, 0, 1)["shd"] == "909FA4" and eff(on, 2, 0)["shd"] == "CCCCCC" and eff(on, 1, 1)["shd"] == "EEEEEE"
    tp = etree.fromstring(f"<w:tblPr {NS}><w:tblLook w:val=\"0600\"/></w:tblPr>")
    assert fmt.tbl_look(tp) == dict(firstRow=False, lastRow=False, firstCol=False, lastCol=False, hBand=False, vBand=False)


def test_borders_edges_inside_direct_and_nil():
    t = parse_tbl(tbl('w:firstRow="0" w:noHBand="1" w:noVBand="1"', style="Base"))
    b = eff(t, 1, 1)["bdr"]
    assert b["top"]["color"] == "445566" and b["bottom"]["color"] == "445566"  # inside H
    assert eff(t, 0, 1)["bdr"]["top"] == {"val": "single", "sz": 8, "color": "112233"}  # outer top from the style
    assert eff(t, 1, 1)["bdr"]["left"] == {"val": "nil", "sz": 0}  # the style has no vertical borders: explicitly none, not "unset"
    direct = '<w:tblBorders><w:left w:val="dashed" w:sz="12" w:color="FF0000"/><w:insideV w:val="nil"/><w:insideH w:val="nil"/></w:tblBorders>'
    t2 = parse_tbl(tbl('w:firstRow="0" w:noHBand="1" w:noVBand="1"', style="Base", extra_pr=direct,
                       cell_pr={(1, 1): '<w:tcBorders><w:bottom w:val="single" w:sz="24" w:color="00FF00"/></w:tcBorders>'}))
    assert eff(t2, 1, 0)["bdr"]["left"] == {"val": "dashed", "sz": 12, "color": "FF0000"}  # direct outer left
    assert eff(t2, 1, 1)["bdr"]["top"]["val"] == "nil" and eff(t2, 1, 1)["bdr"]["bottom"]["sz"] == 24  # direct insideH nil, tcBorders on top
    assert t2["content"][1]["content"][1]["attrs"]["cfmt"]["bdr"]["bottom"]["color"] == "00FF00"  # and kept as direct format for export


def test_cell_margins_chain_table_width_ind_jc():
    t = parse_tbl(tbl('w:firstRow="0"', style="Fancy", extra_pr='<w:tblInd w:w="-50" w:type="dxa"/><w:tblCellMar><w:left w:w="200" w:type="dxa"/></w:tblCellMar>',
                      cell_pr={(0, 1): '<w:tcMar><w:right w:w="40" w:type="dxa"/></w:tcMar>'}))
    assert eff(t, 1, 1)["mar"] == {"top": 113, "bottom": 113, "left": 200, "right": 108}  # style Base top/bottom + direct left + TableNormal right
    assert eff(t, 0, 1)["mar"]["right"] == 40 and t["content"][0]["content"][1]["attrs"]["cfmt"]["mar"] == {"right": 40}
    assert t["attrs"]["tfmt"]["jc"] == "center" and t["attrs"]["tfmt"]["ind"] == -50  # jc from the style, ind overridden by direct


def test_shading_theme_fill_tint_and_default_style():
    cp = {(0, 0): '<w:shd w:val="clear" w:color="auto" w:fill="FFFFFF" w:themeFill="accent1" w:themeFillTint="80"/>',
          (0, 1): '<w:shd w:val="clear" w:color="auto" w:fill="FFFFFF" w:themeFill="accent1" w:themeFillShade="80"/>'}
    t = parse_tbl(tbl('w:firstRow="0"', style="NoSuchStyle", cell_pr=cp))
    assert eff(t, 0, 0)["shd"] == "A1B8E1" and eff(t, 0, 1)["shd"] == "223962"  # tint: toward white; shade: toward black
    assert eff(t, 0, 0)["mar"] == {"left": 108, "right": 108}  # unknown style falls back to the default table style


def test_merged_cell_reaching_last_row_is_bottom_edge_and_row_height():
    x = (f'<w:tbl><w:tblPr><w:tblStyle w:val="Base"/></w:tblPr><w:tblGrid><w:gridCol w:w="1000"/><w:gridCol w:w="1000"/></w:tblGrid>'
         '<w:tr><w:trPr><w:trHeight w:val="600" w:hRule="exact"/><w:tblHeader/></w:trPr><w:tc><w:tcPr><w:vMerge w:val="restart"/></w:tcPr><w:p/></w:tc><w:tc><w:p/></w:tc></w:tr>'
         '<w:tr><w:tc><w:tcPr><w:vMerge/></w:tcPr><w:p/></w:tc><w:tc><w:p/></w:tc></w:tr></w:tbl>')
    t = parse_tbl(x)
    assert t["content"][0]["content"][0]["attrs"]["rowspan"] == 2
    assert "bottom" not in eff(t, 0, 0).get("bdr", {})  # spans to the last row = outer bottom edge (the style has none), not insideH
    assert t["content"][0]["attrs"]["rfmt"] == {"h": {"val": 600, "rule": "exact"}}


def test_export_keeps_table_style_direct_props_and_row_height():
    cp = {(1, 1): '<w:shd w:val="clear" w:color="auto" w:fill="ABCDEF"/><w:tcMar><w:left w:w="300" w:type="dxa"/></w:tcMar><w:textDirection w:val="btLr"/>'}
    body = tbl('w:firstRow="1"', rows=2, extra_pr='<w:tblBorders><w:top w:val="single" w:sz="12" w:color="123456"/></w:tblBorders>', cell_pr=cp)
    body = body.replace("<w:tr>", '<w:tr><w:trPr><w:trHeight w:val="500" w:hRule="atLeast"/></w:trPr>', 1)
    src = pkg(body)
    doc, _ = D.parse(src, lambda b: None)
    out, warn = D.export(src, doc, lambda i: None)
    root = etree.fromstring(zipfile.ZipFile(io.BytesIO(out)).read("word/document.xml"))
    ns = {"w": fmt.W_NS}
    assert root.xpath("//w:tblPr/w:tblStyle/@w:val", namespaces=ns) == ["Fancy"]
    assert root.xpath("//w:tblPr/w:tblBorders/w:top/@w:color", namespaces=ns) == ["123456"]
    assert root.xpath("//w:tc/w:tcPr/w:shd/@w:fill", namespaces=ns) == ["ABCDEF"]
    assert root.xpath("//w:tc/w:tcPr/w:tcMar/w:left/@w:w", namespaces=ns) == ["300"]
    assert root.xpath("//w:tc/w:tcPr/w:textDirection/@w:val", namespaces=ns) == ["btLr"]
    assert root.xpath("//w:tr/w:trPr/w:trHeight/@w:val", namespaces=ns) == ["500"]
    # derived `eff` never leaks into the file as direct formatting: only the one direct cell shading exists
    assert len(root.xpath("//w:tcPr/w:shd", namespaces=ns)) == 1


def test_snippet_export_round_trip_custom_table():
    src = pkg(tbl('w:firstRow="1"', rows=2, extra_pr='<w:tblBorders><w:bottom w:val="double" w:sz="6" w:color="AA0000"/></w:tblBorders>'))
    doc, _ = D.parse(src, lambda b: None)
    again, _ = D.parse(D.export(src, doc, lambda i: None)[0], lambda b: None)
    t0, t1 = next(n for n in D.walk(doc) if n["type"] == "table"), next(n for n in D.walk(again) if n["type"] == "table")
    assert t0["attrs"]["tblPr"] == t1["attrs"]["tblPr"] and t0["attrs"]["styleId"] == t1["attrs"]["styleId"] == "Fancy"
    assert [eff(t0, r, c) for r in range(2) for c in range(3)] == [eff(t1, r, c) for r in range(2) for c in range(3)]


# ---- tab stops ------------------------------------------------------------------------------------

def test_tab_stops_parsed_styles_and_written_back():
    p = etree.fromstring(f'<w:pPr {NS}><w:tabs><w:tab w:val="right" w:leader="dot" w:pos="9628"/><w:tab w:val="clear" w:pos="1"/><w:tab w:val="left" w:pos="440"/><w:tab w:val="weird" w:pos="5"/></w:tabs></w:pPr>')
    d, left = fmt.para_fmt(p)
    assert d["tabs"] == [{"pos": 440, "val": "left"}, {"pos": 9628, "val": "right", "leader": "dot"}] and "tabs" not in left
    ppr = etree.Element(fmt.W + "pPr")
    fmt.write_para(ppr, {"tabs": d["tabs"] + [{"pos": "x", "val": "left"}, {"pos": 5, "val": "evil"}], "jc": "left"})
    assert [(t.get(fmt.W + "val"), t.get(fmt.W + "pos"), t.get(fmt.W + "leader")) for t in ppr[0]] == [("left", "440", None), ("right", "9628", "dot")]
    assert [fmt.loc(c) for c in ppr] == ["tabs", "jc"]  # schema order
    body = '<w:p><w:pPr><w:tabs><w:tab w:val="right" w:pos="9000"/></w:tabs></w:pPr><w:r><w:t>a</w:t><w:tab/><w:t>b</w:t></w:r></w:p>'
    doc, _ = D.parse(pkg(body), lambda b: None)
    para = doc["content"][0]
    assert para["attrs"]["pfmt"]["tabs"] == [{"pos": 9000, "val": "right"}]
    out = D.export(pkg(body), doc, lambda i: None)[0]
    assert b'w:tab w:val="right" w:pos="9000"' in zipfile.ZipFile(io.BytesIO(out)).read("word/document.xml")
    lk = docx_style.look(pkg(body))
    assert lk["flags"] == {"Keep": {"keepNext": True, "pageBreakBefore": True}, "Keep2": {"keepNext": True, "pageBreakBefore": True}}  # inherited
    assert lk["tabs"]["Footer"] == [{"pos": 4513, "val": "center"}, {"pos": 9026, "val": "right"}]  # inherited by header/footer text


# ---- sections / headers / footers -----------------------------------------------------------------

HP = '<w:p><w:r><w:t>{}</w:t></w:r></w:p>'


def sect(refs, extra="", cols="1134"):
    return (f'<w:sectPr>{refs}<w:pgSz w:w="11906" w:h="16838"/><w:pgMar w:top="{cols}" w:right="1134" w:bottom="1134" w:left="1134" w:header="425" w:footer="329"/>{extra}</w:sectPr>')


def ref(kind, typ, name):
    return f'<w:{kind}Reference w:type="{typ}" r:id="rId_{name}"/>'


def test_title_pg_even_odd_and_per_section_headers_inherit():
    hdrs = {f"header{i}": HP.format(f"H{i}") for i in (1, 2, 3, 4)} | {f"footer{i}": HP.format(f"F{i}") for i in (1, 2)}
    s1 = sect(ref("header", "default", "header1") + ref("header", "first", "header2") + ref("footer", "default", "footer1"), "<w:titlePg/>")
    s2 = sect(ref("header", "default", "header3") + ref("header", "even", "header4"), '<w:type w:val="continuous"/><w:pgNumType w:fmt="upperRoman" w:start="5"/>', cols="2000")
    body = f'<w:p><w:pPr>{s1}</w:pPr><w:r><w:t>one</w:t></w:r></w:p><w:p><w:pPr>{s2}</w:pPr><w:r><w:t>two</w:t></w:r></w:p><w:p><w:r><w:t>three</w:t></w:r></w:p>'
    last = sect(ref("footer", "default", "footer2"))
    sec = docx_style.Look(pkg(body, headers=hdrs, settings="<w:evenAndOddHeaders/>", tail_sect=last)).sections()
    s = sec["sections"]
    assert len(s) == 3 and sec["even_odd"] is True
    xml = lambda i, kind, t: sec["parts"][s[i][kind][t]]["xml"] if t in s[i][kind] else None
    assert s[0]["title_pg"] and "H2" in xml(0, "header", "first") and "H1" in xml(0, "header", "default") and "F1" in xml(0, "footer", "default")
    assert not s[1]["title_pg"] and "H3" in xml(1, "header", "default") and "H4" in xml(1, "header", "even")
    assert "H2" in xml(1, "header", "first")  # inherited from the previous section, as in Word
    assert "F1" in xml(1, "footer", "default") and "F2" in xml(2, "footer", "default") and "H3" in xml(2, "header", "default")
    assert s[1]["brk"] == "continuous" and s[0]["brk"] == "nextPage"
    assert s[1]["num"] == {"fmt": "upperRoman", "start": 5} and s[1]["page"]["top"] == 100.0 and s[2]["page"]["top"] == 56.7
    assert len(sec["parts"]) == 6  # distinct parts listed once
    lk = docx_style.look(pkg(body, headers=hdrs, tail_sect=last))
    assert "F2" in lk["footer"]["xml"] and "H3" in lk["header"]["xml"] and len(lk["sections"]) == 3  # final section's default, compat fields


def test_section_break_paragraph_stays_opaque_so_the_view_can_find_it():
    s1 = sect("")
    doc, _ = D.parse(pkg(f'<w:p><w:pPr>{s1}</w:pPr><w:r><w:t>x</w:t></w:r></w:p><w:p><w:r><w:t>y</w:t></w:r></w:p>'), lambda b: None)
    assert doc["content"][0]["type"] == "opaque" and doc["content"][0]["attrs"]["kind"] == "section break"
