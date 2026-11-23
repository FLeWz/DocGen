from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select, update

from ..deps import Ctx, org_ctx, require
from ..models import AuthSession, Membership, Role, User
from ..schemas import MemberOut, RoleIn

router = APIRouter(prefix="/members", tags=["members"])


def _get(ctx: Ctx, user_id: UUID) -> Membership:
    m = ctx.repo(Membership).first(Membership.user_id == user_id)
    if not m:
        raise HTTPException(404, "member not found")
    return m


def _guard(ctx: Ctx, target: Membership, new_role: Role | None):
    """Only owners touch owners / grant owner; the last owner can't go."""
    if ctx.role != Role.owner and Role.owner.value in (target.role, new_role and new_role.value):
        raise HTTPException(403, "only an owner can do that")
    if target.role == Role.owner.value and new_role != Role.owner:
        owners = ctx.db.scalar(select(func.count()).select_from(Membership).where(
            Membership.org_id == ctx.org_id, Membership.role == "owner"))
        if owners <= 1:
            raise HTTPException(409, "organization needs at least one owner")


@router.get("", response_model=list[MemberOut])
def list_members(ctx: Ctx = Depends(org_ctx)):
    q = ctx.repo(Membership).query().add_columns(User).join(User, User.id == Membership.user_id)
    return [MemberOut(user_id=u.id, email=u.email, name=u.name, role=m.role, joined_at=m.created_at)
            for m, u in ctx.db.execute(q.order_by(Membership.created_at)).all()]


@router.patch("/{user_id}", response_model=MemberOut)
def change_role(user_id: UUID, body: RoleIn, ctx: Ctx = Depends(require(Role.admin))):
    m = _get(ctx, user_id)
    _guard(ctx, m, body.role)
    m.role = body.role.value
    ctx.db.commit()
    u = ctx.db.get(User, user_id)
    return MemberOut(user_id=u.id, email=u.email, name=u.name, role=m.role, joined_at=m.created_at)


@router.delete("/{user_id}", status_code=204)
def remove(user_id: UUID, ctx: Ctx = Depends(org_ctx)):
    m = _get(ctx, user_id)
    if user_id != ctx.user.id:  # anyone may leave; removing others needs admin
        require(Role.admin)(ctx)
    _guard(ctx, m, None)
    ctx.repo(Membership).delete(m)
    ctx.db.execute(update(AuthSession).where(AuthSession.user_id == user_id,
                                             AuthSession.active_org_id == ctx.org_id)
                   .values(active_org_id=None))
    ctx.db.commit()
