from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker

from . import config

engine = create_engine(config.DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(engine, expire_on_commit=False)


def _apply(conn, info: dict) -> None:
    # set_config(..., true) is transaction-local, so it must be re-applied at every
    # transaction start (after_begin) -- pooled connections never leak a tenant.
    conn.execute(
        text("select set_config('app.org_id', :o, true), set_config('app.user_id', :u, true),"
             " set_config('app.invite_hash', :h, true)"),
        {"o": str(info.get("org_id") or ""), "u": str(info.get("user_id") or ""),
         "h": info.get("invite_hash") or ""},
    )


@event.listens_for(SessionLocal, "after_begin")
def _after_begin(session, transaction, connection):
    _apply(connection, session.info)


def set_ctx(db, **kw) -> None:
    """Set RLS context (org_id, user_id, invite_hash) for this and later transactions."""
    db.info.update(kw)
    _apply(db.connection(), db.info)


def get_db():
    with SessionLocal() as db:
        yield db
