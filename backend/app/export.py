"""Export pipeline: resolve pins into a self-contained spec (stored with the export record), then render.
PDF = the rendered DOCX converted by the Gotenberg container (LibreOffice)."""
import hashlib
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi import HTTPException

import httpx

from . import base_docs, config, docs, docx_export, files, vcs
from .db import SessionLocal, set_ctx
from .deps import Ctx
from .git import repo as git
from .git import content as C
from .models import BaseDocVersion, Document, Export, Folder, Organization, Role, Snippet, User
from .scoping import OrgRepo

_pool = ThreadPoolExecutor(2, thread_name_prefix="export")
STALE = timedelta(minutes=10)


BUILTIN_ROOTS = {"doc", "org", "author", "revision_history", "linked_documents"}


def used_variables(node, out: dict) -> None:
    """name -> {"list": bool, "required": bool}; chips with a default don't require a value, row.* is bound by its table."""
    if not isinstance(node, dict):
        return
    a = node.get("attrs") or {}
    t = node.get("type")
    if t == "table" and a.get("dynamicList"):
        out.setdefault(a["dynamicList"], dict(list=True, required=True))["list"] = True
    if t == "variable":
        n = a.get("name") or ""
        if not n.startswith("row.") and n.split(".")[0] not in BUILTIN_ROOTS:
            o = out.setdefault(n, dict(list=False, required=False))
            o["required"] |= a.get("default") in (None, "")
    for c in node.get("content") or []:
        used_variables(c, out)


def missing_variables(variables: dict, used: dict) -> list[dict]:
    """Declared variables and used chips lacking both a value and a default (declared => required)."""
    vals, out = variables.get("values", {}), {}
    decls = {d["name"]: d for d in variables.get("declarations", [])}
    for n in sorted({*decls, *(k for k, u in used.items() if u["required"])}):
        d = decls.get(n)
        if vals.get(n) in (None, "") and (not d or d.get("default") in (None, "")):
            out[n] = dict(name=n, type=d["type"] if d else "list" if used.get(n, {}).get("list") else "text")
    return list(out.values())


def revision_rows(ctx: Ctx, d: Document, blocks: list[dict]) -> list[dict]:
    """Auto revision history: commits of the document and of each pinned snippet (up to its pin), oldest first."""
    seen, rows = set(), []

    def add(repo, commits):
        tm = vcs.tag_map(repo)
        for c in commits:
            if c["sha"] not in seen:
                seen.add(c["sha"])
                c = vcs.commit_out(c, tm)
                rows.append((c["date"], c["sha"], dict(version=(c["tags"] or [c["sha"][:7]])[0], date=c["date"][:10],
                                                       author=c["author_name"], message=c["message"].split("\n")[0])))

    add(vcs.repo_of(ctx, d.folder_id), git.log(vcs.repo_of(ctx, d.folder_id), d.id, key="Document-Id"))
    for b in blocks:
        s = b["type"] == "snippet_ref" and ctx.repo(Snippet).get(UUID(b["snippet_id"]))
        sha = s and docs.pin_sha(ctx, b, s)
        if sha:
            repo = vcs.repo_of(ctx, s.folder_id)
            add(repo, git.log(repo, s.id, rev=sha))
    return [r[2] for r in sorted(rows, key=lambda r: r[:2])]


def build_spec(ctx: Ctx, d: Document) -> dict:
    """Everything the renderer needs. Tags and `latest` are frozen to commit shas here."""
    w = docs.working(ctx, d)
    blocks = []
    for b in w["blocks"]:
        b = dict(b)
        if b["type"] == "snippet_ref":
            f, s = ctx.repo(Folder).get(UUID(b["folder_id"])), ctx.repo(Snippet).get(UUID(b["snippet_id"]))
            sha = f and s and docs.pin_sha(ctx, b, s)
            if not sha:
                raise HTTPException(422, f"block {b['id']}: referenced snippet version is not available")
            b["pin"] = dict(folder_id=b["folder_id"], snippet_id=b["snippet_id"], sha=sha)
        blocks.append(b)
    br = w["base_document"]
    org = ctx.db.get(Organization, ctx.org_id)
    bv = br and ctx.repo(BaseDocVersion).first(BaseDocVersion.base_document_id == UUID(br["id"]), BaseDocVersion.sha == br["version"])
    return dict(base_variables=bv.variables if bv else [],  # frozen: defaults edited later must not change a stored spec

        document=dict(id=str(d.id), slug=w["slug"], head_sha=d.head_sha, draft=docs.draft_of(ctx, d) is not None),
        title=w["title"], base=br and dict(id=br["id"], sha=br["version"]), blocks=blocks,
        variables=w["variables"], links=w["links"],
        revision_history=w.get("revision_history") if w.get("revision_history") is not None else revision_rows(ctx, d, w["blocks"]),
        built_ins={"doc.title": w["title"], "doc.version": (d.head_sha or "draft")[:7],
                   "doc.date": datetime.now(timezone.utc).date().isoformat(), "org.name": org.name,
                   "author": ctx.user.name})


def pins_of(spec: dict) -> dict:
    return dict(base=spec["base"], document=spec["document"],
                snippets=[dict(block_id=b["id"], **b["pin"], pin=b["version"]) for b in spec["blocks"] if "pin" in b])


def create(ctx: Ctx, d: Document, fmt: str) -> Export:
    spec = build_spec(ctx, d)
    used: dict = {}
    for b in spec["blocks"]:
        v = "pin" in b and C.version_at(git.repo_path(ctx.org_id, b["pin"]["folder_id"]), b["pin"]["sha"], b["pin"]["snippet_id"])
        used_variables(v and v["content"], used)
        if b["type"] == "paragraph":
            used_variables(b["content"], used)
    v = spec["variables"]
    own = {d["name"] for d in v["declarations"]}
    merged = dict(v, declarations=[*(d for d in spec["base_variables"] if d["name"] not in own), *v["declarations"]])
    if miss := missing_variables(merged, used):
        raise HTTPException(422, dict(error="missing_variables", variables=miss,
                                      message="Missing required variables: " + ", ".join(m["name"] for m in miss)))
    return ctx.repo(Export).add(document_id=d.id, user_id=ctx.user.id, format=fmt, status="queued",
                                pins=pins_of(spec), variables=spec["variables"], spec=spec, warnings=[])


def render_docx(org_id, spec: dict) -> tuple[bytes, list]:
    if spec["base"]:
        base = git.show(git.base_repo_path(org_id), spec["base"]["sha"], f"base/{spec['base']['id']}.docx")
        if base is None:
            raise ValueError("pinned base document version not found")
    else:
        base = base_docs.default_docx()
    cache: dict = {}

    def content(pin):
        k = (pin["folder_id"], pin["snippet_id"], pin["sha"])
        if k not in cache:
            v = C.version_at(git.repo_path(org_id, pin["folder_id"]), pin["sha"], pin["snippet_id"])
            cache[k] = v and (v["content"], v["slug"])
        return cache[k]

    def asset(pin, slug, image_id):
        try:
            iid = UUID(str(image_id))
        except ValueError:
            return None
        repo = git.repo_path(org_id, pin["folder_id"])
        return next((b for ext in ("png", "jpg", "gif", "webp")
                     if (b := git.show(repo, pin["sha"], f"snippets/{slug}/assets/{iid}.{ext}"))), None)

    return docx_export.build(spec, base, content, asset)


def render_pdf(org_id, spec: dict) -> tuple[bytes, list]:
    """DOCX -> Gotenberg/LibreOffice. updateIndexes makes LibreOffice refresh the TOC and page-number fields."""
    docx, warnings = render_docx(org_id, spec)
    return docx_to_pdf(docx), warnings


def docx_to_pdf(docx: bytes) -> bytes:
    try:
        with httpx.stream("POST", f"{config.PDF_URL}/forms/libreoffice/convert", timeout=config.PDF_TIMEOUT_S,
                          files={"files": ("document.docx", docx, MIME["docx"])}, data={"updateIndexes": "true"}) as r:
            if r.status_code != 200:
                raise ValueError(f"PDF conversion failed (HTTP {r.status_code}): {r.read()[:200].decode(errors='replace')}")
            out = bytearray()
            for chunk in r.iter_bytes():
                out += chunk
                if len(out) > config.PDF_MAX_BYTES:
                    raise ValueError(f"PDF larger than the {config.PDF_MAX_BYTES // 2**20} MiB limit")
    except httpx.TimeoutException:
        raise ValueError(f"PDF conversion timed out after {config.PDF_TIMEOUT_S:g}s")
    except httpx.HTTPError as e:
        raise ValueError(f"PDF service unavailable ({type(e).__name__})")
    if not out.startswith(b"%PDF"):
        raise ValueError("PDF service returned something that is not a PDF")
    return bytes(out)


RENDERERS = {"docx": render_docx, "pdf": render_pdf}
MIME = {"docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "pdf": "application/pdf"}


def submit(org_id, user_id, export_id) -> None:
    _pool.submit(_run, org_id, user_id, export_id)


def _run(org_id, user_id, export_id) -> None:
    with SessionLocal() as db:
        set_ctx(db, org_id=org_id, user_id=user_id)
        repo = OrgRepo(db, Export, org_id)
        e = repo.get(export_id)
        e.status = "running"
        db.commit()
        try:
            data, warnings = RENDERERS[e.format](org_id, e.spec)
            p = files.export_path(org_id, e.id, e.format)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(data)
            e.sha256, e.warnings, e.status = hashlib.sha256(data).hexdigest(), warnings, "done"
        except Exception as x:  # job boundary: record, never crash the worker
            e.status, e.error = "error", re.sub(r"\s+", " ", str(x))[:500] or type(x).__name__
        e.finished_at = datetime.now(timezone.utc)
        db.commit()


def expire_stale(ctx: Ctx, e: Export) -> None:
    """The queue lives in this process: a restart mid-job leaves a row that would never finish."""
    if e.status in ("queued", "running") and datetime.now(timezone.utc) - e.created_at > STALE:
        e.status, e.error = "error", "export interrupted"
        ctx.db.commit()
