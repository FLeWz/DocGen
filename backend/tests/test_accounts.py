"""Round 15: account creation (CLI/API) and login agree on how passwords and emails are normalised."""
import io

import pytest

from app import manage, throttle
from app.passwords import check_policy, normalize_email, normalize_password

from .conftest import new_client


class Stdin(io.StringIO):
    def __init__(self, data: bytes, tty=False):
        super().__init__()
        self.buffer, self._tty = io.BytesIO(data), tty

    def isatty(self):
        return self._tty


def login(c, email, pw):
    return c.post("/api/auth/login", json={"email": email, "password": pw})


def test_normalize_only_trailing_crlf():
    assert normalize_password("pw\r\n") == "pw" and normalize_password("pw\n\n") == "pw"
    assert normalize_password("  pw with spaces \r\n") == "  pw with spaces "
    assert normalize_password("a\rb") == "a\rb"
    assert normalize_email("  Ann@Example.COM \n") == "ann@example.com"


def test_policy_messages():
    assert check_policy("0123456789") == "0123456789"
    with pytest.raises(ValueError, match="at least 10 characters \\(got 9\\)"):
        check_policy("012345678")
    with pytest.raises(ValueError, match="at most 128"):
        check_policy("x" * 129)


@pytest.mark.parametrize("raw,typed", [
    (b"correct-horse-1\n", "correct-horse-1"),
    (b"correct-horse-1\r\n", "correct-horse-1"),  # PowerShell / cmd `echo`
    (b"correct-horse-1", "correct-horse-1"),
    (b"\xef\xbb\xbfcorrect-horse-1\r\n", "correct-horse-1"),  # UTF-8 BOM from PowerShell 5 / Notepad
    (b"  spaces and \xc4\x8d\xc5\xa1\xc5\xbe !\"#$%&'  \r\n", "  spaces and \u010d\u0161\u017e !\"#$%&'  "),
    (b"0123456789\n", "0123456789"),
    (b"line-one-password\nsecond line ignored\n", "line-one-password"),
])
def test_cli_password_logs_in_exactly_as_typed(client, raw, typed):
    pw = manage.read_password(True, Stdin(raw))
    assert pw == typed
    manage.create_user("Ann@Example.com", "Ann", "Acme", pw)
    r = login(new_client(), "ann@example.com", typed)
    assert r.status_code == 200, r.text
    assert login(new_client(), "ann@example.com", typed + "\r\n").status_code == 200  # CRLF on the login side too
    assert login(new_client(), "ann@example.com", typed + "x").status_code == 401


def test_cli_rejects_short_long_mismatch_and_prompts_twice(capsys):
    for raw in (b"012345678\n", b"x" * 129 + b"\n", b"\n"):
        with pytest.raises(SystemExit) as e:
            manage.read_password(True, Stdin(raw))
        assert "password must be" in str(e.value)
    answers = iter(["correct-horse-1", "correct-horse-2"])
    with pytest.raises(SystemExit, match="do not match"):
        manage.read_password(False, Stdin(b"", tty=True), prompt=lambda _: next(answers))
    answers = iter(["correct-horse-1", "correct-horse-1"])
    assert manage.read_password(False, Stdin(b"", tty=True), prompt=lambda _: next(answers)) == "correct-horse-1"


def test_cli_email_normalised_and_validated(client):
    assert manage.create_user("  Up@Example.COM ", "U", "O", "correct-horse-1") == "up@example.com"
    assert login(new_client(), "UP@example.com ", "correct-horse-1").status_code == 200
    with pytest.raises(SystemExit, match="invalid email"):
        manage.create_user("not-an-email", "U", "O2", "correct-horse-1")
    with pytest.raises(SystemExit, match="already registered"):  # same address, different case
        manage.create_user("up@example.com", "U", "O3", "correct-horse-1")


def test_main_fails_loudly_and_prints_login_url(client, monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", Stdin(b"correct-horse-1\r\n"))
    manage.main(["create-user", "--email", "A@b.co", "--name", "A", "--org", "O", "--password-stdin"])
    out = capsys.readouterr().out
    assert "created a@b.co" in out and "/login" in out
    monkeypatch.setattr("sys.stdin", Stdin(b"short\n"))
    with pytest.raises(SystemExit) as e:
        manage.main(["create-user", "--email", "c@b.co", "--name", "A", "--org", "O", "--password-stdin"])
    assert e.value.code not in (0, None)


def test_reset_password_and_list_users(client, monkeypatch, capsys):
    manage.create_user("a@b.co", "Ann", "Acme", "correct-horse-1")
    monkeypatch.setattr("sys.stdin", Stdin(b"brand-new-password\r\n"))
    manage.main(["reset-password", "--email", " A@B.co ", "--password-stdin"])
    assert login(new_client(), "a@b.co", "correct-horse-1").status_code == 401
    assert login(new_client(), "a@b.co", "brand-new-password").status_code == 200
    with pytest.raises(SystemExit, match="no user"):
        manage.reset_password("nobody@b.co", "brand-new-password")
    capsys.readouterr()
    manage.main(["list-users"])
    out = capsys.readouterr().out
    assert "a@b.co\tAnn\t1 org(s)" in out and "argon2" not in out


def test_api_signup_and_accept_use_same_policy(client, monkeypatch, outbox):
    monkeypatch.setattr("app.config.ALLOW_SIGNUP", True)
    r = client.post("/api/auth/signup", json={"email": " N@B.co ", "password": "short", "name": "n", "org_name": "o"})
    assert r.status_code == 422 and "at least 10 characters (got 5)" in r.text
    r = client.post("/api/auth/signup", json={"email": " N@B.co ", "password": "correct-horse-1\r\n", "name": "n", "org_name": "o"})
    assert r.status_code == 201 and r.json()["user"]["email"] == "n@b.co"
    assert login(new_client(), "N@b.co", "correct-horse-1").status_code == 200
    from .conftest import signup, token_from
    owner = new_client()
    signup(owner, "owner@b.co")
    owner.post("/api/invites", json={"email": "Inv@B.co", "role": "viewer"})
    c = new_client()
    tok = token_from(outbox)
    r = c.post("/api/invites/accept", json={"token": tok, "name": "I", "password": "short"})
    assert r.status_code == 422 and "at least 10 characters" in r.text
    assert c.post("/api/invites/accept", json={"token": tok, "name": "I", "password": "correct-horse-1\r\n"}).status_code == 200
    assert login(new_client(), "inv@b.co", "correct-horse-1").status_code == 200


def test_throttle_message_says_minutes_not_wrong_password(client):
    from .conftest import signup
    signup(client, "a@b.co")
    c = new_client()
    for _ in range(throttle.MAX_PER_IP):
        assert login(c, "a@b.co", "wrong-password-x").status_code == 401
    r = login(c, "a@b.co", "correct-horse-battery")  # correct password, but locked out
    assert r.status_code == 429 and "try again in 10 min" in r.json()["detail"] and "not a wrong-password" in r.json()["detail"]
    assert 0 < int(r.headers["retry-after"]) <= throttle.WINDOW_S
