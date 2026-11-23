from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID

from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import get_db, set_ctx
from .models import RANK, AuthSession, Membership, Role, User
from .scoping import OrgRepo
from .security import sha

SESSION_COOKIE = "docgen_session"


@dataclass
class Ctx:
    db: Session
    user: User
    auth: AuthSession
    org_id: UUID | None
    role: Role | None

    def repo(self, model):
        return OrgRepo(self.db, model, self.org_id)


def optional_ctx(request: Request, db: Session = Depends(get_db)) -> Ctx | None:
    tok = request.cookies.get(SESSION_COOKIE)
    if not tok:
        return None
    auth = db.scalar(select(AuthSession).where(
        AuthSession.token_hash == sha(tok), AuthSession.expires_at > datetime.now(timezone.utc)))
    if not auth:
        return None
    user = db.get(User, auth.user_id)
    set_ctx(db, user_id=user.id, org_id=auth.active_org_id)
    org_id = role = None
    if auth.active_org_id:
        m = OrgRepo(db, Membership, auth.active_org_id).first(Membership.user_id == user.id)
        if m:  # removed members silently lose the org
            org_id, role = auth.active_org_id, Role(m.role)
        else:
            set_ctx(db, org_id=None)
    return Ctx(db, user, auth, org_id, role)


def user_ctx(ctx: Ctx | None = Depends(optional_ctx)) -> Ctx:
    if not ctx:
        raise HTTPException(401, "not authenticated")
    return ctx


def org_ctx(ctx: Ctx = Depends(user_ctx)) -> Ctx:
    if not ctx.org_id:
        raise HTTPException(400, "no active organization")
    return ctx


def require(min_role: Role):
    def dep(ctx: Ctx = Depends(org_ctx)) -> Ctx:
        if RANK[ctx.role] < RANK[min_role]:
            raise HTTPException(403, f"requires role {min_role.value}")
        return ctx
    return dep
