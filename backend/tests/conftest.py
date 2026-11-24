import os
from pathlib import Path

from sqlalchemy.engine import make_url

TEST_DB = "docgen_test"
_owner = make_url(os.environ["MIGRATE_DATABASE_URL"])
_app = make_url(os.environ["DATABASE_URL"])
ADMIN_URL = _owner  # connects to the main db, used to create the test db
OWNER_URL = _owner.set(database=TEST_DB)
APP_URL = _app.set(database=TEST_DB)
os.environ["MIGRATE_DATABASE_URL"] = OWNER_URL.render_as_string(hide_password=False)
os.environ["DATABASE_URL"] = APP_URL.render_as_string(hide_password=False)

import tempfile  # noqa: E402
os.environ["REPOS_ROOT"] = tempfile.mkdtemp(prefix="repos-")  # never the real /repos volume
os.environ["FILES_ROOT"] = tempfile.mkdtemp(prefix="files-")

import pytest  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402


@pytest.fixture(scope="session")
def owner_engine():
    with create_engine(ADMIN_URL, isolation_level="AUTOCOMMIT").connect() as c:
        if not c.scalar(text("select 1 from pg_database where datname=:d"), {"d": TEST_DB}):
            c.execute(text(f"create database {TEST_DB}"))
    root = Path(__file__).resolve().parent.parent
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "alembic"))
    command.upgrade(cfg, "head")
    return create_engine(OWNER_URL)


@pytest.fixture(autouse=True)
def clean(owner_engine):
    from app import throttle
    throttle._fails.clear()
    with owner_engine.begin() as c:
        c.execute(text("truncate users, organizations, memberships, invites, sessions cascade"))


@pytest.fixture
def outbox(monkeypatch):
    sent = []
    monkeypatch.setattr("app.mail.send_mail", lambda to, subject, body: sent.append((to, subject, body)) or True)
    return sent


def new_client() -> TestClient:
    from app.main import app
    c = TestClient(app)  # own cookie jar per client = one browser
    c.headers["X-CSRF-Token"] = c.get("/api/auth/csrf").json()["csrf_token"]
    return c


@pytest.fixture
def client():
    return new_client()


PW = "correct-horse-battery"


def signup(c, email, org="Org", name="Test"):
    """Create user+org like the bootstrap CLI (public sign-up is closed) and log `c` in."""
    from app.db import SessionLocal
    from app.routers.auth import register
    with SessionLocal() as db:
        register(db, email, name, PW, org)
    return login(c, email)


def login(c, email):
    r = c.post("/api/auth/login", json={"email": email, "password": PW})
    assert r.status_code == 200, r.text
    return r.json()


def token_from(outbox, idx=-1):
    return outbox[idx][2].split("token=")[1].split()[0]


def add_member(owner, outbox, email, role):
    """Invite `email` as `role` (new user) and return its logged-in client."""
    assert owner.post("/api/invites", json={"email": email, "role": role}).status_code == 201
    c = new_client()
    r = c.post("/api/invites/accept", json={"token": token_from(outbox), "name": "N", "password": PW})
    assert r.status_code == 200, r.text
    return c
