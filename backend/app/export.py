"""Export pipeline: resolve pins into a self-contained spec (stored with the export record), then render.
To add PDF (milestone 6): RENDERERS["pdf"] = lambda org_id, spec: (gotenberg(render_docx(org_id, spec)[0]), warnings)."""
import hashlib
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi import HTTPException

from . import base_docs, docs, docx_export, files, vcs
from .db import SessionLocal, set_ctx
from .deps import Ctx
from .git import repo as git
from .git import content as C
from .models import Document, Export, Folder, Organization, Role, Snippet, User
from .scoping import OrgRepo

_pool = ThreadPoolExecutor(2, thread_name_prefix="export")
STALE = timedelta(minutes=10)


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
    return dict(
        document=dict(id=str(d.id), slug=w["slug"], head_sha=d.head_sha, draft=docs.draft_of(ctx, d) is not None),
        title=w["title"], base=br and dict(id=br["id"], sha=br["version"]), blocks=blocks,
        variables=w["variables"], links=w["links"],
        built_ins={"doc.title": w["title"], "doc.version": (d.head_sha or "draft")[:7],
                   "doc.date": datetime.now(timezone.utc).date().isoformat(), "org.name": org.name,
                   "author": ctx.user.name})


def pins_of(spec: dict) -> dict:
    return dict(base=spec["base"], document=spec["document"],
                snippets=[dict(block_id=b["id"], **b["pin"], pin=b["version"]) for b in spec["blocks"] if "pin" in b])


def create(ctx: Ctx, d: Document, fmt: str) -> Export:
    spec = build_spec(ctx, d)
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


RENDERERS = {"docx": render_docx}
MIME = {"docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}


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
