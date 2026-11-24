import { describe, expect, it, test } from "vitest";
import { cellCss, fontStack, pfmtCss, runCss, tableCss } from "./fmt";
import { ooxmlToHtml, setStyleTabs } from "./ooxml";

const NS = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" ' +
  'xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing" xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" ' +
  'xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006"';

test("font stacks fall back to metric-compatible fonts", () => {
  expect(fontStack("Calibri")).toBe("'Calibri', 'Carlito', sans-serif");
  expect(fontStack("Garamond")).toBe("'Garamond', serif");
});

test("exact/atLeast line spacing is points, auto is a multiple; justify maps to CSS", () => {
  expect(pfmtCss({ line: 240, lineRule: "exact", jc: "both" })).toBe("text-align:justify;line-height:12pt");
  expect(pfmtCss({ line: 240 })).toBe("line-height:normal");
  expect(runCss({ size: 21, caps: true })).toBe("font-size:10.5pt;text-transform:uppercase");
  expect(cellCss({ bdr: { left: { val: "nil" } } })).toBe("border-left:none");
  expect(tableCss({ width: { w: 5000, type: "pct" }, jc: "center" })).toBe("width:100%;table-layout:fixed;margin-left:auto;margin-right:auto");
});

test("field instructions are hidden, the cached result stays (TOC / REF / FORMTEXT)", () => {
  const h = ooxmlToHtml(`<w:p ${NS}><w:pPr><w:pStyle w:val="Subtitle"/></w:pPr><w:r><w:fldChar w:fldCharType="begin"/></w:r><w:r><w:instrText> REF X </w:instrText></w:r>` +
    `<w:r><w:fldChar w:fldCharType="separate"/></w:r><w:r><w:rPr><w:b/><w:color w:val="FF0000"/></w:rPr><w:t>RESULT</w:t></w:r><w:r><w:fldChar w:fldCharType="end"/></w:r></w:p>`);
  expect(h).toContain("RESULT");
  expect(h).not.toContain("REF X");
  expect(h).toContain('class="st-Subtitle"');
  expect(h).toContain("<strong>");
  expect(h).toContain("color:#FF0000");
});

test("text box content, content control, tabs with leader (TOC), footnote reference", () => {
  const box = ooxmlToHtml(`<w:p ${NS}><w:r><mc:AlternateContent><mc:Choice><w:drawing><wp:inline><wp:extent cx="952500" cy="95250"/><a:graphic><w:txbxContent><w:p><w:r><w:t>IN-BOX</w:t></w:r></w:p></w:txbxContent></a:graphic></wp:inline></w:drawing></mc:Choice>` +
    `<mc:Fallback><w:pict><w:txbxContent><w:p><w:r><w:t>IN-BOX</w:t></w:r></w:p></w:txbxContent></w:pict></mc:Fallback></mc:AlternateContent></w:r></w:p>`);
  expect(box.match(/IN-BOX/g)).toHaveLength(1); // Choice only, not the Fallback copy
  expect(box).toContain('class="txbx" style="width:100px"');
  const sdt = ooxmlToHtml(`<w:sdt ${NS}><w:sdtContent><w:p><w:pPr><w:pStyle w:val="TOC1"/></w:pPr><w:r><w:t>Intro</w:t></w:r><w:r><w:tab/></w:r><w:r><w:t>3</w:t></w:r></w:p></w:sdtContent></w:sdt>`);
  expect(sdt).toContain('class="tabrow flex"');  // TOC line without explicit stops: dot-leader layout
  expect(sdt).toContain('<span class="tab-fill lead" style="border-bottom-style:dotted"></span><span class="tab-seg"><span>3</span></span>');
  expect(ooxmlToHtml(`<w:p ${NS}><w:r><w:footnoteReference w:id="2"/></w:r></w:p>`)).toContain('<sup class="fnref">2</sup>');
});

test("tables: grid, shading, vertical merge, borders", () => {
  const h = ooxmlToHtml(`<w:tbl ${NS}><w:tblPr><w:tblBorders><w:top w:val="single" w:sz="4" w:color="909FA4"/></w:tblBorders></w:tblPr><w:tblGrid><w:gridCol w:w="2000"/><w:gridCol w:w="1000"/></w:tblGrid>` +
    `<w:tr><w:tc><w:tcPr><w:vMerge w:val="restart"/><w:shd w:fill="D9E2F3"/></w:tcPr><w:p><w:r><w:t>A</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>B</w:t></w:r></w:p></w:tc></w:tr>` +
    `<w:tr><w:tc><w:tcPr><w:vMerge/></w:tcPr><w:p/></w:tc><w:tc><w:p><w:r><w:t>D</w:t></w:r></w:p></w:tc></w:tr></w:tbl>`);
  expect(h).toContain('rowspan="2" style="background:#D9E2F3"');
  expect(h).toContain("--b-top:0.5pt solid #909FA4");
  expect(h).toContain('<col style="width:66.67%"><col style="width:33.33%">');
  expect(h.match(/<td/g)).toHaveLength(3);
});

test("images come only from the server-provided media map; junk XML gives empty output", () => {
  const xml = `<w:p ${NS}><w:r><w:drawing><wp:inline><wp:extent cx="952500" cy="476250"/><a:graphic><a:blip r:embed="rId5"/></a:graphic></wp:inline></w:drawing></w:r></w:p>`;
  expect(ooxmlToHtml(xml, { rId5: "data:image/png;base64,AAAA" })).toContain('<img src="data:image/png;base64,AAAA" alt="" style="width:100px;height:50px">');
  expect(ooxmlToHtml(xml, { rId5: "javascript:alert(1)" })).not.toContain("<img");
  expect(ooxmlToHtml("<w:p><unclosed")).toBe("");
});

describe("header/footer: tab stops from the style and per-page fields", () => {
  const footer = (tab: string) => `<w:ftr ${NS}><w:p><w:pPr><w:pStyle w:val="Footer"/></w:pPr><w:r><w:t>Title</w:t></w:r><w:r><w:tab/></w:r><w:r><w:t xml:space="preserve">Page </w:t></w:r>`
    + `<w:r><w:fldChar w:fldCharType="begin"/></w:r><w:r><w:instrText> PAGE </w:instrText></w:r><w:r><w:fldChar w:fldCharType="separate"/></w:r><w:r><w:t>1</w:t></w:r><w:r><w:fldChar w:fldCharType="end"/></w:r>`
    + `<w:r><w:t xml:space="preserve"> of </w:t></w:r><w:fldSimple w:instr=" NUMPAGES "><w:r><w:t>9</w:t></w:r></w:fldSimple>${tab}</w:p></w:ftr>`;
  it("'Title<tab>Page X of Y' with the right stop inherited from the Footer style, fields evaluated", () => {
    setStyleTabs({ Footer: [{ pos: 4513, val: "center" }, { pos: 9026, val: "right" }] });
    const h = ooxmlToHtml(footer(""), {}, { page: "VII", pages: "12", now: new Date(2026, 0, 1) });
    expect(h).toContain("Title");
    expect(h).toContain("Page </span>");
    expect(h).toContain(">VII</span>");   // PAGE replaced, cached "1" hidden
    expect(h).toContain(">12</span>");    // fldSimple NUMPAGES
    expect(h).not.toMatch(/>1<\/span>|>9<\/span>/);
    expect(h).toContain("translateX(-50%)"); // first tab -> the centre stop
    setStyleTabs(undefined);
  });
  it("without a context the cached field result stays", () => {
    const h = ooxmlToHtml(footer(""));
    expect(h).toContain(">1</span>");
    expect(h).toContain(">9</span>");
  });
  it("tables in headers: cell margins, row height, indent", () => {
    const h = ooxmlToHtml(`<w:tbl ${NS}><w:tblPr><w:tblInd w:w="-109" w:type="dxa"/><w:tblCellMar><w:top w:w="108" w:type="dxa"/></w:tblCellMar></w:tblPr><w:tblGrid><w:gridCol w:w="2000"/></w:tblGrid><w:tr><w:trPr><w:trHeight w:val="400" w:hRule="atLeast"/></w:trPr><w:tc><w:tcPr><w:tcMar><w:left w:w="200" w:type="dxa"/></w:tcMar></w:tcPr><w:p><w:r><w:t>x</w:t></w:r></w:p></w:tc></w:tr></w:tbl>`);
    expect(h).toContain("margin-left:-5.45pt");
    expect(h).toContain("height:20pt");
    expect(h).toContain("padding-top:5.4pt");
    expect(h).toContain("padding-left:10pt");
  });
});

test("opaque paragraph without pStyle is Normal, so table-style paragraph rules reach it", () => {
  expect(ooxmlToHtml(`<w:p ${NS}><w:r><w:fldChar w:fldCharType="begin"/></w:r><w:r><w:instrText> X </w:instrText></w:r><w:r><w:fldChar w:fldCharType="separate"/></w:r><w:r><w:t>x</w:t></w:r></w:p>`)).toContain('class="st-Normal"');
});

test("auto line spacing is a multiple of the single-spaced font line (259/240 -> 1.241), single stays normal", () => {
  expect(pfmtCss({ line: 259, lineRule: "auto" })).toBe("line-height:1.241");
  expect(pfmtCss({ line: 360 })).toBe("line-height:1.725");
  expect(pfmtCss({ line: 480, lineRule: "atLeast" })).toBe("line-height:24pt");
});

test("paragraph-mark size sets the paragraph font size (strut), also for opaque paragraphs", () => {
  expect(pfmtCss({ mark: { size: 16 }, line: 259 })).toBe("font-size:8pt;line-height:1.241");
  expect(ooxmlToHtml(`<w:p ${NS}><w:pPr><w:rPr><w:sz w:val="16"/></w:rPr></w:pPr><w:r><w:t>small</w:t></w:r></w:p>`)).toContain("font-size:8pt");
});
