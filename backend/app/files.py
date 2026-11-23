"""Uploaded-file storage under FILES_ROOT/{org_id}/images/{image_id}.{ext}; paths come from UUIDs only."""
from pathlib import Path
from uuid import UUID

from . import config

ROOT = Path(config.FILES_ROOT).resolve()
FORMATS = {"PNG": ("png", "image/png"), "JPEG": ("jpg", "image/jpeg"), "GIF": ("gif", "image/gif"),
           "WEBP": ("webp", "image/webp")}  # no SVG: it can carry script


def image_path(org_id, image_id, ext: str) -> Path:
    p = (ROOT / str(UUID(str(org_id))) / "images" / f"{UUID(str(image_id))}.{ext}").resolve()
    assert p.is_relative_to(ROOT)
    return p
