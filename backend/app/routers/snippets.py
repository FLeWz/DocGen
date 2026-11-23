import re
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response

from .. import vcs
from ..deps import Ctx, require
from ..git import content as C, repo as git
from ..models import Draft, Role, Snippet
from ..schemas import DraftIn, RestoreIn, SnippetCreateIn, SnippetOut

router = APIRouter(tags=["snippets"])
V = Depends(require(Role.viewer))
E = Depends(require(Role.editor))


@router.get("/folders/{folder_id}/snippets", response_model=list[SnippetOut])
def list_snippets(folder_id: UUID, q: str | None = Query(default=None, max_length=100), ctx: Ctx = V):
    f = vcs.get_folder(ctx, folder_id)
    where = [Snippet.folder_id == f.id]
    if q:
        where.append(Snippet.title.icontains(q, autoescape=True) | Snippet.slug.icontains(q, autoescape=True))
    rows = ctx.repo(Snippet).list(*where)
    mine = {d.snippet_id for d in ctx.repo(Draft).list(Draft.user_id == ctx.user.id)}
    return [SnippetOut(**{c: getattr(s, c) for c in ("id", "folder_id", "slug", "title", "head_sha")},
                       has_draft=s.id in mine) for s in sorted(rows, key=lambda s: s.title.lower())]


@router.post("/folders/{folder_id}/snippets", status_code=201, response_model=SnippetOut)
def create_snippet(folder_id: UUID, body: SnippetCreateIn, ctx: Ctx = E):
    f = vcs.get_folder(ctx, folder_id)
    taken = {s.slug for s in ctx.repo(Snippet).list(Snippet.folder_id == f.id)}
    if body.slug:
        if body.slug in taken:
            raise HTTPException(409, "slug already used in this folder")
        slug = body.slug
    else:
        base = re.sub(r"[^a-z0-9]+", "-", body.title.lower()).strip("-")[:56] or "snippet"
        slug, n = base, 2
        while slug in taken:
            slug, n = f"{base}-{n}", n + 1
    s = ctx.repo(Snippet).add(folder_id=f.id, slug=slug, title=body.title)
    ctx.repo(Draft).add(snippet_id=s.id, user_id=ctx.user.id, title=s.title, slug=slug,
                        content=C.EMPTY_DOC, styles=[], variables=[], base_sha=None)
    ctx.db.commit()
    return SnippetOut(id=s.id, folder_id=f.id, slug=slug, title=s.title, head_sha=None, has_draft=True)


@router.get("/snippets/{snippet_id}")
def get_snippet(snippet_id: UUID, version: str | None = Query(default=None, max_length=64), ctx: Ctx = V):
    s = vcs.get_snippet(ctx, snippet_id)
    v = vcs.rev_version(ctx, s, version) if version else vcs.head_version(ctx, s)
    v = v or dict(sha=None, slug=s.slug, title=s.title, styles=[], variables=[], content=C.EMPTY_DOC)
    return dict(id=s.id, folder_id=s.folder_id, **v)


@router.get("/snippets/{snippet_id}/draft")
def get_draft(snippet_id: UUID, ctx: Ctx = V):
    s = vcs.get_snippet(ctx, snippet_id)
    d = vcs.current_draft(ctx, s)
    out = vcs.draft_out(ctx, s, d)
    ctx.db.commit()  # persists a silent fast-forward
    return out


@router.put("/snippets/{snippet_id}/draft")
def put_draft(snippet_id: UUID, body: DraftIn, ctx: Ctx = E):
    s = vcs.get_snippet(ctx, snippet_id)
    vcs.current_draft(ctx, s)
    d = vcs.save_draft(ctx, s, body)
    out = vcs.draft_out(ctx, s, d)
    ctx.db.commit()
    return out


@router.delete("/snippets/{snippet_id}/draft", status_code=204)
def discard_draft(snippet_id: UUID, ctx: Ctx = E):
    s = vcs.get_snippet(ctx, snippet_id)
    d = ctx.repo(Draft).first(Draft.snippet_id == s.id, Draft.user_id == ctx.user.id)
    if d:
        ctx.repo(Draft).delete(d)
        ctx.db.commit()
    return Response(status_code=204)


@router.post("/snippets/{snippet_id}/resolve")
def resolve_conflict(snippet_id: UUID, body: DraftIn, ctx: Ctx = E):
    """Accept the user's merged content and re-base the draft onto the current HEAD."""
    s = vcs.get_snippet(ctx, snippet_id)
    d = vcs.save_draft(ctx, s, body, rebase=True)
    out = vcs.draft_out(ctx, s, d)
    ctx.db.commit()
    return out


@router.post("/snippets/{snippet_id}/restore")
def restore(snippet_id: UUID, body: RestoreIn, ctx: Ctx = E):
    s = vcs.get_snippet(ctx, snippet_id)
    v = vcs.rev_version(ctx, s, body.version)
    d = vcs.save_draft(ctx, s, DraftIn(content=v["content"], title=v["title"], styles=v["styles"],
                                       variables=v["variables"]), rebase=True)
    out = vcs.draft_out(ctx, s, d)
    ctx.db.commit()
    return out


@router.get("/snippets/{snippet_id}/history")
def history(snippet_id: UUID, ctx: Ctx = V):
    s = vcs.get_snippet(ctx, snippet_id)
    repo = vcs.repo_of(ctx, s.folder_id)
    tm = vcs.tag_map(repo)
    return [vcs.commit_out(r, tm) for r in git.log(repo, s.id)]


@router.get("/snippets/{snippet_id}/diff")
def diff(snippet_id: UUID, from_: str = Query(alias="from", max_length=64), to: str = Query(max_length=64),
         ctx: Ctx = V):
    s = vcs.get_snippet(ctx, snippet_id)
    a, b = vcs.rev_version(ctx, s, from_), vcs.rev_version(ctx, s, to)
    return dict(from_sha=a["sha"], to_sha=b["sha"], **C.diff(s.id, a, b))
