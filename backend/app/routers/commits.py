import re
from collections import Counter
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import IntegrityError

from .. import vcs
from ..deps import Ctx, require
from ..git import content as C, repo as git
from ..files import image_path
from ..models import Draft, Folder, Image, Role, Snippet
from ..schemas import CommitIn

router = APIRouter(tags=["commits"])


@router.get("/folders/{folder_id}/changes")
def changes(folder_id: UUID, ctx: Ctx = Depends(require(Role.viewer))):
    f = vcs.get_folder(ctx, folder_id)
    out = []
    for s in ctx.repo(Snippet).list(Snippet.folder_id == f.id):
        d = vcs.current_draft(ctx, s)
        if not d or not vcs.dirty(ctx, s, d):
            continue
        conflict = d.base_sha != s.head_sha
        h = vcs.head_version(ctx, s)
        out.append(dict(snippet_id=s.id, slug=d.slug, title=d.title, status="modified" if h else "new",
                        conflict=conflict, **C.diff(s.id, h, vcs.dver(d)),
                        three_way=vcs.conflict_info(ctx, s, d) if conflict else None))
    ctx.db.commit()
    return sorted(out, key=lambda c: c["title"].lower())


@router.post("/folders/{folder_id}/commit", status_code=201)
def commit(folder_id: UUID, body: CommitIn, ctx: Ctx = Depends(require(Role.editor))):
    f = vcs.get_folder(ctx, folder_id)
    ctx.db.scalar(ctx.repo(Folder).query().where(Folder.id == f.id).with_for_update())  # serialize commits
    picked = []
    for sid in dict.fromkeys(body.snippet_ids):
        s = ctx.repo(Snippet).get(sid)
        if not s or s.folder_id != f.id:
            raise HTTPException(404, "snippet not found")
        d = vcs.current_draft(ctx, s)
        if not d or not vcs.dirty(ctx, s, d):
            raise HTTPException(400, f"no changes to commit for snippet {sid}")
        picked.append((s, d))
    conflicts = [vcs.conflict_info(ctx, s, d) for s, d in picked if d.base_sha != s.head_sha]
    if conflicts:
        raise HTTPException(409, dict(error="conflict", conflicts=conflicts))

    repo = vcs.repo_of(ctx, f.id)
    writes, moves = {}, []
    for s, d in picked:
        if s.head_sha and d.slug != s.slug:
            moves.append((f"snippets/{s.slug}", f"snippets/{d.slug}"))
        for name, text in C.files(s.id, vcs.dver(d)).items():
            writes[f"snippets/{d.slug}/{name}"] = text.encode()
        for iid in C.image_ids(d.content):  # embed referenced images so the repo is self-contained
            try:
                img = ctx.repo(Image).get(UUID(iid))
            except ValueError:
                img = None
            if not img:
                raise HTTPException(422, f"unknown image {iid}")
            writes[f"snippets/{d.slug}/assets/{img.id}.{img.ext}"] = image_path(ctx.org_id, img.id, img.ext).read_bytes()
    msg = re.sub(r"^Snippet-Id:.*$", "", body.message, flags=re.M).strip()  # trailers are server-written only
    if not msg:
        raise HTTPException(422, "empty message")
    msg += "\n\n" + "".join(f"Snippet-Id: {s.id}\n" for s, _ in picked)
    try:  # DB first (slug uniqueness), git second, DB commit last: a git failure rolls the index back
        for s, d in picked:
            s.slug, s.title = d.slug, d.title
        ctx.db.flush()
    except IntegrityError:
        ctx.db.rollback()
        raise HTTPException(409, "slug already used in this folder")
    try:
        sha = git.commit(repo, re.sub(r"[<>\n]", "", ctx.user.name).strip() or "user", ctx.user.email,
                         msg, writes, moves)
    except git.GitError as e:
        ctx.db.rollback()
        raise HTTPException(409, f"git commit failed: {e}")
    for s, d in picked:
        s.head_sha = sha
        ctx.repo(Draft).delete(d)
    ctx.db.commit()
    return dict(sha=sha, snippet_ids=[s.id for s, _ in picked])
