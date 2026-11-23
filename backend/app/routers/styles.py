from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import IntegrityError

from .. import docs, styles
from ..deps import Ctx, require
from ..models import BaseDocument, Role, Style
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
