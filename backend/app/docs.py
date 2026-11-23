"""Documents: documents/<slug>.yaml in the folder's repo, per-user drafts, pin validation, updates."""
import json
from uuid import UUID, uuid4

import yaml
from fastapi import HTTPException

from . import vcs
from .deps import Ctx
from .git import content as C, repo as git
from .models import BaseDocument, BaseDocVersion, DocDraft, Document, Folder, Snippet

BODY = ("base_document", "blocks", "variables", "links")
MAX_BODY = 2_000_000


def get_document(ctx: Ctx, id: UUID) -> Document:
    d = ctx.repo(Document).get(id)
    if not d:
        raise HTTPException(404, "document not found")
    return d


def empty_body() -> dict:
    return dict(base_document=None, blocks=[], variables=dict(declarations=[], values={}), links=[])


def path(slug: str) -> str:
    return f"documents/{slug}.yaml"


def dump(doc_id, title: str, body: dict) -> str:
    return yaml.safe_dump(dict(id=str(doc_id), title=title, **{k: body[k] for k in BODY}),
                          sort_keys=False, allow_unicode=True)


def version_at(ctx: Ctx, d: Document, sha: str) -> dict | None:
    repo = vcs.repo_of(ctx, d.folder_id)
    p = git.file_at(repo, sha, d.id, "documents/*.yaml")
    raw = p and git.show(repo, sha, p)
    if raw is None:
        return None
    y = yaml.safe_load(raw)
    return dict(sha=sha, slug=p.split("/")[1].removesuffix(".yaml"), title=y["title"], **{k: y[k] for k in BODY})


def head_version(ctx: Ctx, d: Document) -> dict | None:
    return version_at(ctx, d, d.head_sha) if d.head_sha else None


def _eq(a: dict | None, b: dict | None) -> bool:
    return bool(a and b and a["title"] == b["title"] and a["slug"] == b["slug"] and all(a[k] == b[k] for k in BODY))


def dver(dr: DocDraft) -> dict:
    return dict(title=dr.title, slug=dr.slug, **dr.body)


def draft_of(ctx: Ctx, d: Document) -> DocDraft | None:
    return ctx.repo(DocDraft).first(DocDraft.document_id == d.id, DocDraft.user_id == ctx.user.id)


def dirty(ctx: Ctx, d: Document, dr: DocDraft) -> bool:
    return not dr.base_sha or not _eq(version_at(ctx, d, dr.base_sha), dver(dr))


def working(ctx: Ctx, d: Document) -> dict:
    """What the user is looking at: their draft, else HEAD, else an empty new document."""
    dr = draft_of(ctx, d)
    if dr:
        return dver(dr)
    return head_version(ctx, d) or dict(title=d.title, slug=d.slug, **empty_body())


def doc_out(ctx: Ctx, d: Document, version: str | None = None) -> dict:
    dr = draft_of(ctx, d)
    if version:
        v = version_at(ctx, d, version)
        if not v:
            raise HTTPException(404, "version not found")
        return dict(id=d.id, folder_id=d.folder_id, head_sha=d.head_sha, has_draft=bool(dr), **v)
    w = dver(dr) if dr else head_version(ctx, d) or dict(title=d.title, slug=d.slug, **empty_body())
    return dict(id=d.id, folder_id=d.folder_id, head_sha=d.head_sha, has_draft=bool(dr),
                base_sha=dr.base_sha if dr else d.head_sha, conflict=bool(dr and dr.base_sha != d.head_sha),
                dirty=bool(dr and dirty(ctx, d, dr)), **w)


# ---- validation of references (cross-folder allowed, cross-org is a plain 404) ----

def _check_ref(ctx: Ctx, b: dict) -> None:
    f = ctx.repo(Folder).get(UUID(b["folder_id"]))
    s = ctx.repo(Snippet).get(UUID(b["snippet_id"]))
    if not f or not s or s.folder_id != f.id:
        raise HTTPException(404, "snippet not found")
    if not s.head_sha:
        raise HTTPException(422, "snippet has no committed version yet")
    v = b["version"]
    if v["type"] == "latest":
        return
    repo = vcs.repo_of(ctx, f.id)
    try:
        sha = git.resolve(repo, v["value"])
    except git.NotFound:
        raise HTTPException(404, "version not found")
    if not C.version_at(repo, sha, s.id):
        raise HTTPException(404, "version not found")
    if v["type"] == "commit":
        v["value"] = sha  # normalise abbreviated shas


def base_head(ctx: Ctx, bd: BaseDocument) -> BaseDocVersion:
    return max(ctx.repo(BaseDocVersion).list(BaseDocVersion.base_document_id == bd.id), key=lambda v: v.number)


def normalize(ctx: Ctx, body) -> dict:
    data = body.model_dump(mode="json")
    if len(json.dumps(data)) > MAX_BODY:
        raise HTTPException(413, "document too large")
    seen = set()
    for b in data["blocks"]:
        if not b["id"] or b["id"] in seen:
            b["id"] = str(uuid4())
        seen.add(b["id"])
        if b["type"] == "snippet_ref":
            _check_ref(ctx, b)
    br = data["base_document"]
    if br:
        bd = ctx.repo(BaseDocument).get(UUID(br["id"]))
        if not bd:
            raise HTTPException(404, "base document not found")
        if br["version"]:
            if not ctx.repo(BaseDocVersion).first(BaseDocVersion.base_document_id == bd.id,
                                                  BaseDocVersion.sha == br["version"]):
                raise HTTPException(404, "base document version not found")
        else:
            br["version"] = base_head(ctx, bd).sha
    return {k: data[k] for k in BODY}


def save_draft(ctx: Ctx, d: Document, body) -> DocDraft:
    data = normalize(ctx, body)
    cur = working(ctx, d)
    title, slug = body.title or cur["title"], body.slug or cur["slug"]
    if slug != d.slug and ctx.repo(Document).first(Document.folder_id == d.folder_id, Document.slug == slug,
                                                   Document.id != d.id):
        raise HTTPException(409, "slug already used in this folder")
    dr = draft_of(ctx, d)
    if not dr:
        return ctx.repo(DocDraft).add(document_id=d.id, user_id=ctx.user.id, title=title, slug=slug, body=data,
                                      base_sha=d.head_sha)
    dr.title, dr.slug, dr.body = title, slug, data
    return dr


# ---- update-available ----

def pin_sha(ctx: Ctx, b: dict, s: Snippet) -> str | None:
    v = b["version"]
    if v["type"] == "latest":
        return s.head_sha
    try:
        return git.resolve(vcs.repo_of(ctx, s.folder_id), v["value"])
    except git.NotFound:
        return None


def refs_status(ctx: Ctx, blocks: list[dict]) -> list[dict]:
    out = []
    for b in blocks:
        if b["type"] != "snippet_ref":
            continue
        f, s = ctx.repo(Folder).get(UUID(b["folder_id"])), ctx.repo(Snippet).get(UUID(b["snippet_id"]))
        row = dict(block_id=b["id"], folder_id=b["folder_id"], snippet_id=b["snippet_id"], pin=b["version"],
                   folder_name=f and f.name, title=s and s.title, slug=s and s.slug, missing=not (f and s),
                   resolved_sha=None, latest_sha=s and s.head_sha, newer_commits=0, update_available=False)
        if f and s and s.head_sha:
            row["resolved_sha"] = sha = pin_sha(ctx, b, s)
            if sha and sha != s.head_sha:
                repo = vcs.repo_of(ctx, f.id)
                if not C.same(s.id, C.version_at(repo, sha, s.id), C.version_at(repo, s.head_sha, s.id)):
                    row["newer_commits"] = git.count_newer(repo, s.id, sha, s.head_sha)
                    row["update_available"] = b["version"]["type"] != "latest" and row["newer_commits"] > 0
        out.append(row)
    return out
