# UI: information architecture and review

## Problems found in the first UI (screenshot review at 1440x900)
- One long page: dashboard, import list, base documents, style set, members and invites stacked under the editor; no navigation, no URLs (only `/accept-invite`), so no deep links, no back button, state lost on reload.
- Unstyled browser defaults: serif body font, default buttons, no spacing scale, no focus styling, no colour system, no empty/loading states (blank or "Loading…").
- `prompt()`, `confirm()` for folders, links, dynamic tables and every delete; errors as inline red text.
- Folder tree: emoji + links, actions (rename/move/delete) as three buttons for the selected folder; "move" asks for a folder *name* in a prompt.
- Editor: ~20 identical text buttons in a wrapping row, no grouping, icons or sticky toolbar; the page is far below the fold; history, used-by and commit are separate sections far from the editor.
- Commit tab: raw `git diff` text in a `<pre>`; conflict resolution is a JSON textarea; the commit checkbox is not distinguishable from other checkboxes (this caused the e2e flake below).
- Document builder: unstyled list of blocks, tiny drop zones, native selects inline in rows, right panel of raw inputs; missing variables are a bare `<ul>`.
- Org settings: `<ul>` of invites, members table without header, style set hidden in a `<details>` with a 11-column table of bare inputs.

## Information architecture (react-router)
| URL | Screen |
|---|---|
| `/login`, `/accept-invite?token=` | Auth (outside the shell) |
| `/` | Dashboard: quick actions, recent snippets/documents/imports, "continue in <last folder>" |
| `/folders/:id` | Folder: snippets, documents, search, status badges, create buttons |
| `/snippets/:id` | Snippet editor (+ right panel: versions, used by) |
| `/documents/:id` | Document builder (library, blocks, right panel: variables, base document, links, revision history) |
| `/imports`, `/imports/:id` | Imports: dropzone + list; DOCX editor with actions bar and warnings |
| `/commit/:folderId` | Changed files, side-by-side diff, message, 3-pane conflict resolver |
| `/history/folder/:id`, `/history/snippet/:id`, `/history/document/:id` | History, diff between two commits, restore, tags; document rows: Download DOCX/PDF of that commit (cached badge); every row: Rename (pencil, prompt: Enter saves, Esc cancels, Reset to original) + amber `renamed` badge whose tooltip shows the original message and who/when; same Rename on the snippet side-panel versions and the base-document versions list (admin) |
| `/base-documents` | Base documents (round 6): table (name, current version, placeholders, variables, used by, created/updated), Details (versions, download, used-by, variables editor, Find variables), rename, new version, delete, create (upload / default template / from an import); read-only for non-admins |
| `/styles` | Style set editor |
| `/settings/{general,members,invites}` | Org settings; `general` holds the danger zone. `/settings/base-documents` and `/settings/styles` redirect to the two pages above |

Shell: top bar (product, org switcher, search over `/api/overview`, user menu) · left sidebar (folder tree with counts, context menu, collapsible nodes; side menu: Dashboard, Imports, Base documents, Styles, Settings) · main (breadcrumbs, tabs, page) · right context panel (collapsible, per page). The last open folder is remembered in `localStorage`.

## Test hooks
Stable `data-testid`s are used by `e2e/*.py`: `preview`, `word-pdf`, `save-status`, `doc-save-status`, `change-row`, `block`, `drop-zone`, `lib-item`, `bump-diff`, `missing-vars`, `import-warnings`, `import-msg`, `split-preview`, `slot-badge`, `var-candidates`, `base-doc-vars`, `base-vars`. The editor area contains no checkbox.

## e2e flake: root cause
`page.get_by_role("checkbox").first.check()` ran right after opening the commit tab, while the change list was still loading. The only checkbox on the page then was another one (the editor's "Page guides" / a block's "numbered"), which got ticked; later `Commit 1` never became enabled and `click` timed out after 30 s. Fix: rows carry `data-testid="change-row"` and the scripts wait for the row before ticking its own checkbox; the options that were checkboxes are toggle buttons.

## Pages, tabs, tables in the document view
- Editor and live preview show discrete pages. `paginate.ts` (pure) assigns blocks to pages from measured heights; the editor keeps ONE contenteditable and inserts non-editable page-gap widgets (footer + gap + next header) via `pageplugin.ts`; the preview (`pages.tsx`) clones blocks into paper cards. Per-section size/margins, titlePg and even/odd headers are honoured; PAGE/NUMPAGES are evaluated per page.
- Pagination is an ESTIMATE (no line splitting, widow/orphan control or floats): the toolbar says "N pages (est.)". On the example manual: 11 pages, same as LibreOffice (was 12 before the opaque-paragraph spacing fix). `scripts/compare-render.py <docx> <outdir>` renders both side by side (needs the stack, pdftoppm, Pillow).
- Tab stops (`pPr/tabs`, style tabs) are drawn in headers/footers/TOC/body (`tabs.ts`; tab-width decorations in the editor). Table styles (conditional formats, tblLook, borders, margins) are resolved by the backend into a derived per-cell `eff` attr (never exported); direct formatting stays in `cfmt`/`tfmt`/`rfmt`.
- Import warnings and editor notices share one collapsed `NoticeBar`.

## Base documents (round 5A)
- Import page: badge `{{ CONTENT }}` set / none, buttons `{{ CONTENT }}` (Ctrl+Alt+C, inserts a dashed `p[data-slot]` at the cursor), *Insert slot…*, *Find variables*. The variables modal (`builder/FindVariables.tsx`) lists Use / Where / Detected value / Variable name; *Use as base document* embeds the same table. Settings > Base documents > Versions shows the head version's variables (editable defaults, list defaults as rows); the document builder shows them under *Base document*.

## Editor ribbon and structure (round 5B)

- Ribbon with tabs Home / Insert / Layout / References / Review (`data-testid="ribbon"`, collapsible). Tooltips carry the shortcut.
- View bar: `Select blocks` (gutter handles), `N blocks selected` (`data-testid="sel-count"`), `Structure` toggle (sidebar `data-testid="structure"`: headings, marked sections with rename/jump/unmark, lists, captions).
- Click a page header/footer in the editor pane to open `hf-editor` (section / part / variant selects, a RichEditor box `hf-box`, Done). Page headers update live.

## Round 6: base document management, split picker
- Side menu order: Dashboard · Imports · Base documents · Styles · Settings (NavLink active state; works with the sidebar toggle and at 1100 px: row actions are icon buttons with titles/aria-labels, dates are day-only with the full time as tooltip).
- Name clash (create, upload, *Use as base document*): modal "A base document named X already exists. Create a new version of it instead?" with *Create new version* / *Choose another name* / *Cancel* (`data-testid="name-conflict"`). The default template has no file to version with, so only the other two buttons show. Rename clashes show an error toast.
- Delete: modal fetches `GET /base-documents/{id}/usage`; while documents (committed or draft) use it the list is shown with links and the button stays disabled (`data-testid="in-use"`).
- Split by headings (`SplitDialog.tsx`, `splitting.ts`): outline of every heading (indented by level, block count, characters) with a checkbox per heading, All / None / Level 1 / 1–2 / 1–3, editable snippet title, optional *Preface* snippet, live result list (`data-testid="split-preview"`), folder select. Checkboxes live in this modal only. Grouping is computed client-side by `groupSections` (Vitest) and again server-side on apply.
- Insert tab table-size select: label "Rows × cols", fixed 112 px width (was clipped at 90 px).

## Round 7
- Modal sizes: `Modal size="sm|md|wide"` (default md = min(720px, 92vw)); `.scrollbox` for long tables inside dialogs.
- Ribbon: *Dynamic table* (`Make dynamic table`) in Insert > Tables and Layout > Table; dialog with list variable and optional row fields; table badge `dynamic: <list>`; the variable dialog lists `row.*` fields (`data-testid="row-chips"`).
- Document builder: toolbar *Heading, Paragraph, TOC, List of tables, List of figures, Page break*. A paragraph block holds an inline editor (`data-testid="para-editor"`: style dropdown, bold/italic, lists, variable). *Preview* opens the PDF render first (`data-testid="word-pdf"`, `pdf-preview`, `pdf-cached`, `preview-missing`), tab *Draft view* = the HTML preview (`preview`).
- Header/footer editor box has the page's text width and no horizontal padding; tables wider than the page are clipped by the backend.

## Round 8: table editing
- **Contextual Table tab** (ribbon, appears and is selected while the cursor is in a table, the previous tab comes back when leaving it): rows and columns (insert above/below/left/right, delete, delete table with a confirm modal, duplicate row/column, move row up/down and column left/right), merge/split, header row (repeats on every page), clear contents, cell alignment (horizontal/vertical), shading, border mode (all/outside/inside/none/top/bottom/left/right/inside H/V) with line style, width and colour, cell padding, table alignment, autofit (to contents / to window / fixed), table width (unit or % of page), distribute columns/rows evenly, table style picker (styles of the document or org style set), row height (at least/exactly), *Can break* across pages, sort A→Z / Z→A by the cursor's column, table to text (tab separated; *Text to table* is in Insert > Tables), table caption (SEQ Table, the existing caption dialog), units (cm default, in, pt: `localStorage docgen.unit`).
- **Floating toolbar** (`data-testid="table-toolbar"`) above the table whose cell holds the cursor: insert row/column, delete row/column, merge, split, delete table, *Dynamic rows…*. **Context menu**: right-click in a table, or Shift+F10 / the context-menu key (`docgen:tablemenu`); arrows move, Enter activates, Esc closes and returns to the editor.
- **Direct manipulation** (overlay `TableTools.tsx`, shown for the table under the pointer or cursor; handles are plain `div role=button`, never inputs): drag a column border (`col-resize-N`; default the neighbour absorbs, **Shift** = only this column and the columns to its right are rescaled to keep the table width, **Alt** = no snapping (0.1 cm raster), minimum 0.63 cm, the last border changes the table width, double-click = autofit that column), drag a row border (`row-resize-N`, sets the row height), drag the square at the bottom right (`table-resize`, scales the columns). A guide line and a tooltip with the size in the unit and the other two (`drag-guide`, `drag-tooltip`) follow the pointer; the model changes once on release (one undo step). Column strips above (`col-handle-N`) and row strips left (`row-handle-N`) select a column/row (Shift = range), the corner square (`table-select`) the whole table. Tab / Shift+Tab move between cells, Tab in the last cell adds a row, **Alt+Shift+arrows** move the row/column.
- *Make dynamic table* (Insert / Layout, the toolbar, the context menu): on a table with content the dialog proposes **header cell → variable** (slug, `col_N` for empty headers, duplicates numbered, editable, live list with the first default value), **body rows → default values** of the list variable, the header stays static, the first body row stays as the formatted template (`{{ row.column }}` chips with the cell's run formatting). Merged cells in header/body are **refused** with an explanation (not flattened). *Undo dynamic* rebuilds the static rows from the defaults. Deleting the template row of a dynamic table asks first; *insert below* the template row inserts above it so the template stays last.
- Zoom list has 150 %.

## Round 9
- **Document builder columns** (round 10): `library | handle | blocks | handle | right panel`. Blocks (`.blocks`) are >= 600 px and take all the remaining width, no max-width and no gap. Library default 320 px (min 220, max half the width), right panel default 500 px (min 360); the sidebar is 200 px. Handles (`resize-lib`, `resize-right`, 6 px, `role=separator`, `aria-valuenow/min/max`) drag with pointer capture, arrow keys move 16 px, Home or double-click reset; wanted widths persist per user in localStorage (`docgen.builder.libW.<user id>`, `docgen.builder.rightW.<user id>`). When the window is too narrow the right panel shrinks to its minimum first, then the library (stored widths unchanged); below that the main column scrolls horizontally rather than squeezing the blocks. <= 1280 px: the panel overlays the page (fixed, below the top bar; *Details* toggles it) and has no handle; the library still resizes. Logic: `builder/layout.ts` (`fit`, `limits`; Vitest `layout.test.ts`), `builder/Resizer.tsx`, hook `useBuilderWidths` in `pages/Document.tsx`. Test: `e2e/round9.py`.
- **Base documents**: *Edit* (admins) in Base documents > Details and next to the builder's base picker opens the import editor on the base (`data-testid="edit-base"`). Bar `base-edit-bar` (version it started from, `base-diff`, `base-stale`), toolbar *Versions* (`base-versions`: download, *Restore into draft*), *Discard draft*, *Commit…* (message, change list, result `base-committed`; conflict `base-conflict` with *Commit anyway (override)*). *Split* and *Use as base document* are hidden in a session. The builder shows `base-newer` with *Use vN*.
- **Insert > Tables > Revision table** (every editor, not in headers/footers).
- **Embedded documents** are drawn as their preview picture (raster) or a labelled box (`.oleobj`).
- **Scrollbars**: thin, themed (see DECISIONS round 9).

## Round 12: snippets from an import

- **Create snippet from selection**: a title that exists in the folder opens `ConflictModal` (`data-testid="snippet-conflict"`): "A snippet titled “X” already exists in <folder> (last commit dd.mm.yyyy by Name)": *Update it (new draft version)*, *Create a separate snippet*, *Choose another title*, *Skip*. Your own unsaved draft on it asks "Replace your unsaved draft?" first.
- **Split by headings**: each row (`split-row`) shows a badge *new* / *exists*, for existing ones a select (Update existing, default / Create new (suffix) / Skip); `split-summary` = "N new · N updated (as drafts, not committed) · N skipped"; the button reads "Apply (N new, M updated)".
- **Update snippets from this import…** (import page) / **Update from import…** (snippet page): `UpdateFromImport.tsx` (`update-dialog`, `update-row`, `update-summary`): import picker (any import, e.g. a newer upload), folder, Level 1/1-2/1-3, per heading a checkbox, target snippet select (auto-match, "Create new", "Skip"), badge with the change count or "no changes" (unticked by default). *Apply to drafts (N)* writes drafts only; the toast links to the draft and to the folder's Commit screen (`?select=` accepts a comma list). On the snippet page the rows are radios: one heading replaces that snippet's draft.
- Testids: `snippet-conflict`, `split-row`, `split-summary`, `update-dialog`, `update-row`, `update-summary`. Toasts accept links (`ui.toast(msg, kind, [{label, to}])`).

## Round 15

- Login (`/login`): in `AUTH_MODE=oidc` only a "Sign in with <provider>" link (errors from the callback arrive as `?error=`); accept-invite shows the same link. Login 429 text: "too many failed login attempts ... try again in N min".
- Settings > Invites: "Send test email" next to "Send invite" (admins), shows the exact SMTP error in the banner.

## Round 16: edit locks and revision sections

- **Editing = lock.** Opening a snippet or document (editors and up) acquires its edit lock. Held by someone else: the editor is read-only and the lock bar (`lock-bar`, `data-state` = `readonly | pending | denied | transferred | granted`) says "X is editing since HH:mm" with *Request edit rights* (`request-edit`). Pending: countdown (`lock-countdown`, server clock); denied / within the cooldown the button is disabled and the bar says "You can request again at HH:mm (DD.MM.YYYY)". Admins see *Take over* (`take-over`, confirm dialog). After the rights moved away the bar says "Edit rights given to X" (you granted) or "Edit rights were transferred to X; your last changes were saved to the draft" (timeout / take over) and *Copy my unsaved changes* (`copy-unsaved`: clipboard, else a JSON download); the editor keeps its local content.
- **Holder side.** A modal (`lock-request-modal`, `lock-grant`, `lock-deny`, `lock-incoming-countdown`) opens in the open editor within a poll (4 s). Grant first saves the editor, then moves the lock; the requester's editor reloads the shared draft and becomes editable within a poll (<= 5 s).
- **Notifications.** Bell in the top bar (`bell`, unread count `bell-count`, panel `bell-panel`, polls every 10 s); an email goes to the holder if SMTP works.
- **Badges.** Amber `lock-badge` with the holder's name on folder rows, `tree-lock` icon on folders of the tree that contain a locked item (list refreshed every 15 s).
- **History.** Rows show `+ co-author names` (`co-authors`) and the lock hand-overs (`lock-transfers`); the revision table's *Edited by* is "committer, co-author, ...".
- **Revision table, sections.** Only headings, table captions and figure captions with the section number of the built document: `3.2.1 Heading`, `Table 4: Specs (3.2.1)`, `Figure 2: Layout (3.2.1)`, `Heading (added|removed)`; text before the first heading shows the snippet title; entries `; `-joined, at most 12, then `+N more`.
- Tests: `e2e/locks.py` (grant, deny, cooldown, badges, history; `locks.py timeout` runs in the throwaway stack of `make test-e2e-locks`), Vitest `lockstate.test.ts`.


## Editor view (round 18)
- `RichEditor` (snippets, imports, base documents): Preview mode `off` (default) / Live / Word PDF, Zoom 100% default; both remembered in localStorage (`docgen.editor.preview`, `docgen.editor.zoom`, helper `editor/prefs.ts`).
- Fullscreen/focus mode: button `data-testid="focus-toggle"`, Ctrl+Shift+F, Esc (not while a dialog/menu is open), `?focus=1`. Class `ed-focus` on `<html>` hides topbar, sidebar, right panel, page head; ribbon, title/save status/actions and the view bar stay. The `F11` button is the real browser Fullscreen API.
