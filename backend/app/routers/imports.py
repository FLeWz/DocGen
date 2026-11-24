import re
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Response, UploadFile

from .. import base_docs, docx_import as D, vcs
from ..deps import Ctx, require
from ..files import image_path, import_path
from ..models import BaseDocument, Image, Import, Role
from ..ooxml import rel_ids
from ..schemas import ImportBaseIn, ImportPut, ImportSnippetOut, SelectionIn, SplitIn
from . import images
from .base_documents import _get as get_base, _read, _store

router = APIRouter(tags=["imports"])
V = Depends(require(Role.viewer))
E = Depends(require(Role.editor))
A = Depends(require(Role.admin))
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
MAX_SECTIONS = 200


def _get(ctx: Ctx, id: UUID) -> Import:
    imp = ctx.repo(Import).get(id)
    if not imp:
        raise HTTPException(404, "import not found")
    return imp


def _orig(ctx: Ctx, imp: Import) -> bytes:
    return import_path(ctx.org_id, imp.id).read_bytes()


def _meta(i: Import) -> dict:
    return dict(id=i.id, title=i.title, filename=i.filename, created_at=i.created_at, updated_at=i.updated_at)


@router.post("/import/docx", status_code=201)
def import_docx(file: UploadFile = File(), title: str | None = Form(default=None, max_length=200), ctx: Ctx = E):
    data = _read(file)
    try:  # same gate as base documents: zip structure, bombs, XXE, macros
        base_docs.validate(data)

        def store(b: bytes):
            try:
                return str(images.save(ctx, b).id)
            except HTTPException:
                return None
        content, warnings = D.parse(data, store)
    except base_docs.BadDocx as e:
        raise HTTPException(422, str(e))
    if len(str(content)) > vcs.MAX_DOC:
        raise HTTPException(413, "document too large to edit")
    fn = re.sub(r"[^A-Za-z0-9._ -]", "_", file.filename or "import.docx")[:200]
    imp = ctx.repo(Import).add(title=(title or fn.rsplit(".", 1)[0] or "Imported document")[:200], filename=fn,
                               content=content, warnings=warnings, created_by=ctx.user.id)
    p = import_path(ctx.org_id, imp.id)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    ctx.db.commit()
    return dict(**_meta(imp), warnings=warnings)


@router.get("/imports")
def list_imports(ctx: Ctx = V):
    return [_meta(i) for i in sorted(ctx.repo(Import).list(), key=lambda i: i.created_at, reverse=True)]


@router.get("/imports/{import_id}")
def get_import(import_id: UUID, ctx: Ctx = V):
    i = _get(ctx, import_id)
    return dict(**_meta(i), content=i.content, warnings=i.warnings)


@router.delete("/imports/{import_id}", status_code=204)
def delete_import(import_id: UUID, ctx: Ctx = E):
    """Row + original file; images it created stay until the cleanup endpoint finds them unreferenced."""
    i = _get(ctx, import_id)
    ctx.repo(Import).delete(i)
    ctx.db.commit()
    import_path(ctx.org_id, i.id).unlink(missing_ok=True)


@router.put("/imports/{import_id}")
def put_import(import_id: UUID, body: ImportPut, ctx: Ctx = E):
    i = _get(ctx, import_id)
    if len(str(body.content)) > vcs.MAX_DOC:
        raise HTTPException(413, "document too large")
    try:
        D.check_content(body.content, rel_ids(_orig(ctx, i)))
    except ValueError as e:
        raise HTTPException(422, f"invalid opaque block: {e}")
    i.content = body.content
    if body.title:
        i.title = body.title
    ctx.db.commit()
    return dict(**_meta(i), content=i.content, warnings=i.warnings)


@router.get("/imports/{import_id}/export")
def export_import(import_id: UUID, ctx: Ctx = V):
    i = _get(ctx, import_id)

    def asset(_c, iid):
        img = ctx.repo(Image).get(UUID(str(iid))) if re.fullmatch(r"[0-9a-f-]{36}", str(iid)) else None
        return img and image_path(ctx.org_id, img.id, img.ext).read_bytes()
    try:
        data, warnings = D.export(_orig(ctx, i), i.content, asset)
    except Exception as e:  # python-docx on content that the editor produced but Word structure rejects
        raise HTTPException(422, f"export failed: {type(e).__name__}: {e}")
    name = re.sub(r"[^A-Za-z0-9._-]", "_", i.title) or "document"
    return Response(data, media_type=DOCX, headers={
        "Content-Disposition": f'attachment; filename="{name}.docx"', "X-Content-Type-Options": "nosniff",
        "X-Export-Warnings": str(len(warnings))})


def _sections(content: dict, level: int) -> list[dict]:
    """Top-level blocks cut before every Heading <= level; blocks before the first heading form 'Front matter'."""
    blocks, cuts = content.get("content") or [], []
    for k, b in enumerate(blocks):
        if b.get("type") == "heading" and (b.get("attrs") or {}).get("level", 1) <= level:
            cuts.append(k)
    bounds = ([0] if not cuts or cuts[0] > 0 else []) + cuts + [len(blocks)]
    out = []
    for s, e in zip(bounds, bounds[1:]):
        head = blocks[s] if s in cuts else None
        title = (D.block_text(head).strip() if head else "Front matter") or "Untitled section"
        if head is None and not any(D.block_text(b).strip() or b.get("type") != "paragraph" for b in blocks[s:e]):
            continue  # nothing but empty paragraphs before the first heading
        out.append(dict(title=title[:200], start=s, end=e, blocks=e - s, level=(head or {}).get("attrs", {}).get("level")))
    return out


@router.get("/imports/{import_id}/split")
def split_preview(import_id: UUID, level: int = Query(ge=1, le=2), ctx: Ctx = V):
    return _sections(_get(ctx, import_id).content, level)


def _make(ctx: Ctx, folder, title: str, blocks: list) -> ImportSnippetOut:
    blocks = [{**b, "attrs": {k: v for k, v in b["attrs"].items() if k != "orig"}} if "orig" in (b.get("attrs") or {}) else b for b in blocks]
    content = {"type": "doc", "content": blocks}  # `orig` only means something inside the import's own package
    s = vcs.new_snippet(ctx, folder, title, content=content, styles=D.style_ids(content))
    kinds = sorted({(n.get("attrs") or {}).get("kind", "?") for b in blocks for n in D.walk(b) if n.get("type") == "opaque"})
    warn = [f"contains opaque block(s): {', '.join(kinds)} (exported only if self-contained)"] if kinds else []
    return ImportSnippetOut(id=s.id, folder_id=folder.id, slug=s.slug, title=s.title, head_sha=None, has_draft=True, warnings=warn)


@router.post("/imports/{import_id}/split", status_code=201, response_model=list[ImportSnippetOut])
def split_apply(import_id: UUID, body: SplitIn, ctx: Ctx = E):
    i, f = _get(ctx, import_id), vcs.get_folder(ctx, body.folder_id)
    secs = _sections(i.content, body.level)
    if not secs:
        raise HTTPException(422, "nothing to split")
    if len(secs) > MAX_SECTIONS:
        raise HTTPException(422, f"too many sections (max {MAX_SECTIONS})")
    out = [_make(ctx, f, s["title"], i.content["content"][s["start"]:s["end"]]) for s in secs]
    ctx.db.commit()
    return out


@router.post("/imports/{import_id}/selection-snippet", status_code=201, response_model=ImportSnippetOut)
def selection_snippet(import_id: UUID, body: SelectionIn, ctx: Ctx = E):
    i, f = _get(ctx, import_id), vcs.get_folder(ctx, body.folder_id)
    blocks = i.content.get("content") or []
    if body.end > len(blocks) or body.start >= body.end:
        raise HTTPException(422, f"block range out of bounds (0..{len(blocks)})")
    out = _make(ctx, f, body.title, blocks[body.start:body.end])
    ctx.db.commit()
    return out


@router.post("/imports/{import_id}/base-document", status_code=201)
def use_as_base(import_id: UUID, body: ImportBaseIn, ctx: Ctx = A):
    i = _get(ctx, import_id)
    if bool(body.name) == bool(body.base_document_id):
        raise HTTPException(422, "give either name (new base document) or base_document_id (new version)")
    bd = get_base(ctx, body.base_document_id) if body.base_document_id else ctx.repo(BaseDocument).add(name=body.name)
    v = _store(ctx, bd, D.as_base_doc(_orig(ctx, i)), i.filename, f"From import '{i.title}'")
    ctx.db.commit()
    return dict(base_document_id=bd.id, name=bd.name, version=v.number, sha=v.sha, placeholders=v.placeholders)
