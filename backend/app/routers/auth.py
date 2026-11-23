from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import config
from ..db import get_db, set_ctx
from ..deps import SESSION_COOKIE, Ctx, optional_ctx, user_ctx
from .. import styles
from ..models import AuthSession, Membership, Organization, Role, Style, User
from ..schemas import LoginIn, MeOut, OrgOut, SignupIn
from ..scoping import OrgRepo
from ..security import DUMMY_HASH, hash_pw, make_csrf, new_token, sha, valid_csrf, verify_pw

router = APIRouter(prefix="/auth", tags=["auth"])
CSRF_COOKIE = "docgen_csrf"


def start_session(db: Session, resp: Response, user: User, org_id: UUID | None) -> None:
    tok = new_token()
    exp = datetime.now(timezone.utc) + timedelta(days=config.SESSION_DAYS)
    db.add(AuthSession(token_hash=sha(tok), user_id=user.id, active_org_id=org_id, expires_at=exp))
    resp.set_cookie(SESSION_COOKIE, tok, httponly=True, samesite="lax", secure=config.COOKIE_SECURE,
                    max_age=config.SESSION_DAYS * 86400)


def me_out(ctx: Ctx) -> MeOut:
    org = None
    if ctx.org_id:
        org = OrgOut(id=ctx.org_id, name=ctx.db.get(Organization, ctx.org_id).name, role=ctx.role)
    return MeOut(user=ctx.user, org=org)


@router.get("/csrf")
def csrf(request: Request, response: Response):
    tok = request.cookies.get(CSRF_COOKIE)
    if not valid_csrf(tok):
        tok = make_csrf()
    # readable by JS on purpose (double-submit): the SPA copies it into X-CSRF-Token
    response.set_cookie(CSRF_COOKIE, tok, httponly=False, samesite="lax", secure=config.COOKIE_SECURE)
    return {"csrf_token": tok}


@router.post("/signup", status_code=201, response_model=MeOut)
def signup(body: SignupIn, response: Response, db: Session = Depends(get_db)):
    email = body.email.lower()
    org_id = uuid4()
    user = User(email=email, name=body.name, password_hash=hash_pw(body.password))
    try:
        db.add(user)
        db.flush()
        set_ctx(db, user_id=user.id, org_id=org_id)
        db.add(Organization(id=org_id, name=body.org_name))
        db.flush()
        OrgRepo(db, Membership, org_id).add(user_id=user.id, role=Role.owner.value)
        styles.seed(OrgRepo(db, Style, org_id))
        start_session(db, response, user, org_id)
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "email already registered")
    ctx = Ctx(db, user, None, org_id, Role.owner)
    return me_out(ctx)


@router.post("/login", response_model=MeOut)
def login(body: LoginIn, response: Response, db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.email == body.email.lower()))
    ok = verify_pw(user.password_hash if user else DUMMY_HASH, body.password)
    if not user or not ok:
        raise HTTPException(401, "invalid email or password")
    set_ctx(db, user_id=user.id)
    # user-scoped (not org-scoped) on purpose: RLS lets a user see their own memberships
    m = db.scalar(select(Membership).where(Membership.user_id == user.id).order_by(Membership.created_at))
    org_id = m.org_id if m else None
    set_ctx(db, org_id=org_id)
    start_session(db, response, user, org_id)
    db.commit()
    return me_out(Ctx(db, user, None, org_id, Role(m.role) if m else None))


@router.post("/logout", status_code=204)
def logout(request: Request, response: Response, db: Session = Depends(get_db)):
    tok = request.cookies.get(SESSION_COOKIE)
    if tok:
        db.execute(delete(AuthSession).where(AuthSession.token_hash == sha(tok)))
        db.commit()
    response.delete_cookie(SESSION_COOKIE)


@router.get("/me", response_model=MeOut)
def me(ctx: Ctx = Depends(user_ctx)):
    return me_out(ctx)
