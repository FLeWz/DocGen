from fastapi import APIRouter, Depends

from ..deps import Ctx, require
from ..models import Document, Role, Snippet

router = APIRouter(tags=["overview"])
LIMIT = 2000  # shortcut: one flat list drives tree counts, dashboard and client-side search; paginate/search server-side beyond this


@router.get("/overview")
def overview(ctx: Ctx = Depends(require(Role.viewer))):
    """Per-folder counts + every snippet/document (newest first) for the sidebar, dashboard and global search."""
    items = [dict(kind=kind, id=r.id, folder_id=r.folder_id, title=r.title, slug=r.slug, head_sha=r.head_sha, created_at=r.created_at)
             for kind, model in (("snippet", Snippet), ("document", Document)) for r in ctx.repo(model).list()]
    items.sort(key=lambda i: i["created_at"], reverse=True)
    counts: dict = {}
    for i in items:
        counts.setdefault(str(i["folder_id"]), dict(snippets=0, documents=0))[i["kind"] + "s"] += 1
    return dict(counts=counts, items=items[:LIMIT])
