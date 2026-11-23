from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text

from .db import engine
from .routers import (auth, base_documents, commits, documents, exports, folders, images, invites, library,
                      members, orgs, snippets, styles)
from .security import valid_csrf

app = FastAPI(title="DocGen", docs_url="/api/docs", openapi_url="/api/openapi.json", redoc_url=None)


@app.middleware("http")
async def csrf_guard(request: Request, call_next):
    # double-submit: signed cookie value must equal the header on every unsafe request
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        c, h = request.cookies.get(auth.CSRF_COOKIE), request.headers.get("x-csrf-token")
        if not (h and c == h and valid_csrf(h)):
            return JSONResponse({"detail": "invalid CSRF token"}, status_code=403)
    return await call_next(request)


for r in (auth, orgs, members, invites, folders, snippets, commits, styles, images, base_documents, documents, exports, library):
    app.include_router(r.router, prefix="/api")


@app.get("/api/health", include_in_schema=False)
def health():
    with engine.connect() as c:
        c.execute(text("select 1"))
    return {"ok": True}
