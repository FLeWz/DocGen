"""Admin CLI. Bootstrap the first user + org on an empty install (public sign-up is closed):

    docker compose exec api python -m app.manage create-user --email a@b.co --name "Ann" --org "Acme" --password-stdin < pw.txt

The password is read from stdin (piped, or prompted on a TTY), never from argv."""
import argparse
import getpass
import sys

from fastapi import HTTPException

from .db import SessionLocal
from .routers.auth import register


def create_user(email: str, name: str, org: str, password: str) -> None:
    if len(password) < 10:
        sys.exit("password must be at least 10 characters")
    with SessionLocal() as db:
        try:
            register(db, email, name, password, org)
        except HTTPException as e:
            sys.exit(e.detail)
    print(f"created {email.lower()} (owner of {org})")


def main(argv=None) -> None:
    p = argparse.ArgumentParser(prog="python -m app.manage")
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("create-user", help="create a user with its own organization")
    c.add_argument("--email", required=True)
    c.add_argument("--name", required=True)
    c.add_argument("--org", required=True)
    c.add_argument("--password-stdin", action="store_true", help="read the password from stdin (default: prompt)")
    a = p.parse_args(argv)
    pw = sys.stdin.readline().rstrip("\n") if a.password_stdin or not sys.stdin.isatty() else getpass.getpass("Password: ")
    create_user(a.email, a.name, a.org, pw)


if __name__ == "__main__":
    main()
