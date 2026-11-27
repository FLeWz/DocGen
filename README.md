# DocGen

Multi-tenant documentation builder. Write reusable, git-versioned content blocks (**snippets**) in folders, assemble
them on a styled **base document** into **documents**, export DOCX and PDF. Everything runs in Docker.

Spec: `docs/SPEC.md` · plan and status: `docs/PLAN.md` · design decisions: `docs/DECISIONS.md`.

## Setup (clean machine)

Needs Docker with Compose v2 (nothing else; no Node/Python on the host).

```sh
git clone <this repo> docgen && cd docgen
cp .env.example .env          # then change POSTGRES_PASSWORD, APP_DB_PASSWORD, SECRET_KEY
docker compose up --build -d  # or: make up   (copies .env.example if .env is missing)
```

Open <http://localhost:8080> (`WEB_PORT`). Migrations run on api start; with `SEED_DEMO=1` (the default in
`.env.example`) a demo org is created once. Mail sent by the app (invites) is caught by Mailpit at
<http://localhost:8025> (`MAIL_UI_PORT`). If a port is taken, change `WEB_PORT`/`MAIL_UI_PORT` **and** `PUBLIC_URL`
(invite links use it).

### Invite-only: first user (create accounts)

Public sign-up is **closed** (`ALLOW_SIGNUP=0`: `POST /api/auth/signup` answers 404, the login form hides the sign-up
button). New people join by accepting an invite. Create the first user + org on an empty install with the CLI. The
password comes from stdin or a prompt, never from argv. Email is trimmed and lowercased; the password is used exactly as
typed except that a trailing CR/LF (Windows shells, `echo`) is dropped; 10 to 128 characters. Success prints the
login URL; every failure exits non-zero with `error: ...`.

Linux / macOS:

```sh
# prompt (asks twice)
docker compose exec api python -m app.manage create-user --email you@example.com --name "You" --org "My Org"
# piped (note -T: without it Docker says "the input device is not a TTY")
printf '%s\n' 'a-long-password' | docker compose exec -T api python -m app.manage create-user \
    --email you@example.com --name "You" --org "My Org" --password-stdin
```

Windows PowerShell (Docker Desktop):

```powershell
# prompt (asks twice; the safest way, also for non-ASCII passwords)
docker compose exec api python -m app.manage create-user --email you@example.com --name "You" --org "My Org"
# piped here-string (CRLF is fine; PowerShell 5.1 sends non-ASCII as '?', so use the prompt for those)
@'
a-long-password
'@ | docker compose exec -T api python -m app.manage create-user --email you@example.com --name "You" --org "My Org" --password-stdin
```

cmd.exe: `echo a-long-password| docker compose exec -T api ...` (no space before `|`, otherwise the space becomes part of the
password; the CLI warns when a password ends with whitespace).

Other commands (same `docker compose exec api python -m app.manage ...`):

| Command | What it does |
|---|---|
| `reset-password --email you@example.com [--password-stdin]` | new password (prompt twice, or stdin), same rules; use it when someone cannot log in |
| `list-users` | email, name, number of orgs (`SSO only` for users without password); never prints hashes |
| `test-mail --to you@example.com` | send a test email with the current SMTP settings and print the exact error |

Set `ALLOW_SIGNUP=1` only on a throwaway dev instance. With `AUTH_MODE=oidc` see [docs/OIDC.md](docs/OIDC.md): `create-user` then makes a password-less user.

#### Troubleshooting: "I created an account but cannot log in"

Found by reproducing the documented commands (round 15, `docs/DECISIONS.md`):

| Symptom | Cause | Fix |
|---|---|---|
| Login says "invalid email or password" for a user made from PowerShell/cmd | The CRLF the shell appends was stored as part of the password. Fixed: the CLI and login drop a trailing CR/LF | `reset-password --email ...` for accounts made before the fix |
| Same, password with a trailing space | cmd.exe `echo pw \| ...` includes the space. Kept on purpose (spaces are legal), the CLI now warns | `echo pw\| ...` or the prompt |
| Email with a space/capital letter at creation | stored unnormalised, login looked up the trimmed lowercase address. Fixed: trim + lowercase everywhere | `list-users`; recreate or `reset-password` |
| 129+ character password accepted by the CLI, rejected at login (422) | different limits. Fixed: one policy (10 to 128) for CLI, API, invite accept, UI | `reset-password` |
| `the input device is not a TTY` | piped stdin without `-T` | add `-T`, or drop the pipe and use the prompt |
| "too many failed login attempts ... try again in N min" | login throttle (5 failures per client IP and email per 10 min) - not a wrong password | wait N minutes (shown) |
| Typo while typing the password at the prompt | used to be unconfirmed | the prompt now asks twice |
| Nothing works but the user exists | check `list-users`; `SEED_DEMO=1` users have the public password `demo-password-123` | `reset-password` |

### Demo data (dev only)

`SEED_DEMO=1` creates (idempotently; skipped if present) the org **Demo Org** with these users, all with password
`demo-password-123`:

| Email | Role |
|---|---|
| owner@example.com | owner |
| admin@example.com | admin |
| editor@example.com | editor |
| viewer@example.com | viewer |

Plus folders *Security*, *Operations* (with sub-folder *Runbooks*); snippets with several commits and tags
(`Security/Introduction` v1.0, v1.1), an *Escalation Contacts* snippet with a variable and a dynamic table; a bundled
*Default* base document; the document *Operations Manual* (typed variables incl. a list variable, linked documents,
two commits = revision history, `Security/Introduction` pinned to the old tag `v1.0` so "update available" shows,
a cross-folder pin); and an imported sample DOCX. **Set `SEED_DEMO=0` for anything but a dev machine** (the passwords
are public). Re-run by hand: `docker compose exec api python -m app.seed`.

## Environment variables

Set in `.env` (read by `docker-compose.yml`). No secrets are committed; `.env` is gitignored.

| Variable | Default | Meaning |
|---|---|---|
| `COMPOSE_PROJECT_NAME` | `docgen` | Compose project (volume names are `<project>_pgdata`, ...). `docker-compose.yml` also pins `name: docgen` |
| `WEB_PORT` | `8080` | Host port of the web UI (the only app port published) |
| `MAIL_UI_PORT` | `8025` | Host port of the Mailpit UI (dev) |
| `PUBLIC_URL` | `http://localhost:8080` | External URL, used in invite emails; must match `WEB_PORT` |
| `POSTGRES_USER` / `POSTGRES_DB` | `docgen` | Database owner (runs migrations) and database name |
| `POSTGRES_PASSWORD` | – (required) | Password of the owner role |
| `APP_DB_PASSWORD` | – (required) | Password of `docgen_app`, the unprivileged runtime role that RLS applies to |
| `SECRET_KEY` | – (required) | HMAC key for CSRF tokens; long and random |
| `COOKIE_SECURE` | `false` | `true` behind HTTPS (cookies get the `Secure` flag) |
| `ALLOW_SIGNUP` | `0` | `1` opens `POST /api/auth/signup` (anyone can create an org). Default: invite-only |
| `SEED_DEMO` | `1` in `.env.example` (`0` if unset) | Create demo data on api start |
| `AUTH_MODE` | `local` | `local` = email + password; `oidc` = OpenID Connect only (see [docs/OIDC.md](docs/OIDC.md) for all `OIDC_*` variables) |
| `SMTP_*` | Mailpit | Outgoing mail, see [Mail (SMTP)](#mail-smtp) |
| `LOCK_HEARTBEAT_TTL_S` | `90` | Edit lock expires after this many seconds without a heartbeat (the editor sends one every ~20 s) |
| `LOCK_REQUEST_TIMEOUT_S` | `300` | Seconds the lock holder has to Grant/Deny an edit-rights request before the rights move to the requester |
| `LOCK_REQUEST_COOLDOWN_S` | `1800` | A user may request edit rights on the same snippet/document once per this many seconds (from the creation of the request) |
| `LOCK_SWEEP_S` | `15` | Interval of the background pass that resolves timed-out requests when no tab is open |

Fixed inside the api container (not in `.env`): `DATABASE_URL`, `MIGRATE_DATABASE_URL`, `PDF_URL=http://pdf:3000`,
`REPOS_ROOT=/repos`, `FILES_ROOT=/files`. Optional app tuning: `PDF_TIMEOUT_S` (120), `PDF_MAX_BYTES` (100 MiB).
For production remove the `mail` service and point `SMTP_*` at a real server. Empty values in `.env` mean "unset" (default applies). `./certs/` is mounted read-only at `/certs` in the api container for private CA bundles.

## Sign-in with OpenID Connect

`AUTH_MODE=oidc` replaces the built-in passwords with your company's identity provider (Authorization Code + PKCE). Setup, every
`OIDC_*` variable, the callback URL to register (`<PUBLIC_URL>/api/auth/oidc/callback`), provisioning rules (accounts are auto-created; the role comes from the Keycloak client roles `owner/admin/editor/viewer` in the token; no role = refused), Keycloak / Entra ID / Okta /
Authentik / ADFS examples and troubleshooting: **[docs/OIDC.md](docs/OIDC.md)**. Default stays `AUTH_MODE=local`.
Logout ends the DocGen session first and then the IdP's SSO session (RP-initiated, `id_token_hint`); in Keycloak register `<PUBLIC_URL>/login` as a *Valid post logout redirect URI* (details in docs/OIDC.md, round 19).

## Mail (SMTP)

Invites are mailed through `SMTP_*` (default: the Mailpit container, no auth, no TLS). Mail is sent in a background
thread (invite creation waits at most 5 s; the invite exists either way, a failure is logged and shows as a toast).
**Settings > Invites > Send test email** (admins) or `python -m app.manage test-mail --to you@example.com` sends a
message now and reports the exact SMTP error (refused, timeout, `535 5.7.139` with a hint, untrusted certificate, ...).

| Variable | Default | Meaning |
|---|---|---|
| `SMTP_PRESET` | empty | `microsoft365` = `smtp.office365.com`:587, `starttls`, auth `login`; every other variable overrides it |
| `SMTP_HOST` / `SMTP_PORT` | `mail` / `1025` | server |
| `SMTP_SECURITY` | `none` | `none`, `starttls` (587) or `ssl` (implicit TLS, 465). Legacy `SMTP_STARTTLS=true` still means `starttls` |
| `SMTP_USERNAME` (`SMTP_USER`) | empty | login; empty = no authentication |
| `SMTP_PASSWORD` / `SMTP_PASSWORD_FILE` | empty | password, or a file in the container holding it (Docker secret) |
| `SMTP_AUTH` | `auto` | `auto` (smtplib picks what the server offers), `login`, `plain`, `xoauth2`, `none` |
| `SMTP_FROM` / `SMTP_FROM_NAME` | `DocGen <noreply@docgen.local>` / empty | sender address (+ display name). Microsoft 365: the mailbox itself or an alias with *Send As* |
| `SMTP_REPLY_TO` | empty | Reply-To header |
| `SMTP_TIMEOUT` | `15` | seconds, for connect and every reply |
| `SMTP_VERIFY_TLS` / `SMTP_CA_BUNDLE` | `true` / empty | certificate check; PEM bundle for a private CA (path inside the container, e.g. `/certs/ca.pem`) |
| `SMTP_EHLO_HOSTNAME` | system | name announced in EHLO |
| `SMTP_OAUTH_TENANT_ID` / `_CLIENT_ID` / `_CLIENT_SECRET` | empty | for `SMTP_AUTH=xoauth2` |

Microsoft 365 / Exchange Online with a mailbox password (basic auth):

```sh
SMTP_PRESET=microsoft365
SMTP_USERNAME=docgen@skylabs.si
SMTP_PASSWORD=<app password>
SMTP_FROM=docgen@skylabs.si
```

* SMTP AUTH must be enabled for the mailbox: `Set-CASMailbox -Identity docgen@skylabs.si -SmtpClientAuthenticationDisabled $false`
  (and not blocked tenant-wide, Exchange admin center > Settings > Mail flow).
* Security Defaults / MFA / Conditional Access block password SMTP: use an app password or exempt the account.
* Microsoft is retiring basic-auth SMTP AUTH for Exchange Online (the dates have moved more than once; check the Microsoft 365
  message center for the current one). Plan for OAuth2 below.

Microsoft 365 with OAuth2 (client credentials, `SMTP_AUTH=xoauth2`) - **implemented, not verified against a real tenant**:

1. Entra admin center > App registrations > New registration (single tenant). Note *Directory (tenant) ID* and *Application (client) ID*; add a client secret.
2. Exchange Online PowerShell: register the app as a service principal and give it the mailbox
   (use the **Enterprise application Object ID**, not the app registration's):
   `New-ServicePrincipal -AppId <client-id> -ServiceId <enterprise-app-object-id>` then
   `Add-MailboxPermission -Identity docgen@skylabs.si -User <enterprise-app-object-id> -AccessRights FullAccess`.
3. Permission `SMTP.SendAsApp` (Office 365 Exchange Online > Application permissions) with admin consent. Enable SMTP AUTH on the mailbox as above.
4. `.env`:

```sh
SMTP_PRESET=microsoft365
SMTP_AUTH=xoauth2
SMTP_USERNAME=docgen@skylabs.si
SMTP_FROM=docgen@skylabs.si
SMTP_OAUTH_TENANT_ID=<tenant id>
SMTP_OAUTH_CLIENT_ID=<client id>
SMTP_OAUTH_CLIENT_SECRET=<secret>
```

DocGen requests a token for scope `https://outlook.office365.com/.default` at `login.microsoftonline.com/<tenant>/oauth2/v2.0/token`,
caches it until one minute before it expires and authenticates with SASL XOAUTH2. Typical errors: `535 5.7.3 Authentication
unsuccessful` = missing mailbox permission or SMTP AUTH disabled; `AADSTS7000215` = wrong client secret.

## Looks like Word

The editor and every preview sit on a "page" with the page size, margins, header/footer, fonts, colours and paragraph
styles of the DOCX they belong to: an imported document uses its own package, a snippet uses the base document chosen
under **Look** (else the organization styles). The document builder's **Preview** shows the real PDF export (cached while the document is unchanged; *Draft view* = HTML). The editors open with **no preview at 100% zoom** (the last choice is remembered per browser: `docgen.editor.preview` / `docgen.editor.zoom`). **Fullscreen editor** (toolbar button or **Ctrl+Shift+F**, Esc exits, deep link `?focus=1`) hides the shell and keeps only the toolbar. With a preview open, **Follow** (on by default) scrolls it to the block being edited (HTML: the block, PDF: the page; Markdown/LaTeX too). Preview modes: **Live (HTML)**, **Word (PDF)** (render as Word does) (same
exporter, converted by Gotenberg/LibreOffice; slower, but real pagination) or off; zoom and optional page-break guides.
API: `GET /api/styles/css?import=<id>|base_doc=<id>`, `POST /api/render/pdf`. Scripted check on any DOCX:
`E2E_FIDELITY_DOCX=file.docx E2E_FIDELITY_PAGES=<n> python e2e/fidelity.py` (needs a user from `scripts/e2e-users.sh`).

## Editing

The editor has a ribbon (Home / Insert / Layout / References / Review). On an imported DOCX, click a page header or footer to edit it; use References → Mark as… for sections and generated TOC/figure/table lists; use *Select blocks* or any selection (any size, across pages) for *Create snippet from selection*. Details: docs/DECISIONS.md, round 5B.

Tables (round 8): while the cursor is in a table a contextual **Table** tab, a floating toolbar and a right-click / Shift+F10 menu offer rows/columns, merge/split, borders, shading, widths, autofit, header rows, sort and more; column/row borders and the table corner can be dragged (Shift: only this column, Alt: no snapping, units cm/in/pt). *Make dynamic table* on a table with content turns the header into variables and the body rows into default values. See docs/UI.md and docs/DECISIONS.md, round 8.

Round 9: admins can **edit base documents** directly (Base documents > Details > Edit, or from the document builder): the same editor as imports, *Commit…* writes a new version (author = you; documents keep their pinned version, the builder offers the bump). *Insert > Revision table* drops in a dynamic table over the built-in `revision_history` (date, revision, author, changed sections, commit). Embedded Word objects (OLE) survive import/export; only linked/macro/executable ones are dropped, with a warning. See docs/DECISIONS.md, round 9.

Round 13: revision tables accept the old and new column keys (`row.revision`/`row.version`, `edited_by`/`author`, `description`/`message`/`commit`, ...), blank rows are dropped and an empty history prints one "No revisions yet" row. Document history has **Download DOCX/PDF** per commit (the document as committed: its pins, variables, `doc.version` = tag or short sha); renders are cached under `/files/{org}/cache/` until a tag changes the revision table (`GET /api/documents/{id}/versions/{sha}/export`). See docs/DECISIONS.md, round 13.

Round 19: text boxes and other opaque blocks get *Edit text* / *Convert to text* on hover; harmless wrappers (smart tags, plain content controls, tracked insertions, comment markers, REF fields, internal links) import as editable text; inserting a table or figure offers a caption (Insert dialog, default on); caption numbers read as plain numbers; editor view prefs are stored under versioned keys. See docs/DECISIONS.md "Round 19".

Editing modes (round 20): the view bar has a **Word | Markdown | LaTeX** switch (LaTeX: round 21, below) (snippet editor, import editor, base-document edit; the header/footer editor and Structure sidebar are Word-only). Every open starts in Word; the mode is never remembered. Markdown mode shows the same content as text in a CodeMirror editor (word-only formatting as pandoc attributes such as `::: {custom-style="Quote"}`, `{{ name }}` variables, `$x^2$` equations, `::: {opaque="id"}` placeholders for things text cannot express). Switching back parses the text; blocks you did not touch are restored verbatim from the original JSON (so Word to Markdown to Word without an edit changes nothing, DOCX bytes included), only edited blocks are rebuilt. Parse errors keep you in Markdown with a clickable line list; warnings (dropped horizontal rule...) offer *Switch anyway (lossy)*. While in Markdown the autosave parses after a pause and saves the JSON draft; text that does not parse is kept in `localStorage` per editor and never saved as a draft. Output stays DOCX and PDF. *Insert > Equation* (Ctrl+Alt+E) takes LaTeX math (KaTeX in the editor) and exports a native Word equation (OMML). The conversion needs **pandoc** (pinned 3.14 static binary in the api image, sha256-checked, run with `--sandbox`, 20 s timeout, 2 MB text limit, 4 concurrent processes, 240 conversions/min/user; raw HTML/TeX and includes are disabled). Details: `docs/DECISIONS.md` round 20.

**LaTeX mode (round 21).** Same switch and rules as Markdown (default Word, never remembered, lock kept, autosave only after a clean parse, `Switch anyway (lossy)` for warnings; Markdown <-> LaTeX goes through Word). The source editor is CodeMirror with LaTeX highlighting, bracket matching and a static completion list (`\sec` -> `\section{}`, `\begin{it` -> itemize...). LaTeX is **only an authoring syntax**: no TeX engine runs, output stays DOCX and PDF, `\usepackage` lines are accepted and ignored (warning), nothing can read a file or the network (`\input`, `\include`, `\openin`, `\write18`, `\verbatiminput`, `\includegraphics{/path}` do nothing; images only as `\includegraphics[width=300px]{docgen-image:<imageId>}`). Written text is body-only LaTeX; pasted text may be a full document (`\documentclass` ... `\begin{document}`) or bare body text. Word-only formatting travels in `% docgen:{...}` comment lines (valid LaTeX comments) in front of the block; blocks you did not touch are restored verbatim.

| LaTeX | Becomes in the document |
|---|---|
| `\chapter`, `\section` ... `\subparagraph` (starred too) | Heading 1-4 (with `\chapter` present: chapter = 1, section = 2...; deeper than 4 = 4; numbering is the style's, a star changes nothing) |
| paragraphs, `\par`, `\\`, `\newline` | paragraphs, line breaks |
| `\textbf \textit \emph \underline \texttt \textsc \sout \textsuperscript \textsubscript \textcolor` | bold, italic, underline, Courier New, small caps, strike, super/subscript, colour (size commands and fonts are not mapped) |
| `itemize`, `enumerate` (nested; `\setcounter{enumi}{n}` / `[a.]` start and style), `description` | lists; a description list becomes paragraphs with the term in bold (warning) |
| `quote`, `quotation`, `center`, `flushleft`, `flushright`, `verbatim`, `\verb`, `\hrule` | Quote paragraph, paragraph alignment, Code paragraph / Courier text, horizontal rule |
| `tabular`, `tabular*`, `\hline`, `\multicolumn`, `\multirow` | table (a row followed by `\hline` at the top is the header row; column/row spans) |
| `table` / `figure` floats with `\caption` | the usual `Table n:` / `Figure n:` caption paragraph (SEQ field, lists of tables read it) + table / image |
| `\footnote`, `\endnote`, `\ref{SEQ:Table}`, `\pageref{PAGE}` | footnote / endnote, fields (a plain `\ref{label}` has no Word bookmark: stays text `[label]` with a warning, unless the label is a bookmark of the document) |
| `\cite{k}`, `thebibliography` / `\bibitem` | `[n]` in the text, one `Bibliography` paragraph per item `[n] text` |
| `\href`, `\url` | links |
| `\newpage \clearpage \pagebreak`, `\tableofcontents \listoffigures \listoftables`, `\maketitle` | page break, TOC / list of figures / list of tables, Title + Subtitle (author, date) paragraphs |
| `$..$ \(..\) \[..\] $$..$$`, `equation align gather multline eqnarray split cases` and matrices | the equation node (LaTeX kept verbatim, exported as native Word OMML; what OMML cannot express stays `$source$` with an export warning) |
| `% comments`, `\newcommand`, `\def` | comments and definitions are not kept (macros are expanded while reading, warning) |
| anything else (`\foo`, unknown environments, `\appendix`) | warning with the line number; an unknown environment keeps its text, an unknown command is dropped |

Limits as for Markdown (2 MB, 20 s, 4 concurrent, 240/min/user); at most 100 nested environments; syntax errors (unbalanced braces/environments, more cells than columns) are reported with line and column.

Dates (round 11): every date shown or generated is **DD.MM.YYYY** (timestamps in the UI: DD.MM.YYYY HH:mm, 24 h, browser time zone): UI tables, `doc.date`, date variables, revision history, `date` filter default (`%d.%m.%Y`). Date variables are text inputs accepting DD.MM.YYYY or YYYY-MM-DD, stored as ISO in the document. Frontend formatting lives in `frontend/src/dates.ts` only (a test forbids `toLocale*String`/`Intl.DateTimeFormat` elsewhere); backend in `backend/app/dates.py`. Explicit filter formats (`date:YYYY-MM-DD`, `date:%d/%m/%y`) are honoured. See docs/DECISIONS.md, round 11.

## Architecture

```
 browser ──HTTP──▶ web (nginx :80, published as WEB_PORT)
                    │  static React SPA (TipTap editor)      /api/* proxied
                    ▼
                   api (FastAPI, uvicorn :8000)  ──SMTP──▶ mail (Mailpit, dev)
                    │   │   │
      SQL (docgen_app, RLS) │   └──HTTP: DOCX ─▶ pdf (Gotenberg + LibreOffice) ─▶ PDF
                    ▼   │
                   db (PostgreSQL 16)          git CLI / files
                    ▲                           ▼
                 volume pgdata        volume repos  /repos/{org_id}/{folder_id}.git, /repos/{org_id}/_base.git
                                      volume files  /files/{org_id}/{images,imports,exports,cache}/...
```

* **Backend** `backend/app`: routers (thin) → `vcs.py`/`docs.py`/`export.py` → `git/` (git CLI plumbing) and
  `scoping.OrgRepo` (every tenant query filtered by the session's org). `docx_export.py` (ProseMirror JSON → OOXML),
  `docx_import.py` (DOCX → editable JSON with opaque OOXML blocks), `base_docs.py` (upload validation), `seed.py`.
* **Frontend** `frontend/src`: React + react-router + TanStack Query. `Shell.tsx` (top bar, folder tree, right panel), `ui.tsx` + `app.css` (design system: modals instead of `prompt`/`confirm`, toasts, menus), `pages/` (one file per screen, routes in `App.tsx`), `editor/` (TipTap, live preview, Word page), `builder/` (blocks, variables). IA and screens: `docs/UI.md`.
* Visual walk-through of every screen (needs a seeded stack, e.g. a separate compose project with `SEED_DEMO=1`): `scripts/ui-walk.py`; screenshots land in the gitignored `e2e-artifacts/ui/`.
* Exports are background jobs (rows in `exports`, 2-thread pool in the api process); the UI polls.

## How versioning works

* Every **folder** is a bare git repo. A snippet is `snippets/<slug>/{content.json,meta.yaml,assets/*}`; a document is
  `documents/<slug>.yaml`. The database only indexes them (title, slug, head sha); git is the source of truth.
* Editing writes a per-user **draft** in the DB (autosaved, not a version). **Commit** (folder screen) picks the
  changed snippets/documents and makes one git commit authored by the user. If someone else committed the same
  snippet since your draft started you get a 3-way merge view and resolve before committing.
* **History/diff/restore**: per snippet, document and folder (`git log`, side-by-side and text diff); restoring an old
  version creates a draft. **Tags** (e.g. `v1.2`) name commits.
* **Pins**: a document references a snippet as `{folder_id, snippet_id, version: commit | tag | latest}`; default is a
  fixed commit. "Update available" appears when the snippet has newer commits; bump shows a diff first.
  Exports freeze tags/`latest` to commit shas and store the whole spec (who, when, pins, variables) so they can be
  reproduced. Base documents are versioned in `/repos/{org}/_base.git` and pinned by sha as well.
* Deleting a snippet is a final commit; its history stays in git but it leaves the library. It is refused while a
  document references it.

## Backup and restore

State lives in three volumes (names assume project `docgen`): `docgen_pgdata`, `docgen_repos`, `docgen_files`. Back up
all three **together**, with the api stopped (a commit touches git and the DB).

```sh
mkdir -p backup
docker compose stop api web
docker compose stop db          # consistent copy of pgdata
for v in pgdata repos files; do
  docker run --rm -v docgen_$v:/data:ro -v "$PWD/backup:/backup" alpine tar czf /backup/$v.tgz -C /data .
done
docker compose up -d            # everything back
# optional logical dump, any time:  docker compose exec -T db pg_dump -U docgen -Fc docgen > backup/db.dump
```

Restore into a fresh machine/project (after `git clone` + `.env` with the **same** `POSTGRES_PASSWORD`/`APP_DB_PASSWORD`
and `SECRET_KEY`):

```sh
docker compose down             # add -v only if you really want to drop existing volumes
for v in pgdata repos files; do
  docker volume create docgen_$v
  docker run --rm -v docgen_$v:/data -v "$PWD/backup:/backup:ro" alpine tar xzf /backup/$v.tgz -C /data
done
docker compose up -d --build --wait
```

`scripts/clean-start.sh` performs exactly this round trip in a throwaway project (`docgen-clean`) and checks the data.
Orphaned uploads can be pruned with `POST /api/maintenance/cleanup` (admin; `?dry_run=true` first).

## Tests

```sh
make test               # pytest (in the api image) + Vitest (frontend image) + Playwright e2e (e2e image), all in Docker
make test-backend       # 120 backend tests against a separate docgen_test database
make test-frontend      # Vitest unit tests
make test-e2e-docker    # starts the stack, runs e2e/{smoke,docbuilder,import,invite,fidelity,basedoc}.py in the `e2e` compose service
make test-e2e           # same scripts from the host (.venv with playwright, PLAYWRIGHT_BROWSERS_PATH=$PWD/.playwright,
                        #   E2E_BASE_URL=http://localhost:$WEB_PORT, MAIL_URL=http://localhost:$MAIL_UI_PORT)
scripts/clean-start.sh  # fresh clone + separate compose project + backup/restore round trip
```

The e2e scripts log in as throwaway users created through the CLI (`scripts/e2e-users.sh`) and leave their orgs in the dev database. The Playwright image tag in
`e2e/Dockerfile` must match the pinned `playwright` version.

## Security model

* **Isolation walls** (spec §2): (1) every tenant table has `org_id NOT NULL` and is only reached through
  `OrgRepo`, which filters by the **session's** active org — no endpoint takes `org_id` from the client except
  `POST /api/orgs/switch`, which is checked against your memberships; (2) **Postgres RLS** on every tenant table
  (`SET app.org_id` per transaction) enforced on the unprivileged `docgen_app` role (owners/superusers bypass RLS, so
  the API never connects as one); (3) git repos and files live under `/{org_id}/…`, paths are built server-side from
  UUIDs, never from user input, and checked to stay under their root; (4) another org's object answers **404**, not
  403. Tests: `tests/test_isolation.py` runs two orgs against every route (a test fails if a route is not listed) and
  checks RLS directly as the runtime role.
* **Auth**: argon2id passwords, opaque session token in an HttpOnly SameSite=Lax cookie (hash stored), signed
  double-submit CSRF token on every unsafe request, **login throttling** (5 failures / 10 min per ip+email, 20 per
  email → 429).
* **Roles**: viewer reads/exports; editor edits/commits/builds; admin members, invites, base documents, styles, folder
  delete, file cleanup; owner deletes the org.
* **Uploads**: DOCX validated as a real zip (structure, size, zip-bomb ratio, macros) and parsed with defusedxml
  (no DTD/XXE); images verified with Pillow (no SVG); opaque OOXML re-validated before write-back. Jinja runs
  sandboxed and values are written as XML text nodes.

## Known limits

* Markdown/LaTeX modes (rounds 20, 21): an edited table keeps the attributes of the most similar original table (new rows/columns clone a neighbour; a table nothing like an original gets defaults), untouched tables are kept. In LaTeX, font size/font/highlight/character styles and the cached number of a SEQ field survive only in untouched blocks; starred sections, `\color`, `\label` of new text, `\appendix` and unknown commands are not mapped (warnings). Tables with nested tables or opaque cells appear as one opaque placeholder (restored verbatim; the nested tables are editable in Word mode, round 24). The pandoc layer adds about 170 MB to the api image.
* Not opened in real Microsoft Word by the developers: DOCX output is checked with python-docx, structural tests and
  LibreOffice (PDF conversion). Word will ask to update fields (TOC, page numbers) on open.
* Sign-up is off by default; when enabled it reveals whether an email is registered (409); login is throttled but sign-up is not. The throttle is
  in-process memory (single api worker; resets on restart). CSRF tokens are signed but not tied to the session.
* Export jobs run in the api process; a restart strands queued jobs (reported as error after 10 min). PDFs are not
  byte-reproducible (LibreOffice embeds timestamps); DOCX is.
* No 3-way merge for documents (a moved head means discard and redo); commits made outside the app have no snippet
  history. A crash between git commit and DB commit can leave a stale head sha (no repair job).
* Import keeps direct run/paragraph/cell/table formatting, styles, theme colours/fonts, page setup and header/footer and
  draws opaque blocks (text boxes, fields, TOC, content controls) from their OOXML, but the HTML view is an
  approximation of Word, not Word: see "Fidelity limits" in `docs/DECISIONS.md` (floating objects, first-page headers,
  real pagination, bookmarks, tab stops, list restarts...). "Render as Word does" shows the LibreOffice PDF of the same
  export. Round 24: tables inside table cells (up to 8 levels), form fields, section breaks, single text boxes and block content controls are editable; still opaque: text boxes that share a paragraph with text, embedded documents, equations, SmartArt/charts, complex fields other than PAGE/NUMPAGES/SEQ/REF/FORMTEXT (see `docs/DECISIONS.md`, round 24). Edited tables are re-generated from the editor model, which is not pixel-exact (see "Known limit found" there).
* Org deletion removes its repos/files; folder deletion removes its history. Uploaded images are only removed by the
  admin cleanup endpoint (24 h grace; images that ever entered a commit are kept).
* Base documents can be deleted (admin) only while no document or draft uses them; the file stays in `_base.git` history. No password reset or email verification; Mailpit is dev only.

Snippets from imports (round 12): "Create snippet from selection" and "Split by headings" ask when a snippet with the same title (or slug) exists in the folder: update it (writes your draft on top of HEAD, commit it for the new version), create a separate one, rename or skip. "Update snippets from this import…" (import page) and "Update from import…" (snippet page) refresh existing snippets from the open or a newer import: headings are matched by title, remembered source or fuzzy title, changed sections are applied to drafts. API: `docs/DECISIONS.md`, Round 12.

Round 14: commits can be renamed (**Rename** on every history/version row; Enter saves, Esc cancels, *Reset to original*). It is a display-name override stored in `commit_names`: git messages and shas are never rewritten (`PATCH /api/folders/{id}/commits/{sha}`, `PATCH /api/base-documents/{id}/versions/{sha}`, `{name}`, empty/DELETE = reset). Histories, revision tables and version downloads show the name; renaming invalidates cached downloads of affected versions. See docs/DECISIONS.md, round 14.

