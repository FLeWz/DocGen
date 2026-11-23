import io
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from fastapi.responses import FileResponse
from PIL import Image as PILImage, UnidentifiedImageError

from .. import config
from ..deps import Ctx, require
from ..files import FORMATS, image_path
from ..models import Image, Role
from ..schemas import ImageOut

router = APIRouter(tags=["images"])
PILImage.MAX_IMAGE_PIXELS = 50_000_000  # decompression-bomb guard (Pillow raises above 2x this)


@router.post("/images", status_code=201, response_model=ImageOut)
def upload(file: UploadFile, ctx: Ctx = Depends(require(Role.editor))):
    data = file.file.read(config.MAX_IMAGE_BYTES + 1)
    if len(data) > config.MAX_IMAGE_BYTES:
        raise HTTPException(413, "image too large")
    try:  # trust the bytes, not the filename or Content-Type
        with PILImage.open(io.BytesIO(data)) as im:
            fmt, (w, h) = im.format, im.size
            im.verify()
    except (UnidentifiedImageError, OSError, PILImage.DecompressionBombError, SyntaxError):
        raise HTTPException(415, "not a valid image")
    if fmt not in FORMATS:
        raise HTTPException(415, "unsupported image type (png, jpeg, gif, webp)")
    ext, mime = FORMATS[fmt]
    img = ctx.repo(Image).add(id=uuid4(), ext=ext, mime=mime, size=len(data), width=w, height=h,
                              created_by=ctx.user.id)
    p = image_path(ctx.org_id, img.id, ext)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    ctx.db.commit()
    return img


@router.get("/images/{image_id}")
def get_image(image_id: UUID, ctx: Ctx = Depends(require(Role.viewer))):
    img = ctx.repo(Image).get(image_id)
    if not img:
        raise HTTPException(404, "image not found")
    return FileResponse(image_path(ctx.org_id, img.id, img.ext), media_type=img.mime,
                        headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "private, max-age=3600"})
