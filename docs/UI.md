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
| `/history/folder/:id`, `/history/snippet/:id`, `/history/document/:id` | History, diff between two commits, restore, tags |
| `/settings/{general,members,invites,base-documents,styles}` | Org settings; `general` holds the danger zone |

Shell: top bar (product, org switcher, search over `/api/overview`, user menu) · left sidebar (folder tree with counts, context menu, collapsible nodes; Imports and Settings links) · main (breadcrumbs, tabs, page) · right context panel (collapsible, per page). The last open folder is remembered in `localStorage`.

## Test hooks
Stable `data-testid`s are used by `e2e/*.py`: `preview`, `word-pdf`, `save-status`, `doc-save-status`, `change-row`, `block`, `drop-zone`, `lib-item`, `bump-diff`, `missing-vars`, `import-warnings`, `import-msg`, `split-preview`. The editor area contains no checkbox.

## e2e flake: root cause
`page.get_by_role("checkbox").first.check()` ran right after opening the commit tab, while the change list was still loading. The only checkbox on the page then was another one (the editor's "Page guides" / a block's "numbered"), which got ticked; later `Commit 1` never became enabled and `click` timed out after 30 s. Fix: rows carry `data-testid="change-row"` and the scripts wait for the row before ticking its own checkbox; the options that were checkboxes are toggle buttons.
