"""One place for password/email input rules, used by the API schemas, the CLI and login.

Passwords: only a trailing CR/LF run is removed (Windows shells and `echo` append one); every other character,
including spaces, is part of the password. Emails: trimmed and lowercased."""
MIN_LEN, MAX_LEN = 10, 128


def normalize_password(raw: str) -> str:
    return raw.rstrip("\r\n")


def normalize_email(raw: str) -> str:
    return raw.strip().lower()


def check_policy(pw: str) -> str:
    """Return pw if it meets the policy, else raise ValueError with a message safe to show to users."""
    if len(pw) < MIN_LEN:
        raise ValueError(f"password must be at least {MIN_LEN} characters (got {len(pw)})")
    if len(pw) > MAX_LEN:
        raise ValueError(f"password must be at most {MAX_LEN} characters (got {len(pw)})")
    return pw
