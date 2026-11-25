"""OpenID Connect login routes and user provisioning (policy documented in docs/OIDC.md)."""
import logging
from datetime import datetime, timezone
from urllib.parse import quote
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from .. import config, oidc, styles
from ..db import get_db, set_ctx
from ..models import RANK, Identity, Invite, Membership, Organization, Role, Style, User
from ..passwords import normalize_email
from ..scoping import OrgRepo
from .auth import start_session

log = logging.getLogger(__name__)
def ctx(db: Session, **kw) -> None:
    db.flush()  # pending writes must hit the DB under the context they were made in
    set_ctx(db, **kw)


router = APIRouter(prefix="/auth/oidc", tags=["auth"])
STATE_COOKIE = "docgen_oidc"
COOKIE_PATH = "/api/auth/oidc"


def need_oidc() -> None:
    if config.AUTH_MODE != "oidc":
        raise HTTPException(404, "OpenID Connect is not enabled (AUTH_MODE=local)")


def to_login(msg: str) -> RedirectResponse:
    r = RedirectResponse(f"/login?error={quote(msg)}", status_code=302)
    r.delete_cookie(STATE_COOKIE, path=COOKIE_PATH)
    return r


@router.get("/login", dependencies=[Depends(need_oidc)])
def login(return_to: str | None = None):
    try:
        url, sealed = oidc.begin(return_to)
    except oidc.OidcError as e:
        return to_login(str(e))
    r = RedirectResponse(url, status_code=302)
    # lax: the IdP's redirect back is a top-level GET, which lax cookies accompany
    r.set_cookie(STATE_COOKIE, sealed, max_age=oidc.STATE_TTL_S, httponly=True, samesite="lax",
                 secure=config.COOKIE_SECURE, path=COOKIE_PATH)
    return r


def groups_of(claims: dict) -> list[str]:
    v = oidc.dig(claims, config.OIDC_GROUPS_CLAIM) if config.OIDC_GROUPS_CLAIM else None
    return [str(x) for x in v] if isinstance(v, list) else [v] if isinstance(v, str) and v else []


def role_for(groups: list[str]) -> str:
    """Highest mapped role among the user's groups, else the default role."""
    hit = [config.OIDC_ROLE_MAP[g] for g in groups if g in config.OIDC_ROLE_MAP]
    return max(hit, key=lambda r: RANK[Role(r)]) if hit else config.OIDC_DEFAULT_ROLE


def identity_from(claims: dict, access_token: str) -> tuple[str, str, bool]:
    """-> (email, name, email_verified). Falls back to userinfo, then to OIDC_USERNAME_CLAIM."""
    email = claims.get(config.OIDC_EMAIL_CLAIM)
    if not email or not claims.get(config.OIDC_NAME_CLAIM):
        extra = oidc.userinfo(access_token, claims["sub"])
        claims = {**extra, **claims}  # the signed id_token wins over userinfo
        email = claims.get(config.OIDC_EMAIL_CLAIM)
    verified = str(claims.get("email_verified", True)).lower() != "false" if email else True
    if not email and config.OIDC_USERNAME_CLAIM:
        u = claims.get(config.OIDC_USERNAME_CLAIM)
        email = u if isinstance(u, str) and "@" in u else None
    if not isinstance(email, str) or "@" not in email:
        raise oidc.OidcError(f"your identity provider did not send an email address (claim '{config.OIDC_EMAIL_CLAIM}'"
                             f"{', fallback ' + repr(config.OIDC_USERNAME_CLAIM) if config.OIDC_USERNAME_CLAIM else ''}); "
                             "ask your IT to release the email claim or set OIDC_EMAIL_CLAIM")
    email = normalize_email(email)
    allowed = config.OIDC_ALLOWED_EMAIL_DOMAINS
    if allowed and email.rsplit("@", 1)[1] not in allowed:
        raise oidc.OidcError(f"{email} is not allowed to sign in here (email domain not permitted)")
    name = claims.get(config.OIDC_NAME_CLAIM)
    return email, (name.strip() if isinstance(name, str) and name.strip() else email.split("@")[0])[:100], verified


def provision(db: Session, issuer: str, sub: str, email: str, name: str, verified: bool, groups: list[str]) -> tuple[User, UUID | None]:
    """Find/create the user, apply the org policy. Returns (user, org to open; None = user has no org)."""
    db.execute(text("select pg_advisory_xact_lock(7151501)"))  # first logins must not race on user/org creation
    ident = db.scalar(select(Identity).where(Identity.issuer == issuer, Identity.sub == sub))
    invites = db.execute(text("select id, org_id, role from app_pending_invites(:e)"), {"e": email}).all()
    user = db.get(User, ident.user_id) if ident else None
    if not user:
        user = db.scalar(select(User).where(User.email == email))
        if user and not (config.OIDC_LINK_BY_EMAIL and verified):
            raise oidc.OidcError(f"an account for {email} already exists but is not linked to {config.OIDC_PROVIDER_NAME} "
                                 "sign-in; ask an administrator (OIDC_LINK_BY_EMAIL=1 links by verified email)")
        if not user:
            if not (config.OIDC_AUTO_PROVISION or invites):
                raise oidc.OidcError(f"DocGen is invite-only and there is no invitation for {email}; "
                                     "ask an admin of your organization to invite this address")
            user = User(email=email, name=name, password_hash=None)
            db.add(user)
            db.flush()
        db.add(Identity(user_id=user.id, issuer=issuer, sub=sub))
        db.flush()
    ctx(db, user_id=user.id)
    opened = None
    if config.OIDC_AUTO_PROVISION:
        role = role_for(groups)
        org_id = db.scalar(text("select app_find_org(:n)"), {"n": config.OIDC_DEFAULT_ORG})
        if org_id is None:  # first user of a fresh install owns the org
            org_id = uuid4()
            ctx(db, org_id=org_id)
            db.add(Organization(id=org_id, name=config.OIDC_DEFAULT_ORG))
            db.flush()
            OrgRepo(db, Membership, org_id).add(user_id=user.id, role=Role.owner.value)
            styles.seed(OrgRepo(db, Style, org_id))
        else:
            ctx(db, org_id=org_id)
            repo = OrgRepo(db, Membership, org_id)
            m = repo.first(Membership.user_id == user.id)
            if not m:
                repo.add(user_id=user.id, role=role)
            elif config.OIDC_GROUPS_CLAIM and m.role != Role.owner.value:  # refreshed at every login; owners are never demoted
                m.role = role
        opened = org_id
    for inv_id, org_id, inv_role in invites:  # an invite is accepted by signing in with the invited (IdP-asserted) email
        ctx(db, org_id=org_id)
        inv = OrgRepo(db, Invite, org_id).get(inv_id)
        repo = OrgRepo(db, Membership, org_id)
        if not repo.first(Membership.user_id == user.id):
            repo.add(user_id=user.id, role=inv_role)
        inv.accepted_at = datetime.now(timezone.utc)
        opened = opened or org_id
    ctx(db, org_id=None)
    first = db.scalar(select(Membership).where(Membership.user_id == user.id).order_by(Membership.created_at))
    return user, opened or (first.org_id if first else None)


@router.get("/callback", dependencies=[Depends(need_oidc)])
def callback(request: Request, code: str | None = None, state: str | None = None, error: str | None = None,
             db: Session = Depends(get_db)):
    try:
        st = oidc.unseal(request.cookies.get(STATE_COOKIE))
        if error:
            log.error("OIDC IdP returned error=%s description=%s", error, request.query_params.get("error_description"))
            raise oidc.OidcError(f"the identity provider refused the login ({''.join(c for c in error if c.isalnum() or c == '_')[:40]})")
        if not state or not code or state != st["s"]:
            raise oidc.OidcError("login state mismatch (possible cross-site request): please try again")
        tokens = oidc.exchange(code, st["v"])
        claims = oidc.verify_id_token(tokens["id_token"], st["n"])
        email, name, verified = identity_from(claims, tokens.get("access_token", ""))
        user, org_id = provision(db, oidc.discovery()["issuer"], str(claims["sub"]), email, name, verified, groups_of(claims))
        resp = RedirectResponse(st["r"], status_code=302)
        resp.delete_cookie(STATE_COOKIE, path=COOKIE_PATH)
        start_session(db, resp, user, org_id, tokens["id_token"])
        db.commit()
        log.info("OIDC login %s (org %s)", email, org_id)
        return resp
    except oidc.OidcError as e:
        db.rollback()
        return to_login(str(e))
