"""Login throttling: failed attempts per (client ip, email) and per email, in process memory.
shortcut: state is per api process and lost on restart (we run one uvicorn worker); use the DB or Redis if scaled out."""
import time

from fastapi import HTTPException

WINDOW_S, MAX_PER_IP, MAX_PER_EMAIL = 600, 5, 20
_fails: dict[tuple, list[float]] = {}


def _recent(key: tuple) -> list[float]:
    now = time.monotonic()
    v = [t for t in _fails.get(key, []) if now - t < WINDOW_S]
    if v:
        _fails[key] = v
    else:
        _fails.pop(key, None)
    return v


def check(ip: str, email: str) -> None:
    if len(_fails) > 10_000:  # bound memory under a spray of random emails
        _fails.clear()
    if len(_recent((ip, email))) >= MAX_PER_IP or len(_recent((email,))) >= MAX_PER_EMAIL:
        raise HTTPException(429, "too many failed logins, try again in a few minutes",
                            headers={"Retry-After": str(WINDOW_S)})


def failed(ip: str, email: str) -> None:
    now = time.monotonic()
    for k in ((ip, email), (email,)):
        _fails.setdefault(k, []).append(now)


def succeeded(ip: str, email: str) -> None:
    _fails.pop((ip, email), None)
