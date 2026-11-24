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

### Invite-only: first user

Public sign-up is **closed** (`ALLOW_SIGNUP=0`: `POST /api/auth/signup` answers 404, the login form hides the sign-up
button). New people join only by accepting an invite (a new user is created there). Create the first user + org on an
empty install with the CLI; the password comes from stdin (piped, or prompted on a terminal), never from argv:

```sh
printf '%s\n' 'a-long-password' | docker compose exec -T api python -m app.manage create-user \
    --email you@example.com --name "You" --org "My Org" --password-stdin
docker compose exec api python -m app.manage create-user --email you@example.com --name You --org "My Org"  # prompts
```

Set `ALLOW_SIGNUP=1` only on a throwaway dev instance.

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
| `SMTP_HOST` / `SMTP_PORT` | `mail` / `1025` | SMTP server (Mailpit by default) |
| `SMTP_USER` / `SMTP_PASSWORD` | empty | SMTP credentials |
| `SMTP_STARTTLS` | `false` | Use STARTTLS |
| `SMTP_FROM` | `DocGen <noreply@docgen.local>` | Sender |

Fixed inside the api container (not in `.env`): `DATABASE_URL`, `MIGRATE_DATABASE_URL`, `PDF_URL=http://pdf:3000`,
`REPOS_ROOT=/repos`, `FILES_ROOT=/files`. Optional app tuning: `PDF_TIMEOUT_S` (120), `PDF_MAX_BYTES` (100 MiB).
For production remove the `mail` service and point `SMTP_*` at a real server.

## Looks like Word

The editor and every preview sit on a "page" with the page size, margins, header/footer, fonts, colours and paragraph
styles of the DOCX they belong to: an imported document uses its own package, a snippet uses the base document chosen
under **Look** (else the organization styles). Preview modes: **Live (HTML)**, **Render as Word does (PDF)** (same
exporter, converted by Gotenberg/LibreOffice; slower, but real pagination) or off; zoom and optional page-break guides.
API: `GET /api/styles/css?import=<id>|base_doc=<id>`, `POST /api/render/pdf`. Scripted check on any DOCX:
`E2E_FIDELITY_DOCX=file.docx E2E_FIDELITY_PAGES=<n> python e2e/fidelity.py` (needs a user from `scripts/e2e-users.sh`).

## Editing

The editor has a ribbon (Home / Insert / Layout / References / Review). On an imported DOCX, click a page header or footer to edit it; use References → Mark as… for sections and generated TOC/figure/table lists; use *Select blocks* or any selection (any size, across pages) for *Create snippet from selection*. Details: docs/DECISIONS.md, round 5B.

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
                                      volume files  /files/{org_id}/{images,imports,exports}/{uuid}.*
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
  export. No nested tables in snippets.
* Org deletion removes its repos/files; folder deletion removes its history. Uploaded images are only removed by the
  admin cleanup endpoint (24 h grace; images that ever entered a commit are kept).
* Base documents cannot be deleted; no password reset or email verification; Mailpit is dev only.
