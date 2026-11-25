"""Edit locks (round 16): one exclusive lock per snippet/document (org-wide), kept alive by heartbeats, with a
request -> grant | deny | timeout flow, cooldown, notifications and the hand-over log that ends up in commit trailers.

Everything takes (db, org_id) so the background sweeper can call it without a request. Times are Python UTC clocks
(single API process); env values are read at call time so tests can shrink them."""
import logging
import os
import threading
from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from . import config, mail
from .db import SessionLocal, set_ctx
from .models import DocDraft, Document, Draft, EditLock, LockRequest, Notification, Snippet, User
from .scoping import OrgRepo

log = logging.getLogger(__name__)
MODELS = {"snippet": Snippet, "document": Document}
ROUTE = {"snippets": "snippet", "documents": "document"}


def _env(key: str, default: float) -> float:
    return float(os.environ.get(key) or default)


ttl = lambda: timedelta(seconds=_env("LOCK_HEARTBEAT_TTL_S", 90))
request_timeout = lambda: timedelta(seconds=_env("LOCK_REQUEST_TIMEOUT_S", 300))
cooldown = lambda: timedelta(seconds=_env("LOCK_REQUEST_COOLDOWN_S", 1800))
now = lambda: datetime.now(timezone.utc)


class Locked(HTTPException):
    """409 {error:'locked', holder:{id,name,since}} (see main.py: top-level fields plus `detail` for the SPA)."""

    def __init__(self, holder: dict, **extra):
        self.body = dict(error="locked", holder=holder, **extra)
        super().__init__(409, detail=self.body)


def _repo(db, org_id, model):
    return OrgRepo(db, model, org_id)


def _person(db, uid: UUID) -> dict:
    u = db.get(User, uid)
    return dict(id=str(uid), name=u.name if u else "?", email=u.email if u else "")


def holder(db, lock: EditLock) -> dict:
    return dict(id=str(lock.holder_id), name=_person(db, lock.holder_id)["name"], since=lock.since.isoformat())


def entity(db, org_id, etype: str, eid: UUID):
    e = _repo(db, org_id, MODELS[etype]).get(eid)  # cross-org = not found
    if not e:
        raise HTTPException(404, f"{etype} not found")
    return e


def live(db, org_id, etype: str, eid: UUID, lock_row: bool = False) -> EditLock | None:
    """The current lock; an expired one is deleted (= free)."""
    q = _repo(db, org_id, EditLock).query().where(EditLock.entity_type == etype, EditLock.entity_id == eid)
    row = db.scalar(q.with_for_update() if lock_row else q)
    if row and row.heartbeat_at < now() - ttl():
        db.delete(row)
        db.flush()
        return None
    return row


def _acquire(db, org_id, user: User, etype: str, eid: UUID) -> tuple[EditLock, bool, bool]:
    """(lock, mine, created). Atomic: the unique (org, type, id) constraint decides a race, the loser re-reads the winner."""
    row = live(db, org_id, etype, eid, lock_row=True)
    if row:
        if row.holder_id == user.id:
            row.heartbeat_at = now()
        return row, row.holder_id == user.id, False
    try:
        with db.begin_nested():
            row = _repo(db, org_id, EditLock).add(entity_type=etype, entity_id=eid, holder_id=user.id, since=now(),
                                                  heartbeat_at=now())
        return row, True, True
    except IntegrityError:
        return live(db, org_id, etype, eid), False, False


def acquire(db, org_id, user: User, etype: str, eid: UUID) -> tuple[EditLock, bool]:
    return _acquire(db, org_id, user, etype, eid)[:2]


def require(db, org_id, user: User, etype: str, eid: UUID) -> bool:
    """Write guard: the caller holds the lock, or it is free and is taken now (API clients that never opened an
    editor). Returns True when taken implicitly. Raises Locked otherwise."""
    lock, mine, created = _acquire(db, org_id, user, etype, eid)
    if not mine:
        raise Locked(holder(db, lock))
    return created


def release(db, org_id, user: User, etype: str, eid: UUID) -> None:
    """Holder lets go; a waiting requester gets the lock at once."""
    lock = live(db, org_id, etype, eid, lock_row=True)
    if not lock or lock.holder_id != user.id:
        return
    req = _pending(db, org_id, etype, eid)
    if req:
        _transfer(db, org_id, req, "granted", "released")
    else:
        db.delete(lock)


def release_all(db, org_id, user: User) -> None:
    for lock in _repo(db, org_id, EditLock).list(EditLock.holder_id == user.id):
        release(db, org_id, user, lock.entity_type, lock.entity_id)


def active(db, org_id) -> list[dict]:
    out = []
    for lock in _repo(db, org_id, EditLock).list():
        if lock.heartbeat_at >= now() - ttl():
            e = _repo(db, org_id, MODELS[lock.entity_type]).get(lock.entity_id)
            if e:  # `folder_id` places the badge in the folder tree
                out.append(dict(entity_type=lock.entity_type, entity_id=str(lock.entity_id), folder_id=str(e.folder_id),
                                holder=holder(db, lock)))
    return out


# ---- notifications ----

def notify(db, org_id, user_id: UUID, kind: str, payload: dict) -> None:
    _repo(db, org_id, Notification).add(user_id=user_id, kind=kind, payload=payload)


def _payload(db, org_id, req: LockRequest, **extra) -> dict:
    e = _repo(db, org_id, MODELS[req.entity_type]).get(req.entity_id)
    return dict(entity_type=req.entity_type, entity_id=str(req.entity_id), title=e.title if e else "?",
                request_id=str(req.id), requester=_person(db, req.requester_id), holder=_person(db, req.holder_id),
                link=f"/{req.entity_type}s/{req.entity_id}", **extra)


def _mailholder(to: str, requester: str, title: str, link: str) -> None:
    body = (f"{requester} asks for edit rights on \"{title}\".\n\nOpen it and choose Grant or Deny: {config.PUBLIC_URL}{link}\n"
            f"Without an answer the rights move to {requester} automatically.")
    threading.Thread(target=mail.send_mail, args=(to, f"DocGen: {requester} wants to edit \"{title}\"", body), daemon=True).start()


# ---- hand-over log (becomes `Lock-transferred:` trailers on the next commit) ----

def _note(db, org_id, etype, eid, frm: dict, to: dict, kind: str) -> None:
    d = _repo(db, org_id, Draft if etype == "snippet" else DocDraft).first(
        (Draft.snippet_id if etype == "snippet" else DocDraft.document_id) == eid)
    if d:
        d.transfers = [*(d.transfers or []), dict(**{"from": frm["name"], "to": to["name"]}, kind=kind, at=now().isoformat())]


def _pending(db, org_id, etype, eid, lock_row: bool = False) -> LockRequest | None:
    q = _repo(db, org_id, LockRequest).query().where(
        LockRequest.entity_type == etype, LockRequest.entity_id == eid, LockRequest.status == "pending")
    return db.scalar(q.with_for_update() if lock_row else q)


def _transfer(db, org_id, req: LockRequest, status: str, kind: str) -> None:
    """The lock moves from the request's holder to its requester; both are told."""
    req.status, req.resolved_at = status, now()
    row = live(db, org_id, req.entity_type, req.entity_id, lock_row=True)
    if row:
        row.holder_id, row.since, row.heartbeat_at = req.requester_id, now(), now()
    else:
        _repo(db, org_id, EditLock).add(entity_type=req.entity_type, entity_id=req.entity_id, holder_id=req.requester_id,
                                        since=now(), heartbeat_at=now())
    frm, to = _person(db, req.holder_id), _person(db, req.requester_id)
    _note(db, org_id, req.entity_type, req.entity_id, frm, to, kind)
    ntype = "lock_granted" if status == "granted" else "lock_timeout"
    notify(db, org_id, req.requester_id, ntype, _payload(db, org_id, req, kind=kind))
    if kind == "timeout":
        notify(db, org_id, req.holder_id, "lock_timeout", _payload(db, org_id, req, kind=kind))
    db.flush()


# ---- request flow ----

def _req_out(r: LockRequest | None, db=None) -> dict | None:
    if not r:
        return None
    out = dict(id=str(r.id), status=r.status, created_at=r.created_at, expires_at=r.expires_at, resolved_at=r.resolved_at)
    if db:
        out.update(requester=_person(db, r.requester_id), holder=_person(db, r.holder_id))
    return out


def request_rights(db, org_id, user: User, etype: str, eid: UUID) -> dict:
    entity(db, org_id, etype, eid)
    sweep(db, org_id, etype, eid)
    lock, mine = acquire(db, org_id, user, etype, eid)
    if mine:  # free (or already mine): nothing to ask for
        return state(db, org_id, user, etype, eid)
    last = db.scalar(_repo(db, org_id, LockRequest).query().where(
        LockRequest.entity_type == etype, LockRequest.entity_id == eid, LockRequest.requester_id == user.id)
        .order_by(LockRequest.created_at.desc()).limit(1))
    if last and last.created_at > now() - cooldown():
        until = last.created_at + cooldown()
        retry = max(1, int((until - now()).total_seconds()))
        raise HTTPException(429, detail=dict(error="cooldown", retry_after=retry, retry_at=until.isoformat(),
                                             message="You can request edit rights on this file once every "
                                                     f"{int(cooldown().total_seconds() // 60)} min"),
                            headers={"Retry-After": str(retry)})
    other = _pending(db, org_id, etype, eid)
    if other:
        raise HTTPException(409, detail=dict(error="request_pending", requester=_person(db, other.requester_id),
                                             expires_at=other.expires_at.isoformat()))
    try:
        with db.begin_nested():
            req = _repo(db, org_id, LockRequest).add(entity_type=etype, entity_id=eid, requester_id=user.id,
                                                     holder_id=lock.holder_id, created_at=now(),
                                                     expires_at=now() + request_timeout())
    except IntegrityError:  # lost the race for the single pending slot
        other = _pending(db, org_id, etype, eid)
        raise HTTPException(409, detail=dict(error="request_pending", requester=_person(db, other.requester_id) if other else None,
                                             expires_at=other and other.expires_at.isoformat()))
    p = _payload(db, org_id, req)
    notify(db, org_id, lock.holder_id, "lock_request", p)
    holder = db.get(User, lock.holder_id)
    if holder and holder.email:
        _mailholder(holder.email, user.name, p["title"], p["link"])
    return state(db, org_id, user, etype, eid)


def answer(db, org_id, user: User, request_id: UUID, grant: bool) -> dict:
    req = _repo(db, org_id, LockRequest).get(request_id)
    if not req or req.holder_id != user.id:
        raise HTTPException(404, "request not found")
    sweep(db, org_id, req.entity_type, req.entity_id)
    req = db.scalar(_repo(db, org_id, LockRequest).query().where(LockRequest.id == request_id).with_for_update())
    if req.status != "pending":
        raise HTTPException(409, detail=dict(error="not_pending", status=req.status))
    if grant:
        _transfer(db, org_id, req, "granted", "granted")
    else:
        req.status, req.resolved_at = "denied", now()
        notify(db, org_id, req.requester_id, "lock_denied", _payload(db, org_id, req))
    return state(db, org_id, user, req.entity_type, req.entity_id)


def takeover(db, org_id, user: User, etype: str, eid: UUID) -> dict:
    entity(db, org_id, etype, eid)
    lock = live(db, org_id, etype, eid, lock_row=True)
    if lock and lock.holder_id != user.id:
        prev = _person(db, lock.holder_id)
        req = _pending(db, org_id, etype, eid, lock_row=True)
        if req:
            req.status, req.resolved_at = "cancelled", now()
        lock.holder_id, lock.since, lock.heartbeat_at = user.id, now(), now()
        _note(db, org_id, etype, eid, prev, _person(db, user.id), "taken_over")
        e = entity(db, org_id, etype, eid)
        notify(db, org_id, UUID(prev["id"]), "lock_taken", dict(entity_type=etype, entity_id=str(eid), title=e.title,
                                                               by=user.name, link=f"/{etype}s/{eid}"))
    else:
        acquire(db, org_id, user, etype, eid)
    return state(db, org_id, user, etype, eid)


def sweep(db, org_id, etype: str | None = None, eid: UUID | None = None) -> int:
    """Pending requests whose time is up (or whose holder is gone) hand the lock to the requester: timed_out."""
    q = _repo(db, org_id, LockRequest).query().where(LockRequest.status == "pending")
    if eid:
        q = q.where(LockRequest.entity_type == etype, LockRequest.entity_id == eid)
    n = 0
    for req in db.scalars(q.with_for_update(skip_locked=True)).all():
        lock = live(db, org_id, req.entity_type, req.entity_id)
        if req.expires_at <= now() or not lock or lock.holder_id != req.holder_id:
            _transfer(db, org_id, req, "timed_out", "timeout")
            n += 1
    return n


def sweep_all() -> int:
    """Background pass over every org that has a pending request (SECURITY DEFINER app_lock_orgs, RLS hides the rest)."""
    n = 0
    with SessionLocal() as db:
        orgs = [r[0] for r in db.execute(text("select org_id from app_lock_orgs()"))]
        db.rollback()
    for org in orgs:
        with SessionLocal() as db:
            set_ctx(db, org_id=org)
            n += sweep(db, org)
            db.commit()
    return n


def sweeper_loop(stop: threading.Event) -> None:
    while not stop.wait(_env("LOCK_SWEEP_S", 15)):
        try:
            sweep_all()
        except Exception:  # never let one bad pass kill the thread
            log.exception("lock sweeper pass failed")


# ---- state for heartbeat / status ----

def state(db, org_id, user: User, etype: str, eid: UUID) -> dict:
    lock = live(db, org_id, etype, eid)
    mine = bool(lock and lock.holder_id == user.id)
    pend = _pending(db, org_id, etype, eid)
    last = db.scalar(_repo(db, org_id, LockRequest).query().where(
        LockRequest.entity_type == etype, LockRequest.entity_id == eid, LockRequest.requester_id == user.id)
        .order_by(LockRequest.created_at.desc()).limit(1))
    until = last and last.created_at + cooldown()
    return dict(entity_type=etype, entity_id=str(eid), held=mine, holder=lock and holder(db, lock),
                incoming=_req_out(pend, db) if pend and mine else None,
                pending=pend and dict(requester=_person(db, pend.requester_id), expires_at=pend.expires_at),
                my_request=_req_out(last), cooldown_until=until if until and until > now() else None,
                server_time=now())
