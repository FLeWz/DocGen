import re

from alembic import context
from sqlalchemy import create_engine, text

from app import config
from app.models import Base

APP_ROLE = "docgen_app"


def ensure_app_role(conn):
    # Runtime role: not superuser, no BYPASSRLS, not table owner => RLS is enforced on it.
    pw = config.APP_DB_PASSWORD
    assert pw, "APP_DB_PASSWORD required"
    verb = "ALTER" if conn.scalar(text("select 1 from pg_roles where rolname=:r"), {"r": APP_ROLE}) else "CREATE"
    sql = conn.scalar(text(f"select format('{verb} ROLE {APP_ROLE} LOGIN NOSUPERUSER NOBYPASSRLS PASSWORD %L', cast(:p as text))"),
                      {"p": pw})
    conn.exec_driver_sql(sql)


with create_engine(config.MIGRATE_DATABASE_URL, isolation_level="AUTOCOMMIT").connect() as c:
    ensure_app_role(c)

engine = create_engine(config.MIGRATE_DATABASE_URL)
with engine.connect() as connection:
    context.configure(connection=connection, target_metadata=Base.metadata)
    with context.begin_transaction():
        context.run_migrations()
