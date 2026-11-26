import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text

from .db import engine
from . import config, locks, oidc as oidc_lib
from .routers import (auth, oidc, base_documents, commits, documents, exports, folders, images, imports, invites, library,
                      locks as locks_router, maintenance, members, orgs, overview, render, snippets, styles)
from .security import valid_csrf

@asynccontextmanager
async def lifespan(app):
    if config.AUTH_MODE == "oidc":  # non-fatal startup check, retried in the background with clear log lines
        oidc_lib.warn_legacy()
        threading.Thread(target=oidc_lib.warmup, daemon=True).start()
    stop = threading.Event()  # sweeper: pending lock requests time out even when nobody's tab is open
    threading.Thread(target=locks.sweeper_loop, args=(stop,), daemon=True).start()
    yield
    stop.set()


app = FastAPI(title="DocGen", docs_url="/api/docs", openapi_url="/api/openapi.json", redoc_url=None, lifespan=lifespan)


@app.middleware("http")
async def csrf_guard(request: Request, call_next):
    # double-submit: signed cookie value must equal the header on every unsafe request
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        c, h = request.cookies.get(auth.CSRF_COOKIE), request.headers.get("x-csrf-token")
        if not (h and c == h and valid_csrf(h)):
            return JSONResponse({"detail": "invalid CSRF token"}, status_code=403)
    return await call_next(request)


@app.exception_handler(base_documents.NameExists)
async def name_exists(request: Request, e: base_documents.NameExists):
    # top-level fields for scripts, `detail.message` for the SPA's error banner
    return JSONResponse({**e.body, "detail": {**e.body, "message": f"A base document named {e.body['name']} already exists"}}, status_code=409)


@app.exception_handler(locks.Locked)
async def locked(request: Request, e: locks.Locked):
    return JSONResponse({**e.body, "detail": {**e.body, "message": f"Locked by {e.body['holder']['name']}"}}, status_code=409)


for r in (auth, oidc, orgs, members, invites, folders, snippets, commits, styles, images, base_documents, documents, exports, library, imports, maintenance, render, overview, locks_router):
    app.include_router(r.router, prefix="/api")


@app.get("/api/health", include_in_schema=False)
def health():
    with engine.connect() as c:
        c.execute(text("select 1"))
    return {"ok": True}
