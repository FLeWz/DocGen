# DocGen plan

Spec: `docs/SPEC.md`. Choices not fixed by the spec: `docs/DECISIONS.md`.

## Repo layout

```
docker-compose.yml  Makefile  .env.example (.env is gitignored)
backend/
  app/
    main.py        FastAPI app, CSRF middleware, router registration
    config.py      env vars          db.py       engine, RLS context (set_ctx / after_begin)
    models.py      ORM + OrgScoped mixin + Role    scoping.py  OrgRepo (org-filtered access)
    rls.py         tenant_rls() SQL helper         deps.py     Ctx, user_ctx / org_ctx / require(role)
    security.py    argon2, tokens, CSRF            mail.py     SMTP
    schemas.py     Pydantic                        routers/    auth, orgs, members, invites, ...
  alembic/versions/  migrations (run on api start via entrypoint.sh)
  tests/             pytest (run in Docker)
frontend/            React+TS+Vite, TanStack Query; nginx serves dist and proxies /api -> api:8000
docs/
```
Added later: `app/git/` (git CLI access), `vcs.py`/`docs.py` (folder/snippet/document logic), `docx_export.py`,
`docx_import.py`, `ooxml.py`, `base_docs.py`, `export.py` (job + renderers), `throttle.py` (login throttling),
`seed.py` (demo data), `routers/maintenance.py` (file cleanup). Also `e2e/` (Playwright scripts + Dockerfile),
`scripts/clean-start.sh`, `README.md`.

Storage volumes: `pgdata`; `repos` -> `/repos/{org_id}/{folder_id}.git`, `/repos/{org_id}/_base.git`; `files` -> `/files/{org_id}/...`. Paths are built server-side from UUIDs only.

## Data model (`*` = tenant table: `org_id NOT NULL` + RLS)

| Table | Columns |
|---|---|
| organizations | id, name |
| users | id, email (unique, lowercase), name, password_hash |
| memberships* | id, org_id, user_id, role (owner/admin/editor/viewer); unique(org_id,user_id) |
| invites* | id, org_id, email, role, token_hash, invited_by, expires_at, accepted_at, revoked_at |
| sessions | id, token_hash, user_id, active_org_id, expires_at |
| folders* (M2) | id, org_id, parent_id, name, repo path |
| snippets* (M2) | id, org_id, folder_id, slug, title, head_sha (index of git content) |
| drafts* (M2) | id, org_id, snippet_id, user_id, content json, base_sha |
| documents* (M4) | id, org_id, folder_id, slug, title, base_doc ref, blocks, variables, links (also committed as `documents/<slug>.yaml`) |
| base_documents* (M4) | id, org_id, name, file path in `_base.git` |
| styles* (M3) | org style set |
| exports* (M4) | id, org_id, document_id, user_id, pins json, variables json, format, status, file path |
| jobs (M4) | merged into `exports` (status column); executed by an in-process thread pool |
| imports* (M7) | id, org_id, title, filename, content (editable TipTap JSON), warnings; original at `/files/{org}/imports/{id}.docx` |
| base_doc_versions*, document_drafts*, document_refs*, images* | see `models.py` |

## API (`/api`, OpenAPI at `/api/docs`)

Final state (every route is listed in `COVERED` in `tests/test_isolation.py`): `auth/{signup,login,logout,me,csrf}` ·
`orgs` (GET, POST), `orgs/current` (GET, DELETE), `orgs/switch` · `members` (GET; PATCH/DELETE `/{user_id}`) ·
`invites` (GET, POST, `/{id}/resend`, DELETE `/{id}`, `/lookup`, `/accept`) · `folders` (CRUD) and
`folders/{id}/{snippets,changes,commit,history,tags}` · `snippets/{id}` (GET, DELETE) with `/draft` (GET, PUT, DELETE),
`/resolve`, `/restore`, `/history`, `/diff`, `/used-by` · `styles` (CRUD, `?base_doc=`) · `images` · `base-documents`
(+ `/versions`, `/download`) · `documents` (+ `/draft`, `/history`, `/updates`, `/blocks/{id}/bump`,
`/revision-history`, `/export?format=docx|pdf`, `/exports`) · `exports/{id}` (+ `/download`) · `library/snippets` ·
`import/docx`, `imports` (GET; `/{id}` GET/PUT/DELETE, `/export`, `/split`, `/selection-snippet`, `/base-document`) ·
`maintenance/cleanup` (admin).

Roles: viewer reads/exports; editor edits/commits/builds/deletes snippets and imports; admin members/invites/base docs/styles/folder delete/cleanup; owner deletes the org. Other org's object -> 404.

## Milestones (all done)

1. Skeleton + auth + orgs + invites + isolation tests
2. Folders + git-backed snippets + draft/commit/history/diff/tags/conflicts
3. Snippet editor (TipTap) + preview + styles
4. Base documents + document builder + DOCX export + export records/jobs
5. Dynamic variables/tables + revision history + linked docs
6. PDF export via Gotenberg
7. DOCX import, snippet from selection, round-trip tests
8. Polish: seed script, login throttling, snippet/import delete + file cleanup, style-set UI, Docker e2e in `make test`,
   README, acceptance run (`scripts/clean-start.sh`)

Every milestone: new tenant tables go through `OrgScoped` + `tenant_rls()` + `OrgRepo`, and new routes must be added to `COVERED` in `tests/test_isolation.py` with an isolation test.
