from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from .. import export, files
from ..deps import Ctx, require
from ..models import Document, Export, Role
from ..schemas import ExportOut

router = APIRouter(tags=["exports"])


def _get(ctx: Ctx, id: UUID) -> Export:
    e = ctx.repo(Export).get(id)
    if not e:
        raise HTTPException(404, "export not found")
    export.expire_stale(ctx, e)
    return e


@router.get("/exports/{export_id}", response_model=ExportOut)
def status(export_id: UUID, ctx: Ctx = Depends(require(Role.viewer))):
    return _get(ctx, export_id)


@router.get("/exports/{export_id}/download")
def download(export_id: UUID, ctx: Ctx = Depends(require(Role.viewer))):
    e = _get(ctx, export_id)
    if e.status != "done":
        raise HTTPException(409, f"export is {e.status}")
    d = ctx.repo(Document).get(e.document_id)
    return FileResponse(files.export_path(ctx.org_id, e.id, e.format), media_type=export.MIME[e.format],
                        filename=f"{d.slug if d else 'document'}.{e.format}",
                        headers={"X-Content-Type-Options": "nosniff"})
