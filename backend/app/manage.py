"""Admin CLI (run inside the api container, `-T` is required when stdin is piped):

    printf '%s\\n' 'a-long-password' | docker compose exec -T api python -m app.manage create-user \\
        --email a@b.co --name "Ann" --org "Acme" --password-stdin
    docker compose exec api python -m app.manage create-user --email a@b.co --name Ann --org Acme   # prompts twice
    docker compose exec api python -m app.manage reset-password --email a@b.co [--password-stdin]
    docker compose exec api python -m app.manage list-users
    docker compose exec api python -m app.manage test-mail --to you@example.com

Passwords come from stdin (UTF-8, a trailing CR/LF is dropped, nothing else) or a double prompt, never argv.
Every failure exits non-zero with a message on stderr."""
import argparse
import getpass
import sys

from fastapi import HTTPException
from pydantic import TypeAdapter, ValidationError, EmailStr
from sqlalchemy import func, select

from . import config, mail
from .db import SessionLocal, set_ctx
from .models import Membership, Organization, User
from .passwords import check_policy, normalize_email, normalize_password
from .routers.auth import register
from .security import hash_pw


def clean_email(raw: str) -> str:
    email = normalize_email(raw)
    try:
        TypeAdapter(EmailStr).validate_python(email)
    except ValidationError as e:
        sys.exit(f"invalid email {raw!r}: {e.errors()[0]['msg']}")
    return email


def read_password(from_stdin: bool, stdin=None, prompt=getpass.getpass) -> str:
    """stdin (piped, or --password-stdin) or two prompts. Returns a normalised password that meets the policy."""
    stdin = stdin or sys.stdin
    if from_stdin or not stdin.isatty():
        raw = (stdin.buffer.read().decode("utf-8-sig") if hasattr(stdin, "buffer") else stdin.read())
        pw = normalize_password(raw.split("\n", 1)[0])  # first line only
    else:
        pw = normalize_password(prompt("Password: "))
        if pw != normalize_password(prompt("Repeat password: ")):
            sys.exit("error: passwords do not match, nothing was changed")
    try:
        return check_policy(pw)
    except ValueError as e:
        sys.exit(f"error: {e}")


def create_user(email: str, name: str, org: str, password: str | None) -> str:
    """password None = SSO-only user (AUTH_MODE=oidc)."""
    email = clean_email(email)
    with SessionLocal() as db:
        try:
            register(db, email, name, password, org)
        except HTTPException as e:
            sys.exit(f"error: {e.detail}")
    return email


def reset_password(email: str, password: str) -> None:
    email = clean_email(email)
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == email))
        if not user:
            sys.exit(f"error: no user {email}")
        user.password_hash = hash_pw(password)
        db.commit()


def list_users() -> list[tuple[str, str, str]]:
    with SessionLocal() as db:
        # memberships are RLS-protected: count per user through the user's own context
        rows = []
        for u in db.scalars(select(User).order_by(User.created_at)).all():
            set_ctx(db, user_id=u.id)
            n = db.scalar(select(func.count()).select_from(Membership).where(Membership.user_id == u.id))
            rows.append((u.email, u.name, f"{n} org(s)" + ("" if u.password_hash else ", SSO only")))
        return rows


def main(argv=None) -> None:
    p = argparse.ArgumentParser(prog="python -m app.manage")
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("create-user", help="create a user with its own organization")
    c.add_argument("--email", required=True)
    c.add_argument("--name", required=True)
    c.add_argument("--org", required=True)
    c.add_argument("--password-stdin", action="store_true", help="read the password from stdin (default: prompt)")
    r = sub.add_parser("reset-password", help="set a new password for an existing user")
    r.add_argument("--email", required=True)
    r.add_argument("--password-stdin", action="store_true")
    sub.add_parser("list-users", help="list users (never prints hashes)")
    t = sub.add_parser("test-mail", help="send a test email with the configured SMTP settings")
    t.add_argument("--to", required=True)
    a = p.parse_args(argv)
    if a.cmd in ("create-user", "reset-password") and config.AUTH_MODE == "oidc":
        if a.cmd == "reset-password":
            sys.exit("error: AUTH_MODE=oidc, users have no password; nothing to reset")
        email = create_user(a.email, a.name, a.org, None)  # first admin: linked at first SSO login (OIDC_LINK_BY_EMAIL=1)
        print(f"created {email} (owner of {a.org}), SSO only: signs in through {config.OIDC_PROVIDER_NAME}")
    elif a.cmd == "create-user":
        email = create_user(a.email, a.name, a.org, read_password(a.password_stdin))
        print(f"created {email} (owner of {a.org})\nlog in at {config.PUBLIC_URL}/login with that email and the password you entered")
    elif a.cmd == "reset-password":
        reset_password(a.email, read_password(a.password_stdin))
        print(f"password of {clean_email(a.email)} changed; log in at {config.PUBLIC_URL}/login")
    elif a.cmd == "list-users":
        for row in list_users():
            print("\t".join(row))
    elif a.cmd == "test-mail":
        err = mail.send_test(a.to)
        if err:
            sys.exit(f"error: {err}")
        print(f"test email sent to {a.to}")


if __name__ == "__main__":
    main()
