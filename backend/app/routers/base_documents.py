import re
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Response, UploadFile
from sqlalchemy import text

from .. import base_docs
from ..deps import Ctx, require
from ..git import repo as git
from ..models import BaseDocument, BaseDocVersion, Role
from ..schemas import BaseDocOut, BaseDocVersionOut

router = APIRouter(tags=["base-documents"])
V = Depends(require(Role.viewer))
A = Depends(require(Role.admin))


def _get(ctx: Ctx, id: UUID) -> BaseDocument:
    bd = ctx.repo(BaseDocument).get(id)
    if not bd:
        raise HTTPException(404, "base document not found")
    return bd


def _versions(ctx: Ctx, bd: BaseDocument) -> list[BaseDocVersion]:
    return sorted(ctx.repo(BaseDocVersion).list(BaseDocVersion.base_document_id == bd.id), key=lambda v: -v.number)


def _out(ctx: Ctx, bd: BaseDocument) -> BaseDocOut:
    vs = _versions(ctx, bd)
    return BaseDocOut(id=bd.id, name=bd.name, head=vs[0] if vs else None)


def _read(file: UploadFile) -> bytes:
    data = file.file.read(base_docs.MAX_BYTES + 1)
    if len(data) > base_docs.MAX_BYTES:
        raise HTTPException(413, "file too large (max 25 MB)")
    return data


def _store(ctx: Ctx, bd: BaseDocument, data: bytes, filename: str, message: str) -> BaseDocVersion:
    try:
        info = base_docs.validate(data)
    except base_docs.BadDocx as e:
        raise HTTPException(422, str(e))
    ctx.db.execute(text("select pg_advisory_xact_lock(hashtext(:k))"), {"k": f"base:{ctx.org_id}"})  # one writer per _base.git
    repo = git.base_repo_path(ctx.org_id)
    if not repo.exists():
        git.init(repo)
    vs = _versions(ctx, bd)
    n = vs[0].number + 1 if vs else 1
    msg = (message.strip() or f"Upload version {n}") + f"\n\nBase-Document-Id: {bd.id}\n"
    try:
        sha = git.commit(repo, re.sub(r"[<>\n]", "", ctx.user.name).strip() or "user", ctx.user.email, msg,
                         {f"base/{bd.id}.docx": data})
    except git.GitError as e:
        ctx.db.rollback()
        raise HTTPException(409, f"git commit failed: {e}")
    return ctx.repo(BaseDocVersion).add(
        base_document_id=bd.id, sha=sha, number=n, filename=re.sub(r"[^A-Za-z0-9._ -]", "_", filename or "base.docx")[:200],
        size=len(data), message=message.strip()[:500], styles=info["styles"], placeholders=info["placeholders"],
        created_by=ctx.user.id)


@router.get("/base-documents", response_model=list[BaseDocOut])
def list_base_documents(ctx: Ctx = V):
    return sorted((_out(ctx, b) for b in ctx.repo(BaseDocument).list()), key=lambda b: b.name.lower())


@router.post("/base-documents", status_code=201, response_model=BaseDocOut)
def create_base_document(name: str = Form(min_length=1, max_length=100), file: UploadFile | None = File(default=None),
                         ctx: Ctx = A):
    """Without `file`, the bundled default base document is created."""
    data, fn = (_read(file), file.filename) if file else (base_docs.default_docx(), "default.docx")
    bd = ctx.repo(BaseDocument).add(name=name)
    _store(ctx, bd, data, fn, "")
    ctx.db.commit()
    return _out(ctx, bd)


@router.get("/base-documents/{id}")
def get_base_document(id: UUID, ctx: Ctx = V):
    bd = _get(ctx, id)
    vs = _versions(ctx, bd)
    return dict(**_out(ctx, bd).model_dump(), styles=vs[0].styles, version_count=len(vs))


@router.post("/base-documents/{id}/versions", status_code=201, response_model=BaseDocVersionOut)
def new_version(id: UUID, file: UploadFile, message: str = Form(default="", max_length=500), ctx: Ctx = A):
    v = _store(ctx, _get(ctx, id), _read(file), file.filename, message)
    ctx.db.commit()
    return v


@router.get("/base-documents/{id}/versions", response_model=list[BaseDocVersionOut])
def versions(id: UUID, ctx: Ctx = V):
    return _versions(ctx, _get(ctx, id))


@router.get("/base-documents/{id}/download")
def download(id: UUID, version: str | None = Query(default=None, pattern=r"^[0-9a-f]{40}$"), ctx: Ctx = V):
    bd = _get(ctx, id)
    v = (ctx.repo(BaseDocVersion).first(BaseDocVersion.base_document_id == bd.id, BaseDocVersion.sha == version)
         if version else (_versions(ctx, bd) or [None])[0])
    data = v and git.show(git.base_repo_path(ctx.org_id), v.sha, f"base/{bd.id}.docx")
    if not data:
        raise HTTPException(404, "version not found")
    name = re.sub(r"[^A-Za-z0-9._-]", "_", bd.name) or "base"
    return Response(data, media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    headers={"Content-Disposition": f'attachment; filename="{name}-v{v.number}.docx"',
                             "X-Content-Type-Options": "nosniff"})
