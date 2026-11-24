from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.exc import IntegrityError

from .. import docs, docx_style, styles
from ..deps import Ctx, require
from ..files import import_path
from ..git import repo as git
from ..models import BaseDocument, Import, Role, Style
from ..schemas import StyleIn, StyleOut, StylePatchIn

router = APIRouter(tags=["styles"])


def _get(ctx: Ctx, id: UUID) -> Style:
    s = ctx.repo(Style).get(id)
    if not s:
        raise HTTPException(404, "style not found")
    return s


@router.get("/styles", response_model=list[StyleOut])
def list_styles(base_doc: UUID | None = None, ctx: Ctx = Depends(require(Role.viewer))):
    bd = None
    if base_doc:  # ?base_doc=<id>: the base document's styles (head version) override the org set
        if not ctx.repo(BaseDocument).get(base_doc):
            raise HTTPException(404, "base document not found")
        bd = docs.base_head(ctx, ctx.repo(BaseDocument).get(base_doc))
    return sorted(styles.resolve(ctx.repo(Style), bd), key=lambda s: (s.kind, s.level or 0, s.name))


@router.get("/styles/css")
def styles_css(base_doc: UUID | None = None, import_id: UUID | None = Query(default=None, alias="import"),
               ctx: Ctx = Depends(require(Role.viewer))):
    """Effective look as CSS (+ page size, header/footer, media) of an imported DOCX, a base document (head version), or
    the org style set. The editor and the previews inject it so snippets look like the Word page."""
    if import_id:
        imp = ctx.repo(Import).get(import_id)
        if not imp:
            raise HTTPException(404, "import not found")
        data = import_path(ctx.org_id, imp.id).read_bytes()
    elif base_doc:
        bd = ctx.repo(BaseDocument).get(base_doc)
        if not bd:
            raise HTTPException(404, "base document not found")
        v = docs.base_head(ctx, bd)
        data = git.show(git.base_repo_path(ctx.org_id), v.sha, f"base/{bd.id}.docx")
        if data is None:
            raise HTTPException(404, "base document file not found")
    else:
        return docx_style.org_look(ctx.repo(Style).list())
    try:
        out = docx_style.look(data)
        if base_doc:  # org styles the base document lacks (Quote, Code...) still get a look
            extra = docx_style.org_look(ctx.repo(Style).list(), skip={x["style_id"] for x in out["styles"]})
            out["css"] = extra["css"] + "\n" + out["css"]
        return out
    except Exception as e:  # a package that validated at upload but has odd styles/theme: fall back, never 500 the editor
        raise HTTPException(422, f"cannot read styles: {type(e).__name__}")


@router.post("/styles", status_code=201, response_model=StyleOut)
def create_style(body: StyleIn, ctx: Ctx = Depends(require(Role.admin))):
    try:
        s = ctx.repo(Style).add(**body.model_dump())
        ctx.db.commit()
    except IntegrityError:
        ctx.db.rollback()
        raise HTTPException(409, "style_id already exists")
    return s


@router.patch("/styles/{style_pk}", response_model=StyleOut)
def patch_style(style_pk: UUID, body: StylePatchIn, ctx: Ctx = Depends(require(Role.admin))):
    s = _get(ctx, style_pk)
    for k in body.model_fields_set:  # explicit null clears color/font/size
        setattr(s, k, getattr(body, k))
    if (s.kind == "heading") != (s.level is not None):
        ctx.db.rollback()
        raise HTTPException(422, "level is required for (and only for) heading styles")
    ctx.db.commit()
    return s


@router.delete("/styles/{style_pk}", status_code=204)
def delete_style(style_pk: UUID, ctx: Ctx = Depends(require(Role.admin))):
    s = _get(ctx, style_pk)
    if s.builtin:
        raise HTTPException(409, "built-in styles cannot be deleted")
    ctx.repo(Style).delete(s)  # content still naming it falls back to Normal/HeadingN
    ctx.db.commit()
