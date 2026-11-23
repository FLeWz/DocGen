export class ApiError extends Error {
  constructor(public status: number, message: string) { super(message); }
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
  if (body !== undefined) headers["Content-Type"] = "application/json";
  const r = await fetch("/api" + path, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
  if (!r.ok) {
    const d = (await r.json().catch(() => ({}))).detail;
    throw new ApiError(r.status, typeof d === "string" ? d : Array.isArray(d) ? d.map((e) => e.msg).join("; ") : r.statusText);
  }
  return (r.status === 204 ? undefined : await r.json()) as T;
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
export type Doc = { type: "doc"; content: unknown[] };
export type Draft = { snippet_id: string; title: string; slug: string; content: Doc; styles: string[]; variables: string[];
  exists: boolean; base_sha: string | null; head_sha: string | null; conflict: boolean; dirty: boolean };
export type Commit = { sha: string; author_name: string; author_email: string; date: string; message: string; snippet_ids: string[]; tags: string[] };
export type Change = { snippet_id: string; slug: string; title: string; status: "new" | "modified"; conflict: boolean; text: string;
  summary: { added: number; removed: number; changed: number; unchanged: number }; three_way: ThreeWay | null };
export type ThreeWay = { snippet_id: string; slug: string; base: { content: Doc } | null; theirs: { content: Doc } | null; mine: { content: Doc } };
export const pretty = (v: unknown) => JSON.stringify(v, null, 2);
