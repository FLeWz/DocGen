"""Shared folder/snippet/draft logic for the git-backed routers."""
import json
import re
from datetime import datetime, timezone
import unicodedata
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import func

from . import locks
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
    d = ctx.repo(Draft).add(snippet_id=s.id, user_id=ctx.user.id, title=title, slug=slug,
                            content=content or C.EMPTY_DOC, styles=styles or [], variables=[], base_sha=None)
    touch(d, ctx.user)
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


def touch(d, user) -> None:
    """`user` is now the draft's last editor and (latest) contributor; reassigned so JSONB change is seen."""
    d.user_id = user.id
    d.contributors = [c for c in d.contributors or [] if c["id"] != str(user.id)] + [
        dict(id=str(user.id), name=user.name, email=user.email, at=datetime.now(timezone.utc).isoformat())]


def dver(d: Draft) -> dict:
    return dict(slug=d.slug, title=d.title, styles=d.styles, variables=d.variables, content=d.content)


def current_draft(ctx: Ctx, s: Snippet) -> Draft | None:
    """The snippet's shared draft. A draft that was never edited past its base silently follows HEAD."""
    d = ctx.repo(Draft).first(Draft.snippet_id == s.id)
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
    """Upsert the shared draft (caller must hold the edit lock); omitted optional fields keep their current value."""
    if len(json.dumps(body.content)) > MAX_DOC:
        raise HTTPException(413, "document too large")
    locks.require(ctx.db, ctx.org_id, ctx.user, "snippet", s.id)
    d = ctx.repo(Draft).first(Draft.snippet_id == s.id)
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
    touch(d, ctx.user)
    return d


def editor(ctx: Ctx, d) -> dict | None:
    u = ctx.db.get(User, d.user_id)
    return u and dict(id=str(u.id), name=u.name, at=d.updated_at)


def draft_out(ctx: Ctx, s: Snippet, d: Draft | None) -> dict:
    if d:
        return dict(**dver(d), snippet_id=s.id, folder_id=s.folder_id, exists=True, base_sha=d.base_sha,
                    contributors=d.contributors, last_editor=editor(ctx, d),
                    head_sha=s.head_sha, conflict=d.base_sha != s.head_sha, dirty=dirty(ctx, s, d))
    h = head_version(ctx, s) or dict(title=s.title, slug=s.slug, styles=[], variables=[], content=C.EMPTY_DOC)
    return dict(**{k: h[k] for k in ("title", "slug", "styles", "variables", "content")}, snippet_id=s.id,
                folder_id=s.folder_id, exists=False, base_sha=s.head_sha, head_sha=s.head_sha,
                conflict=False, dirty=False, contributors=[], last_editor=None)


TRAILER = re.compile(r"^Snippet-Id: ([0-9a-f-]{36})$", re.M)
DOC_TRAILER = re.compile(r"^Document-Id: ([0-9a-f-]{36})$", re.M)
COAUTHOR = re.compile(r"^Co-authored-by: (.+?) <([^>]*)>$", re.M)
TRANSFER = re.compile(r"^Lock-transferred: (.+)$", re.M)
ANY_TRAILER = re.compile(r"^(?:Snippet-Id|Document-Id|Co-authored-by|Lock-transferred):.*$", re.M)  # server-written only: stripped from user text


def trailers(committer, drafts) -> str:
    """Co-authored-by for every contributor of the drafts but the committer, Lock-transferred for every hand-over."""
    clean = lambda t: re.sub(r"[<>\n]", "", t or "").strip()
    seen, out = {str(committer.id)}, []
    for d in drafts:
        for c in d.contributors or []:
            if c["id"] not in seen:
                seen.add(c["id"])
                out.append(f"Co-authored-by: {clean(c['name']) or 'user'} <{clean(c['email'])}>")
        for t in d.transfers or []:
            line = f"Lock-transferred: {clean(t['from'])} -> {clean(t['to'])} ({t['kind']})"
            if line not in out:
                out.append(line)
    return "".join(x + "\n" for x in out)


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
    return dict(**row, **name_fields((nm or {}).get(row["sha"]), ANY_TRAILER.sub("", body).strip()), snippet_ids=TRAILER.findall(body), co_authors=[dict(name=n, email=e) for n, e in COAUTHOR.findall(body)],
                lock_transfers=TRANSFER.findall(body),
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
