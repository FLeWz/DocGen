from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from fastapi.responses import JSONResponse
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import config, locks, oidc, throttle
from ..db import get_db, set_ctx
from ..deps import SESSION_COOKIE, Ctx, optional_ctx, user_ctx
from .. import styles
from ..models import AuthSession, Membership, Organization, Role, Style, User
from ..schemas import LoginIn, MeOut, OrgOut, SignupIn
from ..scoping import OrgRepo
from ..passwords import check_policy, normalize_email, normalize_password
from ..security import DUMMY_HASH, hash_pw, make_csrf, new_token, sha, valid_csrf, verify_pw

router = APIRouter(prefix="/auth", tags=["auth"])
CSRF_COOKIE = "docgen_csrf"


def start_session(db: Session, resp: Response, user: User, org_id: UUID | None, id_token: str | None = None) -> None:
    tok = new_token()
    exp = datetime.now(timezone.utc) + timedelta(days=config.SESSION_DAYS)
    db.add(AuthSession(token_hash=sha(tok), user_id=user.id, active_org_id=org_id, expires_at=exp, id_token=id_token))
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


def register(db: Session, email: str, name: str, password: str, org_name: str,
             resp: Response | None = None) -> tuple[User, UUID]:
    """New user + their own org (owner). Shared by signup (when allowed) and `app.manage create-user`."""
    org_id = uuid4()
    user = User(email=normalize_email(email), name=name, password_hash=hash_pw(check_policy(normalize_password(password))) if password is not None else None)
    try:
        db.add(user)
        db.flush()
        set_ctx(db, user_id=user.id, org_id=org_id)
        db.add(Organization(id=org_id, name=org_name))
        db.flush()
        OrgRepo(db, Membership, org_id).add(user_id=user.id, role=Role.owner.value)
        styles.seed(OrgRepo(db, Style, org_id))
        if resp is not None:
            start_session(db, resp, user, org_id)
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "email already registered")
    return user, org_id


def local_only() -> None:
    if config.AUTH_MODE == "oidc":
        raise HTTPException(404, "password login is disabled: this server signs in with OpenID Connect (AUTH_MODE=oidc)")


@router.get("/config")
def auth_config():
    """Public: what the login screen needs."""
    return {"mode": config.AUTH_MODE, "provider_name": config.OIDC_PROVIDER_NAME if config.AUTH_MODE == "oidc" else None,
            "allow_signup": config.ALLOW_SIGNUP and config.AUTH_MODE == "local"}


@router.post("/signup", status_code=201, response_model=MeOut, dependencies=[Depends(local_only)])
def signup(body: SignupIn, response: Response, db: Session = Depends(get_db)):
    if not config.ALLOW_SIGNUP:
        raise HTTPException(404, "sign-up is disabled; ask for an invite")
    user, org_id = register(db, body.email, body.name, body.password, body.org_name, response)
    ctx = Ctx(db, user, None, org_id, Role.owner)
    return me_out(ctx)


@router.post("/login", response_model=MeOut, dependencies=[Depends(local_only)])
def login(body: LoginIn, request: Request, response: Response, db: Session = Depends(get_db)):
    email, ip = body.email.lower(), request.client.host if request.client else "?"
    throttle.check(ip, email)
    user = db.scalar(select(User).where(User.email == email))
    ok = verify_pw(user.password_hash if user and user.password_hash else DUMMY_HASH, body.password)
    if not user or not user.password_hash or not ok:
        throttle.failed(ip, email)
        raise HTTPException(401, "invalid email or password")
    throttle.succeeded(ip, email)
    set_ctx(db, user_id=user.id)
    # user-scoped (not org-scoped) on purpose: RLS lets a user see their own memberships
    m = db.scalar(select(Membership).where(Membership.user_id == user.id).order_by(Membership.created_at))
    org_id = m.org_id if m else None
    set_ctx(db, org_id=org_id)
    start_session(db, response, user, org_id)
    db.commit()
    return me_out(Ctx(db, user, None, org_id, Role(m.role) if m else None))


@router.post("/logout")
def logout(request: Request, db: Session = Depends(get_db)):
    """local: 204. oidc: 200 {redirect} = the IdP's end-session URL (RP-initiated logout) or null."""
    tok = request.cookies.get(SESSION_COOKIE)
    id_token = None
    if tok:
        s = db.scalar(select(AuthSession).where(AuthSession.token_hash == sha(tok)))
        id_token = s.id_token if s else None
        if s and s.active_org_id:  # a logged-out user holds no edit locks
            set_ctx(db, user_id=s.user_id, org_id=s.active_org_id)
            locks.release_all(db, s.active_org_id, db.get(User, s.user_id))
        db.execute(delete(AuthSession).where(AuthSession.token_hash == sha(tok)))
        db.commit()
    resp = (JSONResponse({"redirect": oidc.end_session_url(id_token)}) if config.AUTH_MODE == "oidc"
            else Response(status_code=204))
    resp.delete_cookie(SESSION_COOKIE)
    return resp


@router.get("/me", response_model=MeOut)
def me(ctx: Ctx = Depends(user_ctx)):
    return me_out(ctx)
