export class ApiError extends Error {
  constructor(public status: number, message: string, public detail?: unknown) { super(message); }
}

export function readCookie(name: string, cookie = document.cookie): string | undefined {
  return cookie.split("; ").find((c) => c.startsWith(name + "="))?.slice(name.length + 1);
}

export async function api<T = unknown>(path: string, method = "GET", body?: unknown): Promise<T> {
  const headers: Record<string, string> = {};
  if (method !== "GET") {
    if (!readCookie("docgen_csrf")) await fetch("/api/auth/csrf");
    headers["X-CSRF-Token"] = readCookie("docgen_csrf") ?? "";
  }
  const form = body instanceof FormData; // multipart: the browser sets the boundary header
  if (body !== undefined && !form) headers["Content-Type"] = "application/json";
  const r = await fetch("/api" + path, { method, headers, body: body === undefined ? undefined : form ? body : JSON.stringify(body) });
  if (!r.ok) {
    const d = (await r.json().catch(() => ({}))).detail;
    throw new ApiError(r.status, typeof d === "string" ? d : Array.isArray(d) ? d.map((e) => e.msg).join("; ") : d?.message ?? r.statusText, d);
  }
  return (r.status === 204 ? undefined : await r.json()) as T;
}

/** POST JSON, get the raw body back (PDF). Same CSRF handling as api(). */
export async function apiBlob(path: string, body: unknown): Promise<Blob> {
  if (!readCookie("docgen_csrf")) await fetch("/api/auth/csrf");
  const r = await fetch("/api" + path, { method: "POST", headers: { "Content-Type": "application/json", "X-CSRF-Token": readCookie("docgen_csrf") ?? "" }, body: JSON.stringify(body) });
  if (!r.ok) {
    const d = (await r.json().catch(() => ({}))).detail;
    throw new ApiError(r.status, typeof d === "string" ? d : r.statusText, d);
  }
  return r.blob();
}

export type Role = "owner" | "admin" | "editor" | "viewer";
export const ROLES: Role[] = ["viewer", "editor", "admin", "owner"];
export type Org = { id: string; name: string; role: Role };
export type Me = { user: { id: string; email: string; name: string }; org: Org | null };
export type Member = { user_id: string; email: string; name: string; role: Role };
export type Invite = { id: string; email: string; role: Role; status: string; expires_at: string; email_sent?: boolean };
export type Lookup = { org_name: string; email: string; role: Role; status: string; user_exists: boolean };
export const isAdmin = (r?: Role) => r === "admin" || r === "owner";

export type Folder = { id: string; parent_id: string | null; name: string };
export type SnippetRow = { id: string; folder_id: string; slug: string; title: string; head_sha: string | null; has_draft: boolean };
export type Doc = { type: "doc"; content?: unknown[] };
export type Style = { id: string; style_id: string; name: string; kind: "paragraph" | "heading" | "character" | "table"; level: number | null;
  color: string | null; font_size: number | null; font_family: string | null; numbering: "none" | "decimal";
  space_before: number; space_after: number; bold: boolean; italic: boolean; builtin: boolean };
export type Draft = { snippet_id: string; title: string; slug: string; content: Doc; styles: string[]; variables: string[];
  exists: boolean; base_sha: string | null; head_sha: string | null; conflict: boolean; dirty: boolean };
export type Commit = { sha: string; author_name: string; author_email: string; date: string; message: string; snippet_ids: string[]; tags: string[] };
export type Change = { kind?: "document"; document_id?: string | null; snippet_id: string | null; slug: string; title: string; status: "new" | "modified"; conflict: boolean; text: string;
  summary: { added: number; removed: number; changed: number; unchanged: number }; three_way: ThreeWay | null };
export type ThreeWay = { snippet_id: string; slug: string; base: { content: Doc } | null; theirs: { content: Doc } | null; mine: { content: Doc } };
export const pretty = (v: unknown) => JSON.stringify(v, null, 2);

// ---- milestone 4: base documents, documents, library, exports ----
export type BaseDocVersion = { id: string; sha: string; number: number; filename: string; size: number; message: string;
  placeholders: { content: boolean; slots: string[] }; variables: VarDecl[]; created_at: string };
export type VarCandidate = { id: string; kind: string; value: string; locs: Record<string, number>; name: string; include: boolean };
export type BaseDoc = { id: string; name: string; head: BaseDocVersion | null; version_count: number; used_by: number; created_at: string; updated_at: string };
export type Pin = { type: "commit" | "tag" | "latest"; value: string | null };
export type Block = { id: string; slot: string } & (
  | { type: "snippet_ref"; folder_id: string; snippet_id: string; version: Pin }
  | { type: "heading"; text: string; level: number; styleId: string | null; numbered: boolean }
  | { type: "page_break" } | { type: "toc" });
export type LinkedDoc = { title: string; doc_id: string | null; version: string | null; url: string | null };
export type VarDecl = { name: string; type: "text" | "date" | "number" | "list"; default: unknown; columns?: string[] };
export type Variables = { declarations: VarDecl[]; values: Record<string, unknown> };
export type RevRow = { version: string; date: string; author: string; message: string };
export type DocBody = { title: string; slug: string; base_document: { id: string; version: string | null } | null; blocks: Block[];
  variables: Variables; links: LinkedDoc[]; revision_history: RevRow[] | null };
export type DocFull = DocBody & { id: string; folder_id: string; head_sha: string | null; has_draft: boolean; dirty?: boolean; conflict?: boolean };
export type DocRow = { id: string; folder_id: string; slug: string; title: string; head_sha: string | null; has_draft: boolean };
export type RefStatus = { block_id: string; folder_name: string | null; title: string | null; slug: string | null; missing: boolean; pin: Pin;
  resolved_sha: string | null; latest_sha: string | null; newer_commits: number; update_available: boolean };
export type LibraryGroup = { folder_id: string; folder_name: string; tags: string[]; snippets: { id: string; title: string; slug: string; head_sha: string }[] };
export type ExportStatus = { id: string; status: "queued" | "running" | "done" | "error"; error: string | null; warnings: string[] };
