import re
import shutil
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Response

from .. import docs, export, files, vcs
from ..deps import Ctx, require
from ..git import content as C, repo as git
from ..models import DocDraft, DocRef, Document, Export, Folder, Role, Snippet
from ..schemas import DocCreateIn, DocDraftIn, DocOut, ExportOut

router = APIRouter(tags=["documents"])
V = Depends(require(Role.viewer))
E = Depends(require(Role.editor))


def _row(d: Document, mine: set) -> DocOut:
    return DocOut(id=d.id, folder_id=d.folder_id, slug=d.slug, title=d.title, head_sha=d.head_sha, has_draft=d.id in mine)


@router.get("/documents", response_model=list[DocOut])
def list_documents(folder_id: UUID | None = None, ctx: Ctx = V):
    rows = ctx.repo(Document).list(*([Document.folder_id == folder_id] if folder_id else []))
    mine = {r.document_id for r in ctx.repo(DocDraft).list(DocDraft.user_id == ctx.user.id)}
    return [_row(d, mine) for d in sorted(rows, key=lambda d: d.title.lower())]


@router.post("/documents", status_code=201, response_model=DocOut)
def create_document(body: DocCreateIn, ctx: Ctx = E):
    f = vcs.get_folder(ctx, body.folder_id)
    taken = {d.slug for d in ctx.repo(Document).list(Document.folder_id == f.id)}
    if body.slug:
        if body.slug in taken:
            raise HTTPException(409, "slug already used in this folder")
        slug = body.slug
    else:
        base = re.sub(r"[^a-z0-9]+", "-", body.title.lower()).strip("-")[:56] or "document"
        slug, n = base, 2
        while slug in taken:
            slug, n = f"{base}-{n}", n + 1
    d = ctx.repo(Document).add(folder_id=f.id, slug=slug, title=body.title)
    ctx.repo(DocDraft).add(document_id=d.id, user_id=ctx.user.id, title=d.title, slug=slug, body=docs.empty_body(),
                           base_sha=None)
    ctx.db.commit()
    return _row(d, {d.id})


@router.get("/documents/{document_id}")
def get_document(document_id: UUID, version: str | None = Query(default=None, pattern=r"^[0-9a-f]{7,40}$"),
                 ctx: Ctx = V):
    return docs.doc_out(ctx, docs.get_document(ctx, document_id), version)


@router.put("/documents/{document_id}/draft")
def put_draft(document_id: UUID, body: DocDraftIn, ctx: Ctx = E):
    d = docs.get_document(ctx, document_id)
    docs.save_draft(ctx, d, body)
    out = docs.doc_out(ctx, d)
    ctx.db.commit()
    return out


@router.delete("/documents/{document_id}/draft", status_code=204)
def discard_draft(document_id: UUID, ctx: Ctx = E):
    dr = docs.draft_of(ctx, docs.get_document(ctx, document_id))
    if dr:
        ctx.repo(DocDraft).delete(dr)
        ctx.db.commit()
    return Response(status_code=204)


@router.delete("/documents/{document_id}", status_code=204)
def delete_document(document_id: UUID, ctx: Ctx = E):
    """Removes the document from the index and (if committed) deletes its file in a final commit."""
    d = docs.get_document(ctx, document_id)
    ctx.db.scalar(ctx.repo(Folder).query().where(Folder.id == d.folder_id).with_for_update())
    paths = [files.export_path(ctx.org_id, e.id, e.format) for e in ctx.repo(Export).list(Export.document_id == d.id)]
    repo = vcs.repo_of(ctx, d.folder_id)
    # overrides of commits that only this document owns go with it (shared commits keep theirs)
    vcs.drop_names(ctx, d.folder_id, (r["sha"] for r in git.log(repo, d.id, key="Document-Id")
                                      if len(vcs.TRAILER.findall(r["body"])) == 0 and len(vcs.DOC_TRAILER.findall(r["body"])) == 1))
    if d.head_sha:
        try:
            git.commit(vcs.repo_of(ctx, d.folder_id), re.sub(r"[<>\n]", "", ctx.user.name).strip() or "user",
                       ctx.user.email, f"Delete document {d.slug}\n\nDocument-Id: {d.id}\n", {},
                       deletes=[docs.path(d.slug)])
        except git.GitError as e:
            raise HTTPException(409, f"git commit failed: {e}")
    ctx.repo(Document).delete(d)
    ctx.db.commit()
    for p in paths:
        p.unlink(missing_ok=True)
    shutil.rmtree(files.cache_dir(ctx.org_id, d.id), ignore_errors=True)
    return Response(status_code=204)


@router.get("/documents/{document_id}/history")
def history(document_id: UUID, ctx: Ctx = V):
    d = docs.get_document(ctx, document_id)
    repo = vcs.repo_of(ctx, d.folder_id)
    tm = vcs.tag_map(repo)
    nm = vcs.names(ctx, d.folder_id)
    return [vcs.commit_out(r, tm, nm) for r in git.log(repo, d.id, key="Document-Id")]


@router.get("/documents/{document_id}/updates")
def updates(document_id: UUID, ctx: Ctx = V):
    """Per snippet block: resolved pin, latest sha, newer commits, update_available."""
    return docs.refs_status(ctx, docs.working(ctx, docs.get_document(ctx, document_id))["blocks"])


def _block(ctx: Ctx, d: Document, block_id: str) -> tuple[dict, Snippet]:
    b = next((b for b in docs.working(ctx, d)["blocks"] if b["id"] == block_id and b["type"] == "snippet_ref"), None)
    s = b and ctx.repo(Snippet).get(UUID(b["snippet_id"]))
    if not s:
        raise HTTPException(404, "block not found")
    return b, s


@router.get("/documents/{document_id}/blocks/{block_id}/bump")
def bump_preview(document_id: UUID, block_id: str, ctx: Ctx = V):
    b, s = _block(ctx, docs.get_document(ctx, document_id), block_id)
    old = docs.pin_sha(ctx, b, s)
    if not old or not s.head_sha:
        raise HTTPException(404, "version not found")
    repo = vcs.repo_of(ctx, s.folder_id)
    return dict(from_sha=old, to_sha=s.head_sha, **C.diff(s.id, C.version_at(repo, old, s.id), C.version_at(repo, s.head_sha, s.id)))


@router.post("/documents/{document_id}/blocks/{block_id}/bump")
def bump_apply(document_id: UUID, block_id: str, ctx: Ctx = E):
    d = docs.get_document(ctx, document_id)
    b, s = _block(ctx, d, block_id)
    if b["version"]["type"] == "latest" or docs.pin_sha(ctx, b, s) == s.head_sha:
        raise HTTPException(409, "already at the latest version")
    w = docs.working(ctx, d)
    for x in w["blocks"]:
        if x["id"] == block_id:
            x["version"] = dict(type="commit", value=s.head_sha)
    body = DocDraftIn(**{k: w[k] for k in docs.BODY})
    docs.save_draft(ctx, d, body)
    out = docs.doc_out(ctx, d)
    ctx.db.commit()
    return out


@router.get("/documents/{document_id}/revision-history")
def revision_history(document_id: UUID, ctx: Ctx = V):
    """The auto-built rows (git log of the document and its pinned snippets) for the builder's editable preview."""
    d = docs.get_document(ctx, document_id)
    w = docs.working(ctx, d)
    br = w["base_document"]
    return export.revision_rows(ctx, d, w["blocks"], br and dict(id=br["id"], sha=br["version"]))


# ---- export ----

@router.get("/documents/{document_id}/export", status_code=202)
def start_export(document_id: UUID, format: str = Query(default="docx", max_length=8), reuse: bool = False, ctx: Ctx = V):
    """Queues a background job (GET per the API spec); poll /api/exports/{id}, then /download. `reuse` serves the latest finished
    export with an identical spec (the preview's cache) instead of rendering again."""
    if format not in export.RENDERERS:
        raise HTTPException(400, f"unsupported format '{format}' (available: {', '.join(export.RENDERERS)})")
    d = docs.get_document(ctx, document_id)
    if reuse and (hit := export.cached(ctx, d, format)):
        return dict(export_id=hit.id, status=hit.status, reused=True)
    e = export.create(ctx, d, format)
    ctx.db.commit()
    export.submit(ctx.org_id, ctx.user.id, e.id)
    return dict(export_id=e.id, status=e.status)


@router.get("/documents/{document_id}/versions/{sha}/export", status_code=202)
def export_version(document_id: UUID, sha: str = Path(pattern=r"^[0-9a-f]{40}$"), format: str = Query(default="docx", max_length=8),
                   ctx: Ctx = V):
    """Export the document as committed at `sha`; same job flow as /export (poll /api/exports/{id}, then /download).
    `cached: true` = the finished file was served from the render cache."""
    if format not in export.RENDERERS:
        raise HTTPException(400, f"unsupported format '{format}' (available: {', '.join(export.RENDERERS)})")
    return export.start_version(ctx, docs.get_document(ctx, document_id), sha, format)


@router.get("/documents/{document_id}/exports", response_model=list[ExportOut])
def list_exports(document_id: UUID, ctx: Ctx = V):
    d = docs.get_document(ctx, document_id)
    return sorted(ctx.repo(Export).list(Export.document_id == d.id), key=lambda e: e.created_at, reverse=True)
