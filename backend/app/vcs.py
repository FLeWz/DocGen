"""Shared folder/snippet/draft logic for the git-backed routers."""
import json
import re
import unicodedata
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import func

from .deps import Ctx
from .git import content as C, repo as git
from .models import CommitName, Draft, Folder, Snippet, User

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


def slugify(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:56] or "snippet"


def new_snippet(ctx: Ctx, f: Folder, title: str, slug: str | None = None, content: dict | None = None,
                styles: list | None = None, source: dict | None = None) -> Snippet:
    """Snippet row + the caller's draft (no commit). Slug derived from the title when not given; 409 if taken."""
    taken = {s.slug for s in ctx.repo(Snippet).list(Snippet.folder_id == f.id)}
    if slug:
        if slug in taken:
            raise HTTPException(409, "slug already used in this folder")
    else:
        base = slugify(title)
        slug, n = base, 2
        while slug in taken:
            slug, n = f"{base}-{n}", n + 1
    s = ctx.repo(Snippet).add(folder_id=f.id, slug=slug, title=title, source=source)
    ctx.repo(Draft).add(snippet_id=s.id, user_id=ctx.user.id, title=title, slug=slug,
                        content=content or C.EMPTY_DOC, styles=styles or [], variables=[], base_sha=None)
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
DOC_TRAILER = re.compile(r"^Document-Id: ([0-9a-f-]{36})$", re.M)
ANY_TRAILER = re.compile(r"^(?:Snippet|Document)-Id:.*$", re.M)  # server-written only: stripped from user text


BASE_SCOPE = "base"


def names(ctx: Ctx, scope) -> dict:
    """sha -> override of one repo (folder id or BASE_SCOPE)."""
    out = {}
    for n in ctx.repo(CommitName).list(CommitName.scope == str(scope)):
        u = n.renamed_by and ctx.db.get(User, n.renamed_by)
        out[n.sha] = dict(name=n.name, by=u.name if u else None, at=n.renamed_at)
    return out


def name_fields(n: dict | None, original: str) -> dict:
    """The ONE place that decides what a commit is called: the override, else the immutable git message."""
    return dict(message=n["name"] if n else original, original_message=original, name=n and n["name"], renamed=bool(n),
                renamed_by=n and n["by"], renamed_at=n and n["at"])


def commit_out(row: dict, tagmap: dict, nm: dict | None = None) -> dict:
    body = row.pop("body")
    return dict(**row, **name_fields((nm or {}).get(row["sha"]), ANY_TRAILER.sub("", body).strip()), snippet_ids=TRAILER.findall(body),
                document_ids=DOC_TRAILER.findall(body), tags=tagmap.get(row["sha"], []))


def rename(ctx: Ctx, scope, sha: str, name: str | None, original: str) -> dict | None:
    """Set/replace (name) or remove (None/blank) the override. Caller has checked that sha is a commit of the repo."""
    name = (name or "").strip()
    if any(unicodedata.category(c) == "Cc" for c in name):
        raise HTTPException(422, "name must not contain control characters")
    if len(name) > 200:
        raise HTTPException(422, "name is too long (max 200)")
    row = ctx.repo(CommitName).first(CommitName.scope == str(scope), CommitName.sha == sha)
    if not name:
        row and ctx.repo(CommitName).delete(row)
    elif row:
        row.name, row.renamed_by, row.renamed_at = name, ctx.user.id, func.now()
    else:
        ctx.repo(CommitName).add(scope=str(scope), sha=sha, name=name, renamed_by=ctx.user.id)
    ctx.db.commit()
    return names(ctx, scope).get(sha)


def drop_names(ctx: Ctx, scope, shas) -> None:
    for n in ctx.repo(CommitName).list(CommitName.scope == str(scope), CommitName.sha.in_(list(shas))):
        ctx.repo(CommitName).delete(n)


def tag_map(repo) -> dict:
    m: dict = {}
    for t in git.tags(repo):
        m.setdefault(t["sha"], []).append(t["name"])
    return m
