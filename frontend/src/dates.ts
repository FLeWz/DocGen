/** The only place that turns a date into text: DD.MM.YYYY (date) or DD.MM.YYYY HH:mm (datetime, 24 h, browser time zone). No locale. */
const p2 = (n: number) => String(n).padStart(2, "0");
const ISO_DAY = /^(\d{4})-(\d{2})-(\d{2})$/;
const DMY = /^(\d{1,2})\.(\d{1,2})\.(\d{4})$/;

const real = (y: number, m: number, d: number) => { const t = new Date(Date.UTC(y, m - 1, d)); return t.getUTCFullYear() === y && t.getUTCMonth() === m - 1 && t.getUTCDate() === d; };
const dmy = (y: number, m: number, d: number) => `${p2(d)}.${p2(m)}.${String(y).padStart(4, "0")}`;

function asDate(v: unknown): Date | null {
  const d = v instanceof Date ? v : typeof v === "number" || (typeof v === "string" && v.trim()) ? new Date(v as string | number) : null;
  return d && !isNaN(d.getTime()) ? d : null;
}

/** Date: date-only values (`2026-11-24`, `24.11.2026`) use their own parts (never shifted by time zone); timestamps use local parts. "" for null. */
export function formatDate(v: unknown): string {
  if (v === null || v === undefined || v === "") return "";
  if (typeof v === "string") {
    const s = v.trim(), i = ISO_DAY.exec(s), m = DMY.exec(s);
    if (i && real(+i[1], +i[2], +i[3])) return dmy(+i[1], +i[2], +i[3]);
    if (m && real(+m[3], +m[2], +m[1])) return dmy(+m[3], +m[2], +m[1]);
  }
  const d = asDate(v);
  return d ? dmy(d.getFullYear(), d.getMonth() + 1, d.getDate()) : String(v);
}

export function formatDateTime(v: unknown): string {
  const d = v === null || v === undefined || v === "" ? null : asDate(v);
  return d ? `${dmy(d.getFullYear(), d.getMonth() + 1, d.getDate())} ${p2(d.getHours())}:${p2(d.getMinutes())}` : formatDate(v);
}

/** What the user typed -> the ISO date stored in the document (`iso` null = empty), or an inline error. Accepts D.M.YYYY and YYYY-MM-DD. */
export function parseDateInput(text: string): { iso: string | null; error?: string } {
  const s = text.trim();
  if (!s) return { iso: null };
  const m = DMY.exec(s), i = ISO_DAY.exec(s);
  const [y, mo, d] = m ? [+m[3], +m[2], +m[1]] : i ? [+i[1], +i[2], +i[3]] : [0, 0, 0];
  if (!y) return { iso: null, error: "Use DD.MM.YYYY" };
  return real(y, mo, d) ? { iso: `${String(y).padStart(4, "0")}-${p2(mo)}-${p2(d)}` } : { iso: null, error: "Not a real date" };
}

const PIC: Record<string, (d: Date) => string> = {
  YYYY: (d) => String(d.getFullYear()), MM: (d) => p2(d.getMonth() + 1), DD: (d) => p2(d.getDate()), HH: (d) => p2(d.getHours()), mm: (d) => p2(d.getMinutes()), ss: (d) => p2(d.getSeconds()),
  "%Y": (d) => String(d.getFullYear()), "%y": (d) => p2(d.getFullYear() % 100), "%m": (d) => p2(d.getMonth() + 1), "%d": (d) => p2(d.getDate()), "%H": (d) => p2(d.getHours()), "%M": (d) => p2(d.getMinutes()), "%S": (d) => p2(d.getSeconds()),
};
/** The `date:<fmt>` variable filter as the backend applies it (YYYY MM DD HH mm ss or strftime); empty format = DD.MM.YYYY. Date-only values. Unparseable value: unchanged. */
export function applyDateFilter(v: string, fmt: string): string {
  const day = formatDate(v), m = DMY.exec(day);
  if (!m) return v;
  if (!fmt.trim()) return day;
  const d = new Date(+m[3], +m[2] - 1, +m[1]);
  return fmt.replace(/YYYY|MM|DD|HH|mm|ss|%[YymdHMS]/g, (t) => PIC[t](d));
}

/** Today as a local ISO day (`toISOString` would give the UTC day). */
export const todayIso = () => { const d = new Date(); return `${d.getFullYear()}-${p2(d.getMonth() + 1)}-${p2(d.getDate())}`; };
