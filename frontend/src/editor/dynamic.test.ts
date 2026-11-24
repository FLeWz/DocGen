import { Editor } from "@tiptap/core";
import { expect, test } from "vitest";
import { analyzeTable, columnNames, makeDynamicFromRows, slug, undoDynamic } from "./dynamic";
import { extensions } from "./extensions";
import { ctxOf, run } from "./tables";

const txt = (t: string, marks?: unknown[]) => ({ type: "text", text: t, ...(marks ? { marks } : {}) });
const p = (...c: unknown[]) => ({ type: "paragraph", attrs: {}, content: c });
const c = (content: unknown[], a: Record<string, unknown> = {}, type = "tableCell") => ({ type, attrs: { colspan: 1, rowspan: 1, colwidth: null, ...a }, content });
const row = (cells: unknown[]) => ({ type: "tableRow", content: cells });
const bold = [{ type: "bold" }];
const mk = (rows: unknown[], attrs: Record<string, unknown> = {}) => new Editor({ extensions, content: { type: "doc", content: [{ type: "table", attrs: { grid: [3000, 3000], ...attrs }, content: rows }, p(txt("after"))] as any } });
const inTable = (e: Editor) => { let hit = 0; e.state.doc.descendants((n, pos) => { if (!hit && n.type.name === "table") hit = pos + 4; }); e.commands.setTextSelection(hit); };
const table = (e: Editor): any => e.getJSON().content![0];

test("header text -> variable names: slug, col_N for empty, duplicates numbered", () => {
  expect(slug("Full name")).toBe("full_name"); expect(slug("E-mail (work)")).toBe("e_mail_work"); expect(slug("Größe")).toBe("gro_e"); expect(slug("1st")).toBe("_1st"); expect(slug("  ")).toBe("");
  expect(columnNames(["Name", "", "Name", "name", "", "Name_2"])).toEqual(["name", "col_2", "name_2", "name_3", "col_5", "name_2_2"]);
  expect(slug("x".repeat(80)).length).toBeLessThanOrEqual(40);
});

const src = () => mk([row([c([p(txt("Full name", bold))], {}, "tableHeader"), c([p()], {}, "tableHeader")]),
  row([c([p(txt("Ann", bold))], { cfmt: { shd: "FFFF00" } }), c([p(txt("L1")), p(txt("L2"))])]), row([c([p(txt("Bob"))]), c([p(txt("x"))])])]);

test("analysis: tblHeader row names the columns, body rows are the defaults (paragraphs -> line breaks)", () => {
  const e = src();
  inTable(e);
  const plan = analyzeTable(ctxOf(e.state)!.table);
  expect(plan).toEqual({ ok: true, body: 1, headers: ["Full name", ""], rows: [["Ann", "L1\nL2"], ["Bob", "x"]] });
});

test("merged cells in header or body are refused, nothing changes", () => {
  const e = mk([row([c([p(txt("H"))], { colspan: 2 }, "tableHeader")]), row([c([p(txt("a"))]), c([p(txt("b"))])])]);
  inTable(e);
  const plan = analyzeTable(ctxOf(e.state)!.table);
  expect(plan.ok).toBe(false);
  expect(run(e, makeDynamicFromRows("t", ["a", "b"]))).toBe(false);
  expect(table(e).attrs.dynamicList).toBeNull();
});

test("conversion: header stays, first body row = formatted template with chips, other rows -> dynamicRows, one undo step", () => {
  const e = src(), before = JSON.stringify(e.getJSON());
  inTable(e);
  expect(run(e, makeDynamicFromRows("people", ["full_name", "col_2"]))).toBe(true);
  const t = table(e);
  expect(t.attrs).toMatchObject({ dynamicList: "people", dynamicCols: ["full_name", "col_2"], dynamicRows: [{ full_name: "Ann", col_2: "L1\nL2" }, { full_name: "Bob", col_2: "x" }] });
  expect(t.content.length).toBe(2);
  expect(t.content[0].content.map((x: any) => x.type)).toEqual(["tableHeader", "tableHeader"]);
  expect(t.content[0].content[0].content[0].content[0].text).toBe("Full name");     // header stays static
  const tpl = t.content[1].content;
  expect(tpl[0].attrs.cfmt).toEqual({ shd: "FFFF00" });                                 // template cell formatting kept
  expect(tpl[0].content[0].content[0]).toMatchObject({ type: "variable", attrs: { name: "row.full_name" }, marks: [{ type: "bold" }] }); // run formatting kept
  expect(tpl[1].content[0].content[0].attrs.name).toBe("row.col_2");
  e.commands.undo();
  expect(JSON.stringify(e.getJSON())).toBe(before);
});

test("undo dynamic restores the static rows from the defaults (text, line breaks, marks, cell formatting)", () => {
  const e = src();
  inTable(e);
  run(e, makeDynamicFromRows("people", ["full_name", "col_2"]));
  inTable(e);
  expect(run(e, undoDynamic)).toBe(true);
  const t = table(e);
  expect(t.attrs).toMatchObject({ dynamicList: null, dynamicRows: null });
  expect(t.content.length).toBe(3);
  const cell = (r: number, k: number) => t.content[r].content[k];
  expect(cell(1, 0).content[0].content[0]).toMatchObject({ text: "Ann", marks: [{ type: "bold" }] });
  expect(cell(1, 0).attrs.cfmt).toEqual({ shd: "FFFF00" });
  expect(cell(1, 1).content[0].content.map((n: any) => n.text ?? n.type)).toEqual(["L1", "hardBreak", "L2"]);
  expect(cell(2, 0).content[0].content[0].text).toBe("Bob");
});

test("no body rows: empty default, the template is a plain copy of the header row", () => {
  const e = mk([row([c([p(txt("Name", bold))], { cfmt: { shd: "00FF00" } }, "tableHeader"), c([p(txt("Mail"))], {}, "tableHeader")])]);
  inTable(e);
  expect(run(e, makeDynamicFromRows("l", ["name", "mail"]))).toBe(true);
  const t = table(e);
  expect(t.attrs.dynamicRows).toEqual([]);
  expect(t.content.length).toBe(2);
  expect(t.content[1].content[0].type).toBe("tableCell");
  expect(t.content[1].content[0].attrs.cfmt?.shd).toBeUndefined();
  inTable(e);
  run(e, undoDynamic);
  expect(table(e).content.length).toBe(1);   // header only: the empty default leaves no rows
});

test("a header row that is not a tblHeader row is the first row; wrong column count is refused", () => {
  const e = mk([row([c([p(txt("A"))]), c([p(txt("B"))])]), row([c([p(txt("1"))]), c([p(txt("2"))])])]);
  inTable(e);
  expect(analyzeTable(ctxOf(e.state)!.table)).toEqual({ ok: true, body: 1, headers: ["A", "B"], rows: [["1", "2"]] });
  expect(run(e, makeDynamicFromRows("l", ["only_one"]))).toBe(false);
});
