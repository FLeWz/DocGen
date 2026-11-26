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


def _role_name(raw) -> str | None:
    r = str(raw).strip().lower()
    r = r[len(config.OIDC_ROLE_PREFIX):] if config.OIDC_ROLE_PREFIX and r.startswith(config.OIDC_ROLE_PREFIX) else r
    return r if r in {x.value for x in Role} else None


def role_from(sources: dict[str, dict]) -> str | None:
    """Highest DocGen role over id_token / access token / userinfo: client roles at resource_access.<client>.roles (exact
    key, client ids may contain dots), optional realm roles, OIDC_GROUPS_CLAIM via OIDC_ROLE_MAP. None = nothing recognised."""
    found: list[str] = []
    seen_ra = False
    for name, c in sources.items():
        ra = c.get("resource_access")
        seen_ra = seen_ra or isinstance(ra, dict)
        cl = ra.get(config.OIDC_ROLES_CLIENT_ID) if isinstance(ra, dict) else None
        lists = [("client", cl.get("roles") if isinstance(cl, dict) else None)]
        if config.OIDC_REALM_ROLES:
            ra2 = c.get("realm_access")
            lists.append(("realm", ra2.get("roles") if isinstance(ra2, dict) else None))
        for kind, v in lists:
            hit = [r for r in (_role_name(x) for x in v) if r] if isinstance(v, list) else []
            if hit:
                log.info("OIDC roles from %s %s roles: %s", name, kind, hit)
            found += hit
        hit = [config.OIDC_ROLE_MAP[g] for g in groups_of(c) if g in config.OIDC_ROLE_MAP]
        if hit:
            log.info("OIDC roles from %s groups: %s", name, hit)
        found += hit
    if not seen_ra and not found:
        log.warning("OIDC: no resource_access in the id_token, access token or userinfo; add the client roles mapper to the "
                    "client scope (Add to access token / userinfo) or enable Full scope allowed")
    role = max(found, key=lambda r: RANK[Role(r)]) if found else (config.OIDC_DEFAULT_ROLE or None)
    log.info("OIDC chosen role: %s", role)
    return role


def identity_from(claims: dict, info: dict) -> tuple[str, str, bool]:
    """-> (email, name, email_verified). `info` = userinfo; falls back to it, then to OIDC_USERNAME_CLAIM."""
    claims = {**info, **claims}  # the signed id_token wins over userinfo
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


def provision(db: Session, issuer: str, sub: str, email: str, name: str, verified: bool, role: str) -> tuple[User, UUID | None]:
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
            user = User(email=email, name=name, password_hash=None)
            db.add(user)
            db.flush()
        db.add(Identity(user_id=user.id, issuer=issuer, sub=sub))
        db.flush()
    ctx(db, user_id=user.id)
    opened = None
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
        elif m.role != role:  # refreshed at every login, up and down
            if m.role == Role.owner.value and len(repo.list(Membership.role == Role.owner.value)) == 1:
                log.warning("OIDC: %s is the only owner of %s; keeping owner although the token says %s", email, config.OIDC_DEFAULT_ORG, role)
            else:
                log.info("OIDC: role of %s changed %s -> %s", email, m.role, role)
                m.role = role
    opened = org_id
    for inv_id, org_id, inv_role in invites:  # an invite is accepted by signing in with the invited (IdP-asserted) email
        ctx(db, org_id=org_id)
        inv = OrgRepo(db, Invite, org_id).get(inv_id)
        repo = OrgRepo(db, Membership, org_id)
        if not repo.first(Membership.user_id == user.id):
            repo.add(user_id=user.id, role=inv_role)
        inv.accepted_at = datetime.now(timezone.utc)
        opened = org_id  # the invited org opens
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
        at = tokens.get("access_token", "")
        info = oidc.userinfo(at, claims["sub"])
        email, name, verified = identity_from(claims, info)
        role = role_from({"id_token": claims, "access_token": oidc.verify_access_token(at, claims["sub"]), "userinfo": info})
        if not role:  # decided before any DB write, so a refused login leaves nothing behind
            raise oidc.OidcError(f"Signed in as {email}, but no DocGen role is assigned. Ask an administrator to add you to a group "
                                 f"with the owner, admin, editor or viewer client role of {config.OIDC_ROLES_CLIENT_ID}.")
        user, org_id = provision(db, oidc.discovery()["issuer"], str(claims["sub"]), email, name, verified, role)
        resp = RedirectResponse(st["r"], status_code=302)
        resp.delete_cookie(STATE_COOKIE, path=COOKIE_PATH)
        start_session(db, resp, user, org_id, tokens["id_token"])
        db.commit()
        log.info("OIDC login %s (org %s)", email, org_id)
        return resp
    except oidc.OidcError as e:
        db.rollback()
        return to_login(str(e))
