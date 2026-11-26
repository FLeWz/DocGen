"""Stateless text views of editor content (markdown now, LaTeX later): see app/pandoc_bridge.py. Touches no org data."""
import json
import time
from collections import defaultdict, deque
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .. import pandoc_bridge as B
from ..deps import Ctx, require
from ..models import Role

router = APIRouter(tags=["convert"])
LIMIT, WINDOW_S = 240, 60  # requests per user per minute (autosave in text mode converts on every pause)
_hits: dict = defaultdict(deque)


def guard(ctx: Ctx) -> None:
    now, q = time.monotonic(), _hits[ctx.user.id]
    while q and now - q[0] > WINDOW_S:
        q.popleft()
    if len(q) >= LIMIT:
        raise HTTPException(429, "too many conversions, slow down", headers={"Retry-After": str(WINDOW_S)})
    q.append(now)


class ToText(BaseModel):
    format: Literal["markdown"] = "markdown"
    content: dict


class FromText(BaseModel):
    format: Literal["markdown"] = "markdown"
    text: str
    base_content: dict | None = None


def call(fn, *a):
    try:
        return fn(*a)
    except B.BridgeError as e:
        raise HTTPException(e.status, e.msg)


@router.post("/convert/to-text")
def to_text(body: ToText, ctx: Ctx = Depends(require(Role.editor))):
    guard(ctx)
    if len(json.dumps(body.content)) > 8 * B.MAX_TEXT:
        raise HTTPException(413, "content too large")
    return call(B.to_text, body.content, body.format)


@router.post("/convert/from-text")
def from_text(body: FromText, ctx: Ctx = Depends(require(Role.editor))):
    guard(ctx)
    if len(body.text.encode()) > B.MAX_TEXT:
        raise HTTPException(413, "text too large")
    return call(B.from_text, body.text, body.base_content, body.format)
