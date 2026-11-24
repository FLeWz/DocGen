# Build "DocGen": a multi-tenant documentation builder (Docker web app)

You are building a complete, working web application called **DocGen**. It runs fully inside Docker (`docker compose up` must start everything). Users write reusable, versioned content blocks ("snippets"), then assemble them on top of a styled "base document" into full documents, which they export as DOCX and PDF.

Read the whole spec before writing code. Where the spec leaves a choice open, pick the simplest option that meets it and write the decision down in `docs/DECISIONS.md`.

---

## 1. Tech stack (use this unless something blocks you)

| Layer | Choice | Why |
|---|---|---|
| Backend | Python 3.12, FastAPI, SQLAlchemy 2, Alembic | Best DOCX libraries live in Python |
| DB | PostgreSQL 16 | Row-level security for tenant isolation |
| Versioning | Real git via `pygit2` (or the `git` CLI), bare repos on a Docker volume | Spec requires git |
| DOCX read/write | `python-docx`, `docxtpl` (Jinja in DOCX), `docxcompose` (merge) | Keeps Word styles |
| PDF | LibreOffice headless (in its own container, e.g. Gotenberg) converts the final DOCX to PDF | PDF matches DOCX exactly |
| Frontend | React + TypeScript + Vite, TipTap (ProseMirror) editor, TanStack Query | Block editor with custom nodes |
| Auth | Email + password (argon2), HTTP-only session cookie, CSRF token | Simple, safe |
| Mail | SMTP settings via env; in dev use Mailpit container to catch invite emails | Testable invites |

Containers in `docker-compose.yml`: `web` (frontend served by nginx), `api`, `db`, `pdf` (Gotenberg/LibreOffice), `mail` (Mailpit, dev only). Volumes: `pgdata`, `repos` (git), `files` (uploads, base docs, images).

---

## 2. Multi-tenancy, users, invites

- **Organization** has many **Users** through **Membership** (`role`: `owner`, `admin`, `editor`, `viewer`).
- A user can belong to several orgs and switch the active org in the UI. The active org is stored in the session.
- Sign-up creates a user and a new org (user becomes `owner`).
- **Invites**: owner/admin enters an email + role → app creates a single-use token (expires in 7 days) and emails a link. Opening the link: existing user → joins org after login; new user → sign-up form, then joins. Admins can list, resend, revoke invites and change/remove members.
- **Isolation is a hard requirement**:
  - Every tenant table has `org_id NOT NULL`.
  - All queries go through a repository/service layer that always filters by the session's active `org_id`. No endpoint takes `org_id` from the client.
  - Also enable Postgres Row-Level Security on tenant tables (`SET app.org_id` per request) as a second wall.
  - Git repos and files are stored under `/{org_id}/...`; paths are built server-side only, never from user input.
  - Accessing another org's object returns **404** (not 403), so IDs don't leak.
  - Write automated tests that create two orgs and prove that org B cannot read, list, edit, export, link to, or commit into anything from org A (every endpoint).
- Permissions: `viewer` reads and exports; `editor` also edits, commits, builds; `admin` also manages members/invites/base documents; `owner` also deletes the org.

---

## 3. Core concepts and data model

### Folders
- Org-scoped tree of folders (nested allowed). Users build their documentation inside folders.
- **Each folder is backed by its own bare git repository** (`/repos/{org_id}/{folder_id}.git`). The folder is the unit of versioning.

### Snippets
- A snippet is a chapter/section of content. **Every snippet belongs to exactly one folder and is always referenced at a specific version.**
- Stored in the folder's git repo as files, e.g.:
  ```
  snippets/<snippet-slug>/content.json   # ProseMirror/TipTap JSON (source of truth)
  snippets/<snippet-slug>/meta.yaml      # title, id (uuid), styles used, variables declared
  snippets/<snippet-slug>/assets/*       # images
  ```
- The snippet's id (uuid) is stable; the slug may change (rename = git move).
- DB keeps an index (id, folder_id, slug, title, last commit sha) for fast listing/search; git is the source of truth for content.

### Versioning with git ("commit" workflow)
- Editing a snippet saves to a **draft** (per user, stored in DB, autosaved). Drafts are not versions.
- A **Commit** screen per folder shows changed snippets (diff of draft vs HEAD, rendered side-by-side and as text), lets the user pick which to include, enter a message, and commit. Author = the user's name/email. One git commit per action.
- Show history per snippet and per folder (`git log`), view any old version, diff two versions, restore an old version into a draft.
- Users can **tag** a commit (e.g. `v1.2`) to give it a friendly version name.
- Handle conflicts: if HEAD moved since the draft was started, show a 3-way diff and let the user resolve before committing.

### Sharing snippets across folders
- A document in folder X can use snippets from folder Y. The reference is always explicit:
  ```json
  { "folder_id": "...", "snippet_id": "...", "version": { "type": "commit|tag|latest", "value": "sha-or-tag" } }
  ```
- The UI shows the source folder on every block, e.g. `Security/Intro @ v1.2`. Default pin is a fixed commit; "latest" is allowed but shown with a warning badge.
- Show "update available" when a pinned snippet has newer commits, with a one-click "bump to latest" (shows a diff first).
- Show "used by" on each snippet (which documents/versions reference it).

### Base documents (templates)
- An admin uploads a `.docx` (or builds one in the app) as a **base document**. It defines: page size/margins, styles (`styles.xml`), numbering, header/footer, logo, cover page, and fixed sections such as:
  - **Revision history table**
  - **Referenced/linked documents table** (title, id, version, link)
  - Approval/sign-off table, table of contents, etc.
- The base doc contains a placeholder (e.g. a paragraph `{{ CONTENT }}` or a content control) where the chapters go. It may contain more named slots (`{{ SLOT:appendix }}`).
- Base documents are also versioned (store them in an org-level git repo `/repos/{org_id}/_base.git`).

### Dynamic (templated) content
- In the snippet editor and in base documents, the user can **mark text or a table as dynamic**:
  - **Dynamic text**: select text → "Make variable" → becomes a chip `{{ project_name }}`. Supports defaults and simple filters (date format, upper).
  - **Dynamic table**: mark a table as bound to a list variable; one row is the row template, columns use `{{ row.field }}`. Rows repeat at build time.
  - Built-in variables: `doc.title`, `doc.version`, `doc.date`, `org.name`, `author`, `revision_history` (auto-built from the git log of the document and its snippets, editable before export), `linked_documents` (from the document's link list).
- Variables are declared per document (name, type: text/date/number/list-of-objects, default). The builder shows a form to fill them. Missing values are listed as errors before export.
- Implementation: render to DOCX with `docxtpl` (Jinja). Escape user values; use a sandboxed Jinja environment (no attribute access to Python internals).

### Documents (the builder)
- A **Document** lives in a folder and has: base document (pinned version), ordered list of blocks, variable values, linked-documents list, and its own version history (stored as `documents/<slug>.yaml` in the folder's git repo, committed like snippets).
- Block types: `snippet ref`, `heading` (custom styled, numbered or not), `page break`, `table of contents`, `slot` content.

---

## 4. Screens / UX

Keep the layout simple: left sidebar (org switcher, folder tree), main area, right panel for context.

1. **Snippet editor** – split view: editor left, live preview right (preview uses the same styles as the chosen base doc, rendered as HTML that mimics the DOCX styles). Toolbar: paragraph styles (taken from the base doc's style list, not free fonts), bold/italic/underline, lists, tables, images, links, "Make variable", "Mark table dynamic". Headings: pick level + a custom style (color, font size, numbering, spacing) defined as named styles in the base doc or org style set. Autosave draft indicator, "Commit…" button.
2. **Document builder** – block builder: left = searchable snippet library (all folders, grouped by folder, filter by tag/version); center = ordered blocks, drag & drop to add/reorder/remove; each snippet block shows folder, title, version pin, "update available" badge, and a version dropdown. Right = variables form, base document picker, linked documents editor, revision history preview. Buttons: **Preview**, **Download DOCX**, **Download PDF**, **Commit**.
3. **DOCX import / edit** – upload a `.docx` → parse into the editor (paragraphs with their style names, runs with formatting, tables, images, lists, headings). The user can:
   - edit the document directly and export it again (styles kept);
   - **select a range → "Create snippet from selection"** → choose folder + title → snippet is created as a draft with the original styles;
   - "Split by headings" → one snippet per Heading 1/2 (preview list first);
   - "Use as base document" → saves the file's styles/header/footer as a base doc.
   - Unknown Word elements must not be silently lost: keep them as opaque blocks (store raw OOXML) and write them back on export, and show a warning listing what could not be edited.
4. **History / diff** view for snippets, documents, base docs.
5. **Org settings** – members, roles, invites, org style set, base documents.

---

### Editor (round 5B)

Imports and snippets share one editor with a ribbon. Imported documents also edit their header/footer parts (stored as `content.hf`, written back on export, untouched parts byte-identical). Ranges of blocks can be marked as sections, appendices, TOC, list of figures or list of tables (bookmark ranges / SDT+field). Snippets can be created from any selection; the client sends the selected blocks. See DECISIONS round 5B.

## 5. Export pipeline (DOCX + PDF)

1. Load the pinned base document version.
2. For each block, load the snippet at its pinned commit from the right folder's repo (`git show <sha>:snippets/<slug>/content.json`).
3. Convert ProseMirror JSON → OOXML using the base doc's **style ids** (map paragraph style names; never inline fonts unless the user set direct formatting). Insert images, tables, lists (use the base doc numbering).
4. Insert into the `{{ CONTENT }}` placeholder (and named slots).
5. Render all variables / dynamic tables with `docxtpl`.
6. Fill revision history + linked documents tables.
7. Update fields (TOC, page numbers) – run LibreOffice to refresh fields, or mark fields dirty so Word updates on open.
8. DOCX → return file. PDF → send DOCX to the `pdf` container, return PDF.
- Exports are deterministic: same pins + same variables = same output. Record each export (who, when, all pins) so it can be reproduced.
- Long exports run as a background job (simple DB-backed job queue or RQ+Redis), UI polls status.
- Round-trip test: import a sample DOCX → export → styles.xml, numbering, header/footer and tables still match (write an automated test with a fixture DOCX that has custom styles, a revision table, a merged-cell table, images, numbered lists).

---

## 6. API (REST, JSON)

Design clean resource endpoints, e.g. `/api/auth/*`, `/api/orgs/current`, `/api/members`, `/api/invites`, `/api/folders`, `/api/folders/{id}/snippets`, `/api/snippets/{id}/draft`, `/api/folders/{id}/commit`, `/api/snippets/{id}/history`, `/api/snippets/{id}/diff?from=&to=`, `/api/documents`, `/api/documents/{id}/export?format=docx|pdf`, `/api/base-documents`, `/api/import/docx`. Generate OpenAPI docs. Validate every input with Pydantic. Limit upload size (e.g. 25 MB) and only accept real DOCX (check zip structure, not just the extension); guard against zip bombs and XXE when parsing XML (`defusedxml`).

---

## 7. Quality bar / acceptance criteria

The work is done when all of these pass:

- [ ] `docker compose up --build` on a clean machine starts the app at `http://localhost:8080`; migrations run automatically; a seed script creates a demo org, users, folders, snippets, a base doc and a document.
- [ ] Sign up, create org, invite a second user (email visible in Mailpit), accept, switch orgs.
- [ ] Two-org isolation test suite passes for every endpoint, git repo and file path.
- [ ] Create folder → write snippet → see live preview → commit → edit → commit again → view history and diff → restore old version.
- [ ] Use a snippet from folder A in a document in folder B; the folder is shown; pin to an old version; "update available" appears; bump works.
- [ ] Upload DOCX → create snippet from a selection → styles preserved in the exported DOCX.
- [ ] Base document with revision history table, linked documents table, dynamic text and a dynamic table; export fills them correctly.
- [ ] DOCX and PDF downloads open without errors in Word/LibreOffice; PDF looks like the DOCX.
- [ ] Backend tests (pytest) and frontend tests (Vitest + one Playwright end-to-end happy path) run in CI-like `make test` inside Docker.
- [ ] `README.md`: setup, env vars, architecture diagram (text), how versioning works, how to back up (`pgdata` + `repos` + `files` volumes).

---

## 8. How to work

1. Start with `docs/PLAN.md`: data model, folder/repo layout, API list, milestones. Then build in this order, keeping the app runnable after each step:
   1. Docker skeleton + auth + orgs + invites + isolation tests
   2. Folders + git-backed snippets + draft/commit/history/diff
   3. Snippet editor + preview + styles
   4. Base documents + document builder + DOCX export
   5. Dynamic variables/tables + revision history + linked docs
   6. PDF export
   7. DOCX import + snippet from selection + round-trip tests
   8. Polish, seed data, README
2. Write tests alongside each milestone, not at the end.
3. Keep it simple: no microservices beyond the containers listed, no premature abstractions. Prefer boring, well-known libraries.
4. Never commit secrets; read config from `.env` (ship `.env.example`).
5. When done, give a short summary: what works, known limits, and how to run it.