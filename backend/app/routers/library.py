from collections import defaultdict
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from .. import vcs
from ..deps import Ctx, require
from ..git import repo as git
from ..models import DocRef, Document, Folder, Role, Snippet

router = APIRouter(tags=["library"])


@router.get("/library/snippets")
def library(q: str | None = Query(default=None, max_length=100), folder_id: UUID | None = None,
            tag: str | None = Query(default=None, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$"),
            ctx: Ctx = Depends(require(Role.viewer))):
    """Committed snippets of all org folders, grouped by folder. `tag`: only snippets that exist at a commit
    carrying that git tag (in their own folder). Uncommitted snippets can't be pinned, so they're not listed."""
    snips = defaultdict(list)
    for s in ctx.repo(Snippet).list(Snippet.head_sha.is_not(None)):
        if not q or q.lower() in s.title.lower() or q.lower() in s.slug.lower():
            snips[s.folder_id].append(s)
    out = []
    for f in sorted(ctx.repo(Folder).list(*([Folder.id == folder_id] if folder_id else [])), key=lambda f: f.name.lower()):
        repo = vcs.repo_of(ctx, f.id)
        tags = git.tags(repo) if snips[f.id] or tag else []
        rows = snips[f.id]
        if tag:
            t = next((t for t in tags if t["name"] == tag), None)
            rows = [s for s in rows if t and git.slug_at(repo, t["sha"], s.id)]
        if rows:
            out.append(dict(folder_id=f.id, folder_name=f.name, tags=[t["name"] for t in tags],
                            snippets=[dict(id=s.id, title=s.title, slug=s.slug, head_sha=s.head_sha)
                                      for s in sorted(rows, key=lambda s: s.title.lower())]))
    return out


@router.get("/snippets/{snippet_id}/used-by")
def used_by(snippet_id: UUID, ctx: Ctx = Depends(require(Role.viewer))):
    """Documents whose committed versions reference the snippet, with the pin used in each version."""
    s = vcs.get_snippet(ctx, snippet_id)
    by_doc = defaultdict(list)
    for r in ctx.repo(DocRef).list(DocRef.snippet_id == s.id):
        by_doc[r.document_id].append(r)
    out = []
    for d in ctx.repo(Document).list(Document.id.in_(list(by_doc))):
        f = ctx.repo(Folder).get(d.folder_id)
        out.append(dict(document_id=d.id, title=d.title, folder_id=d.folder_id, folder_name=f and f.name,
                        head_sha=d.head_sha,
                        versions=[dict(commit_sha=r.commit_sha, pin=dict(type=r.pin_type, value=r.pin_value),
                                       current=r.commit_sha == d.head_sha) for r in by_doc[d.id]]))
    return sorted(out, key=lambda x: x["title"].lower())
