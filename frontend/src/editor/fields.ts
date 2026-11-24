// Word fields evaluated per page in the page view (headers/footers): PAGE, NUMPAGES, DATE/TIME. Everything else keeps its cached result.
export type FieldCtx = { page: string; pages: string; now: Date };

const MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];
const p2 = (n: number) => String(n).padStart(2, "0");

/** Word date picture (`\@ "dd.MM.yyyy HH:mm"`) -> text; unknown letters stay literal. */
export function datePicture(pic: string, d: Date): string {
  return pic.replace(/yyyy|yy|MMMM|MMM|MM|M|dd|d|HH|H|hh|h|mm|m|ss|AM\/PM|am\/pm/g, (t) => {
    const h12 = d.getHours() % 12 || 12;
    return ({ yyyy: String(d.getFullYear()), yy: p2(d.getFullYear() % 100), MMMM: MONTHS[d.getMonth()], MMM: MONTHS[d.getMonth()].slice(0, 3), MM: p2(d.getMonth() + 1),
      M: String(d.getMonth() + 1), dd: p2(d.getDate()), d: String(d.getDate()), HH: p2(d.getHours()), H: String(d.getHours()), hh: p2(h12), h: String(h12),
      mm: p2(d.getMinutes()), m: String(d.getMinutes()), ss: p2(d.getSeconds()), "AM/PM": d.getHours() < 12 ? "AM" : "PM", "am/pm": d.getHours() < 12 ? "am" : "pm" } as Record<string, string>)[t];
  });
}

/** Field instruction text (" PAGE \\* MERGEFORMAT ") -> replacement text, or undefined to keep the cached result. */
export function fieldValue(instr: string, ctx?: FieldCtx): string | undefined {
  if (!ctx) return undefined;
  const code = instr.trim().split(/\s+/)[0]?.toUpperCase();
  if (code === "PAGE") return ctx.page;
  if (code === "NUMPAGES") return ctx.pages;
  if (code === "DATE" || code === "TIME" || code === "SAVEDATE" || code === "CREATEDATE") {
    const pic = /\\@\s*"([^"]*)"/.exec(instr)?.[1];
    return pic ? datePicture(pic, ctx.now) : code === "TIME" ? datePicture("HH:mm", ctx.now) : datePicture("dd.MM.yyyy", ctx.now);
  }
  return undefined;
}

const ROMAN: [number, string][] = [[1000, "M"], [900, "CM"], [500, "D"], [400, "CD"], [100, "C"], [90, "XC"], [50, "L"], [40, "XL"], [10, "X"], [9, "IX"], [5, "V"], [4, "IV"], [1, "I"]];
/** Page number in a w:pgNumType format. */
export function pageLabel(n: number, fmt: string): string {
  if (fmt === "upperRoman" || fmt === "lowerRoman") {
    let r = "", x = n;
    for (const [v, s] of ROMAN) while (x >= v) { r += s; x -= v; }
    return fmt === "upperRoman" ? r : r.toLowerCase();
  }
  if (fmt === "upperLetter" || fmt === "lowerLetter") {
    const c = String.fromCharCode(65 + ((n - 1) % 26)).repeat(Math.ceil(n / 26));
    return fmt === "upperLetter" ? c : c.toLowerCase();
  }
  return String(n);
}
