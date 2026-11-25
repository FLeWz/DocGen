from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from .. import config, mail
from ..db import get_db, set_ctx
from ..deps import Ctx, optional_ctx, require
from ..models import AuthSession, Invite, Membership, Organization, Role, User
from ..schemas import AcceptIn, InviteCreateIn, InviteLookupOut, InviteOut, MeOut, TestMailIn, OrgOut, TokenIn, UserOut
from ..scoping import OrgRepo
from ..security import hash_pw, new_token, sha
from .auth import me_out, start_session

router = APIRouter(prefix="/invites", tags=["invites"])


def status_of(i: Invite) -> str:
    if i.accepted_at:
        return "accepted"
    if i.revoked_at:
        return "revoked"
    return "expired" if i.expires_at <= datetime.now(timezone.utc) else "pending"


def out(i: Invite, sent: bool | None = None) -> InviteOut:
    return InviteOut(id=i.id, email=i.email, role=i.role, status=status_of(i), expires_at=i.expires_at,
                     created_at=i.created_at, email_sent=sent)


def send(ctx: Ctx, i: Invite, token: str) -> bool | None:
    org = ctx.db.get(Organization, ctx.org_id)
    link = f"{config.PUBLIC_URL}/accept-invite?token={token}"
    return mail.send_bg(i.email, f"You're invited to {org.name} on DocGen",
        f"{ctx.user.name} invited you to join {org.name} as {i.role}.\n\nAccept: {link}\n\n"
        f"The link works once and expires on {i.expires_at:%d.%m.%Y}.")


@router.post("", status_code=201, response_model=InviteOut)
def create(body: InviteCreateIn, ctx: Ctx = Depends(require(Role.admin))):
    if body.role == Role.owner and ctx.role != Role.owner:
        raise HTTPException(403, "only an owner can invite owners")
    email = body.email.lower()
    if ctx.db.scalar(ctx.repo(Membership).query().join(User, User.id == Membership.user_id)
                     .where(User.email == email)):
        raise HTTPException(409, "already a member")
    if any(status_of(i) == "pending" for i in ctx.repo(Invite).list(Invite.email == email)):
        raise HTTPException(409, "a pending invite exists; resend it instead")
    tok = new_token()
    inv = ctx.repo(Invite).add(email=email, role=body.role.value, token_hash=sha(tok), invited_by=ctx.user.id,
                               expires_at=datetime.now(timezone.utc) + timedelta(days=config.INVITE_DAYS))
    ctx.db.commit()
    return out(inv, send(ctx, inv, tok))


@router.post("/test-mail")
def send_test_mail(body: TestMailIn, ctx: Ctx = Depends(require(Role.admin))):
    """Send a test email now (sync) and report the exact SMTP error; `to` defaults to the caller."""
    err = mail.send_test(body.to or ctx.user.email)
    return {"ok": err is None, "error": err}


@router.get("", response_model=list[InviteOut])
def list_invites(ctx: Ctx = Depends(require(Role.admin))):
    return [out(i) for i in sorted(ctx.repo(Invite).list(), key=lambda i: i.created_at, reverse=True)]


def _pending(ctx: Ctx, id: UUID) -> Invite:
    inv = ctx.repo(Invite).get(id)
    if not inv:
        raise HTTPException(404, "invite not found")
    if status_of(inv) not in ("pending", "expired"):
        raise HTTPException(409, f"invite is {status_of(inv)}")
    return inv


@router.post("/{id}/resend", response_model=InviteOut)
def resend(id: UUID, ctx: Ctx = Depends(require(Role.admin))):
    inv = _pending(ctx, id)
    tok = new_token()  # new token invalidates the old link
    inv.token_hash = sha(tok)
    inv.expires_at = datetime.now(timezone.utc) + timedelta(days=config.INVITE_DAYS)
    ctx.db.commit()
    return out(inv, send(ctx, inv, tok))


@router.delete("/{id}", status_code=204)
def revoke(id: UUID, ctx: Ctx = Depends(require(Role.admin))):
    _pending(ctx, id).revoked_at = datetime.now(timezone.utc)
    ctx.db.commit()


def _by_token(db: Session, token: str) -> Invite:
    h = sha(token)
    set_ctx(db, invite_hash=h)  # RLS lets exactly this invite through
    inv = db.scalar(select(Invite).where(Invite.token_hash == h))
    if not inv:
        raise HTTPException(404, "invite not found")
    set_ctx(db, org_id=inv.org_id)  # server-derived from the token, not from the client
    return inv


@router.post("/lookup", response_model=InviteLookupOut)
def lookup(body: TokenIn, db: Session = Depends(get_db)):
    inv = _by_token(db, body.token)
    org = db.get(Organization, inv.org_id)
    exists = db.scalar(select(User.id).where(User.email == inv.email)) is not None
    return InviteLookupOut(org_name=org.name, email=inv.email, role=inv.role, status=status_of(inv),
                           user_exists=exists)


@router.post("/accept", response_model=MeOut)
def accept(body: AcceptIn, response: Response, db: Session = Depends(get_db),
           ctx: Ctx | None = Depends(optional_ctx)):
    inv = _by_token(db, body.token)
    if status_of(inv) != "pending":
        raise HTTPException(410, f"invite is {status_of(inv)}")
    user = ctx.user if ctx else None
    if user and user.email != inv.email:
        raise HTTPException(403, "this invite was sent to a different email")
    if not user:
        if config.AUTH_MODE == "oidc":
            raise HTTPException(404, "password accounts are disabled (AUTH_MODE=oidc): sign in with your identity provider")
        if db.scalar(select(User.id).where(User.email == inv.email)):
            raise HTTPException(401, "log in first")
        if not (body.name and body.password):
            raise HTTPException(422, "name and password required for new accounts")
        user = User(email=inv.email, name=body.name, password_hash=hash_pw(body.password))
        db.add(user)
        db.flush()
        set_ctx(db, user_id=user.id)
    claimed = db.execute(update(Invite).where(Invite.id == inv.id, Invite.accepted_at.is_(None),
                                              Invite.revoked_at.is_(None))
                         .values(accepted_at=datetime.now(timezone.utc)))
    if claimed.rowcount != 1:
        db.rollback()
        raise HTTPException(410, "invite already used")
    repo = OrgRepo(db, Membership, inv.org_id)
    m = repo.first(Membership.user_id == user.id) or repo.add(user_id=user.id, role=inv.role)
    if ctx:
        ctx.auth.active_org_id = inv.org_id
    else:
        start_session(db, response, user, inv.org_id)
    db.commit()
    org = db.get(Organization, inv.org_id)
    return MeOut(user=UserOut(id=user.id, email=user.email, name=user.name),
                 org=OrgOut(id=org.id, name=org.name, role=m.role))
