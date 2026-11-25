import io
import re
import zipfile
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, Path, Query, Response, UploadFile
from sqlalchemy import func, text
from sqlalchemy.exc import IntegrityError

from .. import base_docs, docx_vars, vcs
from ..ooxml import neutralize_ole
from ..deps import Ctx, require
from ..git import repo as git
from ..models import BaseDocument, BaseDocVersion, DocDraft, Document, Export, Role
from ..schemas import BaseDocOut, BaseDocVersionOut, BaseDocRename, CommitRename, VarPicks, Variables
from .. import docs

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


def named(ctx: Ctx, vs: list[BaseDocVersion]) -> list[BaseDocVersionOut]:
    """Versions with their display names (vcs.name_fields): `message` = override or original."""
    nm = vcs.names(ctx, vcs.BASE_SCOPE)
    return [BaseDocVersionOut.model_validate(v, from_attributes=True).model_copy(update=vcs.name_fields(nm.get(v.sha), v.message)) for v in vs]


class NameExists(Exception):
    def __init__(self, bd: BaseDocument, head: BaseDocVersion | None):
        self.body = dict(error="name_exists", base_document_id=str(bd.id), name=bd.name, current_version=head.number if head else None)


def new_base(ctx: Ctx, name: str, exclude: UUID | None = None) -> str:
    """Trimmed name, or NameExists (409) when another base document of the org has it (case-insensitive)."""
    name = name.strip()
    if not name:
        raise HTTPException(422, "name is required")
    hit = ctx.repo(BaseDocument).first(func.lower(BaseDocument.name) == name.lower(), *([BaseDocument.id != exclude] if exclude else []))
    if hit:
        vs = _versions(ctx, hit)
        raise NameExists(hit, vs[0] if vs else None)
    return name


def add_base(ctx: Ctx, name: str) -> BaseDocument:
    name = new_base(ctx, name)
    try:
        return ctx.repo(BaseDocument).add(name=name)
    except IntegrityError:  # lost a race with the same name: the unique index decides
        ctx.db.rollback()
        raise HTTPException(409, "a base document with this name already exists")


def _usage(ctx: Ctx) -> dict[str, list[dict]]:
    """base document id -> documents pinning it: the committed version (HEAD) or any user's draft."""
    out: dict[str, list[dict]] = {}
    def add(bid, d: Document, state: str, title: str):
        out.setdefault(str(bid), []).append(dict(document_id=d.id, folder_id=d.folder_id, title=title, state=state))
    byid = {d.id: d for d in ctx.repo(Document).list()}
    for d in byid.values():
        hv = d.head_sha and docs.head_version(ctx, d)
        if hv and hv.get("base_document"):
            add(hv["base_document"]["id"], d, "committed", hv["title"])
    for dr in ctx.repo(DocDraft).list():
        if (dr.body.get("base_document") or {}).get("id") and dr.document_id in byid:
            add(dr.body["base_document"]["id"], byid[dr.document_id], "draft", dr.title)
    return out


def _out(ctx: Ctx, bd: BaseDocument, usage: dict | None = None) -> BaseDocOut:
    vs = _versions(ctx, bd)
    used = (usage if usage is not None else _usage(ctx)).get(str(bd.id), [])
    return BaseDocOut(id=bd.id, name=bd.name, head=named(ctx, vs[:1])[0] if vs else None, version_count=len(vs), created_at=bd.created_at,
                      updated_at=vs[0].created_at if vs else bd.created_at, used_by=len({u["document_id"] for u in used}))


def _read(file: UploadFile) -> bytes:
    data = file.file.read(base_docs.MAX_BYTES + 1)
    if len(data) > base_docs.MAX_BYTES:
        raise HTTPException(413, "file too large (max 25 MB)")
    return data


def _store(ctx: Ctx, bd: BaseDocument, data: bytes, filename: str, message: str, variables: list | None = None) -> BaseDocVersion:
    try:
        info = base_docs.validate(data)
        data, ole_warn = neutralize_ole(data)
    except base_docs.BadDocx as e:
        raise HTTPException(422, str(e))
    ctx.db.execute(text("select pg_advisory_xact_lock(hashtext(:k))"), {"k": f"base:{ctx.org_id}"})  # one writer per _base.git
    repo = git.base_repo_path(ctx.org_id)
    if not repo.exists():
        git.init(repo)
    vs = _versions(ctx, bd)
    n = vs[0].number + 1 if vs else 1
    msg = (message.strip() or f"Upload version {n}") + f"\n\nBase-Document-Id: {bd.id}\n" + "".join(f"Warning: {w}\n" for w in ole_warn)
    try:
        sha = git.commit(repo, re.sub(r"[<>\n]", "", ctx.user.name).strip() or "user", ctx.user.email, msg,
                         {f"base/{bd.id}.docx": data})
    except git.GitError as e:
        ctx.db.rollback()
        raise HTTPException(409, f"git commit failed: {e}")
    return ctx.repo(BaseDocVersion).add(
        base_document_id=bd.id, sha=sha, number=n, filename=re.sub(r"[^A-Za-z0-9._ -]", "_", filename or "base.docx")[:200],
        size=len(data), message=message.strip()[:500], styles=info["styles"], placeholders=info["placeholders"],
        variables=docx_vars.package_vars(data, [*(vs[0].variables if vs else []), *(variables or [])]), created_by=ctx.user.id)


@router.get("/base-documents", response_model=list[BaseDocOut])
def list_base_documents(ctx: Ctx = V):
    use = _usage(ctx)
    return sorted((_out(ctx, b, use) for b in ctx.repo(BaseDocument).list()), key=lambda b: b.name.lower())


@router.post("/base-documents", status_code=201, response_model=BaseDocOut)
def create_base_document(name: str = Form(min_length=1, max_length=100), file: UploadFile | None = File(default=None),
                         ctx: Ctx = A):
    """Without `file`, the bundled default base document is created."""
    data, fn = (_read(file), file.filename) if file else (base_docs.default_docx(), "default.docx")
    bd = add_base(ctx, name)
    _store(ctx, bd, data, fn, "")
    ctx.db.commit()
    return _out(ctx, bd)


@router.get("/base-documents/{id}")
def get_base_document(id: UUID, ctx: Ctx = V):
    bd = _get(ctx, id)
    vs = _versions(ctx, bd)
    return dict(**_out(ctx, bd).model_dump(), styles=vs[0].styles)


@router.patch("/base-documents/{id}", response_model=BaseDocOut)
def rename_base_document(id: UUID, body: BaseDocRename, ctx: Ctx = A):
    bd = _get(ctx, id)
    bd.name = new_base(ctx, body.name, exclude=bd.id)
    try:
        ctx.db.commit()
    except IntegrityError:
        ctx.db.rollback()
        raise HTTPException(409, "a base document with this name already exists")
    return _out(ctx, bd)


@router.get("/base-documents/{id}/usage")
def usage(id: UUID, ctx: Ctx = V):
    """Documents pinning this base document and the number of exports rendered on it."""
    bd = _get(ctx, id)
    n = ctx.repo(Export).list(Export.pins["base"]["id"].astext == str(bd.id))
    return dict(documents=_usage(ctx).get(str(bd.id), []), exports=len(n))


@router.delete("/base-documents/{id}", status_code=204)
def delete_base_document(id: UUID, ctx: Ctx = A):
    """Refused (409 + list) while a committed document or a draft pins it. Files leave _base.git in a new commit;
    history stays, so exports already rendered on it can still be re-rendered from their pinned sha."""
    bd = _get(ctx, id)
    ctx.db.execute(text("select pg_advisory_xact_lock(hashtext(:k))"), {"k": f"base:{ctx.org_id}"})
    used = _usage(ctx).get(str(bd.id), [])
    if used:
        raise HTTPException(409, detail=dict(message=f"{len({u['document_id'] for u in used})} document(s) still use this base document", error="in_use",
                                             documents=[{**u, "document_id": str(u["document_id"]), "folder_id": str(u["folder_id"])} for u in used]))
    _vs = _versions(ctx, bd)
    ctx.repo(BaseDocument).delete(bd)
    ctx.db.flush()
    repo = git.base_repo_path(ctx.org_id)
    vcs.drop_names(ctx, vcs.BASE_SCOPE, (v.sha for v in _vs))
    if repo.exists():
        try:
            git.commit(repo, re.sub(r"[<>\n]", "", ctx.user.name).strip() or "user", ctx.user.email,
                       f"Delete base document\n\nBase-Document-Id: {bd.id}\n", {}, deletes=[f"base/{bd.id}.docx"])
        except git.GitError as e:
            ctx.db.rollback()
            raise HTTPException(409, f"git commit failed: {e}")
    ctx.db.commit()
    return Response(status_code=204)


def _head_docx(ctx: Ctx, bd: BaseDocument) -> tuple[BaseDocVersion, bytes]:
    v = _versions(ctx, bd)[0]
    data = git.show(git.base_repo_path(ctx.org_id), v.sha, f"base/{bd.id}.docx")
    if data is None:
        raise HTTPException(404, "version not found")
    return v, data


def _units(data: bytes) -> tuple[list, dict]:
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        body = docx_vars.parse(z.read("word/document.xml"))
    hf = docx_vars.hf_roots(data)
    return [("cover", body, True), *((("header" if "header" in n else "footer"), r, True) for n, r in hf.items())], \
        {"word/document.xml": body, **hf}


@router.put("/base-documents/{id}/variables")
def put_variables(id: UUID, body: Variables, ctx: Ctx = A):
    """Edit type/default/columns of the variables of the current version (names come from the file itself)."""
    v = _versions(ctx, _get(ctx, id))[0]
    known = {d["name"] for d in v.variables}
    v.variables = [d.model_dump() for d in body.declarations if d.name in known]
    ctx.db.commit()
    return v.variables


@router.get("/base-documents/{id}/find-variables")
def find_variables(id: UUID, ctx: Ctx = A):
    """Candidate per-document values in the current version (cover text, header/footer, fields, content controls)."""
    return docx_vars.scan(_units(_head_docx(ctx, _get(ctx, id))[1])[0])


@router.post("/base-documents/{id}/find-variables", status_code=201, response_model=BaseDocVersionOut)
def apply_variables(id: UUID, body: VarPicks, ctx: Ctx = A):
    """New version with the chosen values replaced by `{{ name }}`; detected values become the defaults."""
    bd = _get(ctx, id)
    v, data = _head_docx(ctx, bd)
    units, parts = _units(data)
    if not docx_vars.apply(units, {i.value: i.name for i in body.items}):
        raise HTTPException(422, "none of the values were found")
    nv = _store(ctx, bd, docx_vars.write_parts(data, parts), v.filename, "Variables from detected values",
                docx_vars.picks_decls(body.items))
    ctx.db.commit()
    return nv


@router.post("/base-documents/{id}/versions", status_code=201, response_model=BaseDocVersionOut)
def new_version(id: UUID, file: UploadFile, message: str = Form(default="", max_length=500), ctx: Ctx = A):
    v = _store(ctx, _get(ctx, id), _read(file), file.filename, message)
    ctx.db.commit()
    return v


@router.get("/base-documents/{id}/versions", response_model=list[BaseDocVersionOut])
def versions(id: UUID, ctx: Ctx = V):
    return named(ctx, _versions(ctx, _get(ctx, id)))


@router.patch("/base-documents/{id}/versions/{sha}", response_model=BaseDocVersionOut)
@router.delete("/base-documents/{id}/versions/{sha}", response_model=BaseDocVersionOut)
def rename_version(id: UUID, sha: str = Path(pattern=r"^[0-9a-f]{40}$"), body: CommitRename | None = None, ctx: Ctx = A):
    """Display-name override of a version's commit (git untouched). Empty/null name or DELETE = reset."""
    bd = _get(ctx, id)
    v = ctx.repo(BaseDocVersion).first(BaseDocVersion.base_document_id == bd.id, BaseDocVersion.sha == sha)
    try:
        if not v:
            raise git.NotFound(sha)
        git.resolve(git.base_repo_path(ctx.org_id), sha)
    except (git.NotFound, git.GitError):
        raise HTTPException(404, "version not found")
    vcs.rename(ctx, vcs.BASE_SCOPE, sha, body and body.name, v.message)
    return named(ctx, [v])[0]


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
