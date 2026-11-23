import re
from collections import Counter
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import IntegrityError

import difflib

from .. import docs, vcs
from ..deps import Ctx, require
from ..git import content as C, repo as git
from ..files import image_path
from ..models import DocDraft, DocRef, Document, Draft, Folder, Image, Role, Snippet
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
    for d in ctx.repo(Document).list(Document.folder_id == f.id):
        dr = docs.draft_of(ctx, d)
        if not dr or not docs.dirty(ctx, d, dr):
            continue
        h = docs.head_version(ctx, d)
        old, new = (docs.dump(d.id, h["title"], h) if h else ""), docs.dump(d.id, dr.title, dr.body)
        text = "".join(difflib.unified_diff(old.splitlines(True), new.splitlines(True), f"a/{docs.path(d.slug)}",
                                            f"b/{docs.path(dr.slug)}"))
        blk = lambda v: dict(content=dict(content=v["blocks"])) if v else None
        out.append(dict(kind="document", snippet_id=None, document_id=d.id, slug=dr.slug, title=dr.title,
                        status="modified" if h else "new", conflict=dr.base_sha != d.head_sha, text=text,
                        summary=C.block_summary(blk(h), blk(dict(blocks=dr.body["blocks"]))), three_way=None))
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
    pdocs = []
    for did in dict.fromkeys(body.document_ids):
        d = ctx.repo(Document).get(did)
        if not d or d.folder_id != f.id:
            raise HTTPException(404, "document not found")
        dr = docs.draft_of(ctx, d)
        if not dr or not docs.dirty(ctx, d, dr):
            raise HTTPException(400, f"no changes to commit for document {did}")
        pdocs.append((d, dr))
    conflicts = [vcs.conflict_info(ctx, s, d) for s, d in picked if d.base_sha != s.head_sha]
    # documents have no 3-way merge: someone else committed first -> discard the draft and redo
    conflicts += [dict(document_id=str(d.id), slug=d.slug, base_sha=dr.base_sha, head_sha=d.head_sha)
                  for d, dr in pdocs if dr.base_sha != d.head_sha]
    if conflicts:
        raise HTTPException(409, dict(error="conflict", conflicts=conflicts))

    repo = vcs.repo_of(ctx, f.id)
    writes, moves, deletes = {}, [], []
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
    for d, dr in pdocs:
        if d.head_sha and dr.slug != d.slug:
            deletes.append(docs.path(d.slug))
        writes[docs.path(dr.slug)] = docs.dump(d.id, dr.title, dr.body).encode()
    msg = vcs.ANY_TRAILER.sub("", body.message).strip()  # trailers are server-written only
    if not msg:
        raise HTTPException(422, "empty message")
    msg += "\n\n" + "".join(f"Snippet-Id: {s.id}\n" for s, _ in picked) + "".join(f"Document-Id: {d.id}\n" for d, _ in pdocs)
    try:  # DB first (slug uniqueness), git second, DB commit last: a git failure rolls the index back
        for s, d in picked:
            s.slug, s.title = d.slug, d.title
        for d, dr in pdocs:
            d.slug, d.title = dr.slug, dr.title
        ctx.db.flush()
    except IntegrityError:
        ctx.db.rollback()
        raise HTTPException(409, "slug already used in this folder")
    try:
        sha = git.commit(repo, re.sub(r"[<>\n]", "", ctx.user.name).strip() or "user", ctx.user.email,
                         msg, writes, moves, deletes)
    except git.GitError as e:
        ctx.db.rollback()
        raise HTTPException(409, f"git commit failed: {e}")
    for s, d in picked:
        s.head_sha = sha
        ctx.repo(Draft).delete(d)
    for d, dr in pdocs:
        d.head_sha = sha
        for b in dr.body["blocks"]:
            if b["type"] == "snippet_ref":
                ctx.repo(DocRef).add(document_id=d.id, commit_sha=sha, snippet_id=UUID(b["snippet_id"]),
                                     folder_id=UUID(b["folder_id"]), pin_type=b["version"]["type"],
                                     pin_value=b["version"]["value"])
        ctx.repo(DocDraft).delete(dr)
    ctx.db.commit()
    return dict(sha=sha, snippet_ids=[s.id for s, _ in picked], document_ids=[d.id for d, _ in pdocs])
