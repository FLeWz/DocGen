from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select

from ..db import set_ctx
from .. import files
from ..git import repo as git
from ..deps import Ctx, org_ctx, require, user_ctx
from .. import styles
from ..models import Membership, Organization, Role, Style
from ..schemas import OrgCreateIn, OrgOut, SwitchIn
from ..scoping import OrgRepo
from uuid import uuid4

router = APIRouter(prefix="/orgs", tags=["orgs"])


def _my_orgs(ctx: Ctx):
    # user-scoped by design: RLS exposes a user's own memberships and those orgs only
    q = (select(Organization, Membership.role).join(Membership, Membership.org_id == Organization.id)
         .where(Membership.user_id == ctx.user.id).order_by(Membership.created_at))
    return ctx.db.execute(q).all()


@router.get("", response_model=list[OrgOut])
def list_mine(ctx: Ctx = Depends(user_ctx)):
    return [OrgOut(id=o.id, name=o.name, role=r) for o, r in _my_orgs(ctx)]


@router.post("", status_code=201, response_model=OrgOut)
def create(body: OrgCreateIn, ctx: Ctx = Depends(user_ctx)):
    org_id = uuid4()
    set_ctx(ctx.db, org_id=org_id)
    ctx.db.add(Organization(id=org_id, name=body.name))
    ctx.db.flush()
    OrgRepo(ctx.db, Membership, org_id).add(user_id=ctx.user.id, role=Role.owner.value)
    styles.seed(OrgRepo(ctx.db, Style, org_id))
    ctx.auth.active_org_id = org_id
    ctx.db.commit()
    return OrgOut(id=org_id, name=body.name, role=Role.owner)


@router.get("/current", response_model=OrgOut)
def current(ctx: Ctx = Depends(org_ctx)):
    return OrgOut(id=ctx.org_id, name=ctx.db.get(Organization, ctx.org_id).name, role=ctx.role)


@router.post("/switch", response_model=OrgOut)
def switch(body: SwitchIn, ctx: Ctx = Depends(user_ctx)):
    for o, r in _my_orgs(ctx):
        if o.id == body.org_id:
            ctx.auth.active_org_id = o.id
            ctx.db.commit()
            return OrgOut(id=o.id, name=o.name, role=r)
    raise HTTPException(404, "organization not found")


@router.delete("/current", status_code=204)
def delete_current(ctx: Ctx = Depends(require(Role.owner))):
    org_id = ctx.org_id
    ctx.db.delete(ctx.db.get(Organization, org_id))  # DB cascades memberships/invites/folders, nulls sessions
    ctx.db.commit()
    git.remove_org(org_id)
    files.remove_org(org_id)
