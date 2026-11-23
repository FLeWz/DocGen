"""Uploaded-file storage under FILES_ROOT/{org_id}/images/{image_id}.{ext}; paths come from UUIDs only."""
import shutil
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


def export_path(org_id, export_id, ext: str = "docx") -> Path:
    p = (ROOT / str(UUID(str(org_id))) / "exports" / f"{UUID(str(export_id))}.{ext}").resolve()
    assert p.is_relative_to(ROOT)
    return p


def import_path(org_id, import_id) -> Path:
    p = (ROOT / str(UUID(str(org_id))) / "imports" / f"{UUID(str(import_id))}.docx").resolve()
    assert p.is_relative_to(ROOT)
    return p


def remove_org(org_id) -> None:
    shutil.rmtree(ROOT / str(UUID(str(org_id))), ignore_errors=True)
