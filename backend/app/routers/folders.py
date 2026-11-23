from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException

from .. import vcs
from ..deps import Ctx, require
from ..git import repo as git
from ..models import Folder, Role
from ..schemas import FolderCreateIn, FolderOut, FolderPatchIn, TagIn

router = APIRouter(tags=["folders"])


def _descendants(ctx: Ctx, root: Folder) -> list[Folder]:
    allf, out, todo = ctx.repo(Folder).list(), [root], [root.id]
    while todo:
        kids = [f for f in allf if f.parent_id in todo]
        out += kids
        todo = [k.id for k in kids]
    return out


def _check_parent(ctx: Ctx, parent_id, name: str, self_f: Folder | None = None):
    if parent_id and not ctx.repo(Folder).get(parent_id):
        raise HTTPException(404, "parent folder not found")
    if self_f and parent_id in {f.id for f in _descendants(ctx, self_f)}:
        raise HTTPException(400, "cannot move a folder into itself")
    dup = ctx.repo(Folder).first(Folder.parent_id == parent_id if parent_id else Folder.parent_id.is_(None),
                                 Folder.name == name, *([Folder.id != self_f.id] if self_f else []))
    if dup:
        raise HTTPException(409, "a folder with this name already exists here")


@router.get("/folders", response_model=list[FolderOut])
def list_folders(ctx: Ctx = Depends(require(Role.viewer))):
    return ctx.repo(Folder).list()  # flat; the client builds the tree


@router.post("/folders", status_code=201, response_model=FolderOut)
def create_folder(body: FolderCreateIn, ctx: Ctx = Depends(require(Role.editor))):
    _check_parent(ctx, body.parent_id, body.name)
    f = ctx.repo(Folder).add(name=body.name, parent_id=body.parent_id)
    git.init(vcs.repo_of(ctx, f.id))
    ctx.db.commit()
    return f


@router.patch("/folders/{folder_id}", response_model=FolderOut)
def patch_folder(folder_id: UUID, body: FolderPatchIn, ctx: Ctx = Depends(require(Role.editor))):
    f = vcs.get_folder(ctx, folder_id)
    name = body.name or f.name
    parent = body.parent_id if "parent_id" in body.model_fields_set else f.parent_id
    _check_parent(ctx, parent, name, f)
    f.name, f.parent_id = name, parent  # repo path is id-based, so moves never touch git
    ctx.db.commit()
    return f


@router.delete("/folders/{folder_id}", status_code=204)
def delete_folder(folder_id: UUID, ctx: Ctx = Depends(require(Role.admin))):
    doomed = _descendants(ctx, vcs.get_folder(ctx, folder_id))
    paths = [vcs.repo_of(ctx, f.id) for f in doomed]
    ctx.repo(Folder).delete(doomed[0])  # DB cascades children, snippets, drafts
    ctx.db.commit()
    for p in paths:
        git.remove(p)


@router.get("/folders/{folder_id}/history")
def folder_history(folder_id: UUID, ctx: Ctx = Depends(require(Role.viewer))):
    repo = vcs.repo_of(ctx, vcs.get_folder(ctx, folder_id).id)
    tm = vcs.tag_map(repo)
    return [vcs.commit_out(r, tm) for r in git.log(repo)]


@router.get("/folders/{folder_id}/tags")
def list_tags(folder_id: UUID, ctx: Ctx = Depends(require(Role.viewer))):
    return git.tags(vcs.repo_of(ctx, vcs.get_folder(ctx, folder_id).id))


@router.post("/folders/{folder_id}/tags", status_code=201)
def create_tag(folder_id: UUID, body: TagIn, ctx: Ctx = Depends(require(Role.editor))):
    repo = vcs.repo_of(ctx, vcs.get_folder(ctx, folder_id).id)
    try:
        sha = git.resolve(repo, body.sha) if body.sha else git.head(repo)
        if not sha:
            raise git.NotFound("no commits yet")
    except git.NotFound:
        raise HTTPException(404, "commit not found")
    if body.name in {t["name"] for t in git.tags(repo)}:
        raise HTTPException(409, "tag already exists")
    git.tag_add(repo, body.name, sha)
    return dict(name=body.name, sha=sha)


@router.delete("/folders/{folder_id}/tags/{name}", status_code=204)
def delete_tag(folder_id: UUID, name: str, ctx: Ctx = Depends(require(Role.editor))):
    try:
        git.tag_delete(vcs.repo_of(ctx, vcs.get_folder(ctx, folder_id).id), name)
    except git.NotFound:
        raise HTTPException(404, "tag not found")
