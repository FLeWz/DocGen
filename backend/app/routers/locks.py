"""Edit locks, lock requests and notifications (round 16). `{kind}` is `snippets` or `documents`."""
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import update

from .. import locks
from ..deps import Ctx, require
from ..models import Notification, Role

router = APIRouter(tags=["locks"])
V = Depends(require(Role.viewer))
E = Depends(require(Role.editor))
A = Depends(require(Role.admin))


def _et(kind: str) -> str:
    if kind not in locks.ROUTE:
        raise HTTPException(404, "unknown entity kind")
    return locks.ROUTE[kind]


def _go(ctx: Ctx, fn, *a):
    out = fn(ctx.db, ctx.org_id, ctx.user, *a)
    ctx.db.commit()
    return out


@router.get("/locks")
def list_locks(ctx: Ctx = V):
    """Active locks of the organization (badges in lists and the folder tree)."""
    locks.sweep(ctx.db, ctx.org_id)
    out = locks.active(ctx.db, ctx.org_id)
    ctx.db.commit()
    return out


@router.get("/locks/{kind}/{entity_id}")
def lock_state(kind: str, entity_id: UUID, ctx: Ctx = V):
    et = _et(kind)
    locks.entity(ctx.db, ctx.org_id, et, entity_id)
    locks.sweep(ctx.db, ctx.org_id, et, entity_id)
    out = locks.state(ctx.db, ctx.org_id, ctx.user, et, entity_id)
    ctx.db.commit()
    return out


@router.post("/locks/{kind}/{entity_id}")
def acquire(kind: str, entity_id: UUID, ctx: Ctx = E):
    """Open the editor: take the lock if free (or refresh it if mine). `held:false` + `holder` = open read-only."""
    et = _et(kind)
    locks.entity(ctx.db, ctx.org_id, et, entity_id)
    locks.sweep(ctx.db, ctx.org_id, et, entity_id)
    locks.acquire(ctx.db, ctx.org_id, ctx.user, et, entity_id)
    out = locks.state(ctx.db, ctx.org_id, ctx.user, et, entity_id)
    ctx.db.commit()
    return out


@router.post("/locks/{kind}/{entity_id}/heartbeat")
def heartbeat(kind: str, entity_id: UUID, ctx: Ctx = V):
    """Every ~20 s from the open editor. Holder: refreshes the lock and learns of an `incoming` request. Anyone else just
    polls the state (a requester sees `held` flip to true when the rights arrive)."""
    et = _et(kind)
    locks.entity(ctx.db, ctx.org_id, et, entity_id)
    locks.sweep(ctx.db, ctx.org_id, et, entity_id)
    lock = locks.live(ctx.db, ctx.org_id, et, entity_id, lock_row=True)
    if lock and lock.holder_id == ctx.user.id:
        lock.heartbeat_at = locks.now()
    out = locks.state(ctx.db, ctx.org_id, ctx.user, et, entity_id)
    ctx.db.commit()
    return out


@router.post("/locks/{kind}/{entity_id}/release")
def release(kind: str, entity_id: UUID, ctx: Ctx = V):
    et = _et(kind)
    locks.entity(ctx.db, ctx.org_id, et, entity_id)
    locks.release(ctx.db, ctx.org_id, ctx.user, et, entity_id)
    ctx.db.commit()
    return Response(status_code=204)


@router.post("/locks/{kind}/{entity_id}/request")
def request_rights(kind: str, entity_id: UUID, ctx: Ctx = E):
    """Ask the holder for edit rights: 429 {retry_after} within the cooldown, 409 when somebody else already waits."""
    return _go(ctx, locks.request_rights, _et(kind), entity_id)


@router.post("/locks/{kind}/{entity_id}/takeover")
def takeover(kind: str, entity_id: UUID, ctx: Ctx = A):
    return _go(ctx, locks.takeover, _et(kind), entity_id)


@router.post("/lock-requests/{request_id}/grant")
def grant(request_id: UUID, ctx: Ctx = E):
    """Holder only; the client has flushed its pending edit to the draft first."""
    return _go(ctx, locks.answer, request_id, True)


@router.post("/lock-requests/{request_id}/deny")
def deny(request_id: UUID, ctx: Ctx = E):
    return _go(ctx, locks.answer, request_id, False)


@router.get("/notifications")
def notifications(ctx: Ctx = V):
    rows = ctx.repo(Notification).list(Notification.user_id == ctx.user.id)
    rows = sorted(rows, key=lambda n: n.created_at, reverse=True)[:50]
    return dict(unread=sum(1 for n in rows if not n.read_at), items=[
        dict(id=str(n.id), kind=n.kind, payload=n.payload, read=bool(n.read_at), created_at=n.created_at) for n in rows])


@router.post("/notifications/read", status_code=204)
def mark_read(ctx: Ctx = V):
    ctx.db.execute(update(Notification).where(Notification.org_id == ctx.org_id, Notification.user_id == ctx.user.id,
                                              Notification.read_at.is_(None)).values(read_at=locks.now()))
    ctx.db.commit()
    return Response(status_code=204)
