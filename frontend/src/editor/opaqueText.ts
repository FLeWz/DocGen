// Visible text of opaque OOXML blocks: edit the text of a text box in place (shape XML stays verbatim) and "Convert to text".
const W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main";
const MC = "http://schemas.openxmlformats.org/markup-compatibility/2006";
const kids = (e: Element, name: string) => Array.from(e.children).filter((c) => c.namespaceURI === W && c.localName === name);
const parse = (xml: string) => { const d = new DOMParser().parseFromString(xml, "application/xml"); return d.getElementsByTagName("parsererror").length ? null : d; };
const inFallback = (e: Element) => { for (let a: Element | null = e; a; a = a.parentElement) if (a.namespaceURI === MC && a.localName === "Fallback") return true; return false; };
const closestP = (e: Element) => { for (let a = e.parentElement; a; a = a.parentElement) if (a.namespaceURI === W && a.localName === "p") return a; return null; };

export type Line = { text: string; style: string | null };
/** Text of the paragraph itself (not of paragraphs nested in its text boxes), then those nested ones. Field results count, deleted text and the VML fallback copy do not. */
function lines(p: Element, out: Line[]) {
  let text = "";
  for (const e of Array.from(p.getElementsByTagNameNS(W, "*"))) {
    if (inFallback(e) || closestP(e) !== p) continue;
    if (e.localName === "t") text += e.textContent ?? "";
    else if (e.localName === "tab") text += "\t";
  }
  out.push({ text, style: kids(p, "pPr")[0] ? kids(p, "pPr")[0].getElementsByTagNameNS(W, "pStyle")[0]?.getAttributeNS(W, "val") ?? null : null });
  for (const q of Array.from(p.getElementsByTagNameNS(W, "p"))) if (closestP(q) === p && !inFallback(q)) lines(q, out);
}

/** Paragraphs of the first text box (the `mc:Choice` one when there is a VML fallback copy), or null when the block has no text box. */
export function textboxLines(xml: string): string[] | null {
  const d = parse(xml), box = d && Array.from(d.getElementsByTagNameNS(W, "txbxContent")).find((b) => !inFallback(b));
  return box ? kids(box, "p").map((p) => { const o: Line[] = []; lines(p, o); return o[0].text; }) : null;
}

function setText(p: Element, text: string) {
  const doc = p.ownerDocument, run = Array.from(p.getElementsByTagNameNS(W, "r")).find((r) => closestP(r) === p && r.getElementsByTagNameNS(W, "t").length) ?? doc.createElementNS(W, "w:r");
  for (const c of Array.from(p.children)) if (!["pPr", "bookmarkStart", "bookmarkEnd"].includes(c.localName) && c !== run) c.remove();
  for (const c of Array.from(run.children)) if (c.localName !== "rPr") c.remove();
  text.split("\t").forEach((part, i) => {
    if (i) run.appendChild(doc.createElementNS(W, "w:tab"));
    const t = doc.createElementNS(W, "w:t"); t.setAttributeNS("http://www.w3.org/XML/1998/namespace", "xml:space", "preserve"); t.textContent = part; run.appendChild(t);
  });
  if (run.parentNode !== p) p.appendChild(run);
}

/** The block's XML with the paragraphs of its text box(es) replaced by `texts` (one paragraph each; formatting of the paragraph and its first run kept). Everything outside w:txbxContent is untouched. */
export function withTextboxLines(xml: string, texts: string[]): string | null {
  const d = parse(xml); if (!d) return null;
  const boxes = Array.from(d.getElementsByTagNameNS(W, "txbxContent"));
  if (!boxes.length) return null;
  for (const box of boxes) {
    const ps = kids(box, "p"); if (!ps.length) continue;
    texts.forEach((t, i) => { const p = ps[i] ?? (ps.push(ps[ps.length - 1].cloneNode(true) as Element), box.appendChild(ps[ps.length - 1]), ps[ps.length - 1]); setText(p, t); });
    ps.slice(Math.max(texts.length, 1)).forEach((p) => p.remove());
    if (!texts.length) setText(ps[0], "");
  }
  return new XMLSerializer().serializeToString(d.documentElement);
}

/** What the block shows as text, one entry per paragraph (for "Convert to text"). */
export function visibleLines(xml: string): Line[] {
  const d = parse(xml), out: Line[] = [];
  if (!d) return out;
  const root = d.documentElement;
  if (root.namespaceURI === W && root.localName === "p") lines(root, out);
  else for (const p of Array.from(root.getElementsByTagNameNS(W, "p"))) if (!closestP(p) && !inFallback(p)) lines(p, out);
  return out.filter((l) => l.text.trim());
}

export const OPAQUE_WHY: Record<string, string> = {
  "text box": "Text box: use ✎ Edit text to change its text; the shape and position stay as they are.",
  "content control": "Content control (form element around blocks): kept as is. Convert to text to edit its content.",
  "form field": "Form field (FORMTEXT): kept as is. Convert to text to edit it.",
  field: "Field that the editor cannot hold: kept as is.",
  "section break": "Section break (page layout): kept as is.",
  bookmark: "Invisible bookmark marker: kept as is.",
  "embedded object": "Embedded document/OLE object: kept as is.",
  equation: "Equation: kept as is.",
};
export const opaqueWhy = (kind: string) => OPAQUE_WHY[kind] ?? `Not editable (${kind}): kept as-is in the exported DOCX.`;
