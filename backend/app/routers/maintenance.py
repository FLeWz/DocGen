import json
import re
import time
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query

from .. import vcs
from ..deps import Ctx, require
from ..files import ROOT, image_path
from ..git import repo as git
from ..models import Draft, Export, Folder, Image, Import, Role

router = APIRouter(tags=["maintenance"])
UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
GRACE = timedelta(hours=24)  # fresh uploads may not be saved into a draft yet


@router.post("/maintenance/cleanup")
def cleanup(dry_run: bool = Query(default=False), ctx: Ctx = Depends(require(Role.admin))):
    """Delete (1) images no draft, import or git commit of this org refers to and (2) files under the org's files
    dir that have no DB row. Anything younger than 24 h (images) / 1 h (files) is kept."""
    now = datetime.now(timezone.utc)
    used = set()
    for row in [*ctx.repo(Draft).list(), *ctx.repo(Import).list()]:
        used |= set(UUID_RE.findall(json.dumps(row.content)))
    for f in ctx.repo(Folder).list():  # committed assets: history/restore still shows these images
        used |= set(UUID_RE.findall("\n".join(git.ever_paths(vcs.repo_of(ctx, f.id)))))
    images = ctx.repo(Image).list()
    dead = [i for i in images if str(i.id) not in used and now - i.created_at > GRACE]
    known = {str(r.id) for m in (Import, Export) for r in ctx.repo(m).list()} | {str(i.id) for i in images}
    paths = [image_path(ctx.org_id, i.id, i.ext) for i in dead]
    orphans = []
    for sub in ("images", "imports", "exports"):
        d = ROOT / str(ctx.org_id) / sub
        for p in d.glob("*") if d.is_dir() else []:
            if p.stem not in known and time.time() - p.stat().st_mtime > 3600:
                orphans.append(p)
    if not dry_run:
        for i in dead:
            ctx.repo(Image).delete(i)
        ctx.db.commit()
        for p in paths + orphans:
            p.unlink(missing_ok=True)
    return dict(dry_run=dry_run, images_deleted=len(dead), orphan_files_deleted=len(orphans))
