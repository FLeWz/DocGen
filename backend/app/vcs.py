"""Shared folder/snippet/draft logic for the git-backed routers."""
import json
import re
from uuid import UUID

from fastapi import HTTPException

from .deps import Ctx
from .git import content as C, repo as git
from .models import Draft, Folder, Snippet

MAX_DOC = 5_000_000


def get_folder(ctx: Ctx, id: UUID) -> Folder:
    f = ctx.repo(Folder).get(id)
    if not f:
        raise HTTPException(404, "folder not found")
    return f


def get_snippet(ctx: Ctx, id: UUID) -> Snippet:
    s = ctx.repo(Snippet).get(id)
    if not s:
        raise HTTPException(404, "snippet not found")
    return s


def repo_of(ctx: Ctx, folder_id: UUID):
    return git.repo_path(ctx.org_id, folder_id)


def rev_version(ctx: Ctx, s: Snippet, rev: str) -> dict:
    repo = repo_of(ctx, s.folder_id)
    try:
        v = C.version_at(repo, git.resolve(repo, rev), s.id)
    except git.NotFound:
        v = None
    if not v:
        raise HTTPException(404, "version not found")
    return v


def head_version(ctx: Ctx, s: Snippet) -> dict | None:
    return C.version_at(repo_of(ctx, s.folder_id), s.head_sha, s.id) if s.head_sha else None


def dver(d: Draft) -> dict:
    return dict(slug=d.slug, title=d.title, styles=d.styles, variables=d.variables, content=d.content)


def current_draft(ctx: Ctx, s: Snippet) -> Draft | None:
    """The user's draft. A draft that was never edited past its base silently follows HEAD."""
    d = ctx.repo(Draft).first(Draft.snippet_id == s.id, Draft.user_id == ctx.user.id)
    if d and d.base_sha != s.head_sha and not dirty(ctx, s, d):
        h = head_version(ctx, s)
        d.title, d.slug, d.content, d.styles, d.variables = (h[k] for k in ("title", "slug", "content", "styles", "variables"))
        d.base_sha = s.head_sha
    return d


def dirty(ctx: Ctx, s: Snippet, d: Draft) -> bool:
    if not d.base_sha:
        return True
    return not C.same(s.id, C.version_at(repo_of(ctx, s.folder_id), d.base_sha, s.id), dver(d))


def conflict_info(ctx: Ctx, s: Snippet, d: Draft) -> dict:
    repo = repo_of(ctx, s.folder_id)
    return dict(snippet_id=str(s.id), slug=s.slug, base_sha=d.base_sha, head_sha=s.head_sha,
                base=d.base_sha and C.version_at(repo, d.base_sha, s.id),
                theirs=head_version(ctx, s), mine=dver(d))


def save_draft(ctx: Ctx, s: Snippet, body, *, rebase: bool = False) -> Draft:
    """Upsert the user's draft; omitted optional fields keep their current value."""
    if len(json.dumps(body.content)) > MAX_DOC:
        raise HTTPException(413, "document too large")
    d = ctx.repo(Draft).first(Draft.snippet_id == s.id, Draft.user_id == ctx.user.id)
    h = None if d else head_version(ctx, s)
    cur = dver(d) if d else dict(title=s.title, slug=s.slug, styles=(h or {}).get("styles", []),
                                 variables=(h or {}).get("variables", []))
    vals = dict(content=body.content, **{k: getattr(body, k) if getattr(body, k) is not None else cur[k]
                                         for k in ("title", "slug", "styles", "variables")})
    if not d:
        d = ctx.repo(Draft).add(snippet_id=s.id, user_id=ctx.user.id, base_sha=s.head_sha, **vals)
    else:
        for k, v in vals.items():
            setattr(d, k, v)
        if rebase:
            d.base_sha = s.head_sha
    return d


def draft_out(ctx: Ctx, s: Snippet, d: Draft | None) -> dict:
    if d:
        return dict(**dver(d), snippet_id=s.id, folder_id=s.folder_id, exists=True, base_sha=d.base_sha,
                    head_sha=s.head_sha, conflict=d.base_sha != s.head_sha, dirty=dirty(ctx, s, d))
    h = head_version(ctx, s) or dict(title=s.title, slug=s.slug, styles=[], variables=[], content=C.EMPTY_DOC)
    return dict(**{k: h[k] for k in ("title", "slug", "styles", "variables", "content")}, snippet_id=s.id,
                folder_id=s.folder_id, exists=False, base_sha=s.head_sha, head_sha=s.head_sha,
                conflict=False, dirty=False)


TRAILER = re.compile(r"^Snippet-Id: ([0-9a-f-]{36})$", re.M)


def commit_out(row: dict, tagmap: dict) -> dict:
    body = row.pop("body")
    ids = TRAILER.findall(body)
    return dict(**row, message=TRAILER.sub("", body).strip(), snippet_ids=ids, tags=tagmap.get(row["sha"], []))


def tag_map(repo) -> dict:
    m: dict = {}
    for t in git.tags(repo):
        m.setdefault(t["sha"], []).append(t["name"])
    return m
