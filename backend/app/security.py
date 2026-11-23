import hashlib
import hmac
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

from . import config

_ph = PasswordHasher()
DUMMY_HASH = _ph.hash("dummy-password")  # verified against when the user is unknown (timing)


def hash_pw(pw: str) -> str:
    return _ph.hash(pw)


def verify_pw(hashed: str, pw: str) -> bool:
    try:
        return _ph.verify(hashed, pw)
    except (VerificationError, InvalidHashError):
        return False


def new_token() -> str:
    return secrets.token_urlsafe(32)


def sha(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _sig(nonce: str) -> str:
    return hmac.new(config.SECRET_KEY.encode(), nonce.encode(), "sha256").hexdigest()


def make_csrf() -> str:
    n = secrets.token_urlsafe(16)
    return f"{n}.{_sig(n)}"


def valid_csrf(token: str | None) -> bool:
    if not token or "." not in token:
        return False
    n, s = token.split(".", 1)
    return hmac.compare_digest(s, _sig(n))
