"""OpenID Connect relying party: Authorization Code flow + PKCE (S256), state + nonce, full id_token validation.

Protocol only; provisioning/sessions live in routers/oidc.py. All settings are read from `config` at call time.
Libraries: httpx for HTTP, PyJWT (+ PyJWKClient: JWKS cache, refetch on unknown kid = key rotation) for tokens."""
import base64
import hashlib
import hmac
import json
import logging
import secrets
import ssl
import threading
import time
from urllib.parse import urlencode

import httpx
import jwt
from jwt import PyJWKClient

from . import config

log = logging.getLogger(__name__)
ALGS = ["RS256", "RS384", "RS512", "PS256", "PS384", "PS512", "ES256", "ES384", "ES512", "EdDSA"]  # never HS*/none
JWKS_REFRESH_MIN_S = 10  # at most one JWKS refetch per this many seconds when a token names an unknown kid
LEEWAY_S = 60  # tolerated clock skew between us and the IdP
STATE_TTL_S = 600
_disc: dict = {}
_jwks: dict[str, PyJWKClient] = {}
_lock = threading.Lock()


class OidcError(Exception):
    """Message is shown to the user on the login page: never put secrets or raw IdP output in it."""


def _ssl() -> ssl.SSLContext:
    ctx = ssl.create_default_context(cafile=config.OIDC_CA_BUNDLE or None)
    if not config.OIDC_VERIFY_TLS:
        ctx.check_hostname, ctx.verify_mode = False, ssl.CERT_NONE
    return ctx


def _http(method: str, url: str, **kw) -> httpx.Response:
    try:
        with httpx.Client(verify=_ssl(), timeout=10) as c:
            return c.request(method, url, **kw)
    except (httpx.HTTPError, OSError) as e:
        log.error("OIDC request %s %s failed: %s", method, url, e)
        hint = " (TLS: set OIDC_CA_BUNDLE to the company CA)" if "CERTIFICATE" in str(e).upper() else ""
        raise OidcError(f"cannot reach the identity provider at {url}{hint}") from e


def discovery(force: bool = False) -> dict:
    with _lock:
        if not force and _disc and time.monotonic() - _disc["at"] < 3600:
            return _disc["doc"]
    url = f"{config.OIDC_ISSUER}/.well-known/openid-configuration"
    r = _http("GET", url)
    try:
        doc = r.json()
        assert r.status_code == 200
        for k in ("issuer", "authorization_endpoint", "token_endpoint", "jwks_uri"):
            assert doc[k]
    except (ValueError, AssertionError, KeyError, TypeError) as e:
        raise OidcError(f"{url} did not return a valid OpenID discovery document (HTTP {r.status_code})") from e
    if doc["issuer"].rstrip("/") != config.OIDC_ISSUER:
        raise OidcError(f"issuer mismatch: OIDC_ISSUER is {config.OIDC_ISSUER!r} but discovery says {doc['issuer']!r}")
    with _lock:
        _disc.update(doc=doc, at=time.monotonic())
    return doc


def warmup(tries: int = 8, delay: float = 2.0) -> None:
    """Startup check (background thread): non-fatal, logs a clear error per failed attempt."""
    for i in range(tries):
        try:
            d = discovery(force=True)
            log.info("OIDC discovery ok: issuer %s, end_session=%s", d["issuer"], "end_session_endpoint" in d)
            return
        except OidcError as e:
            log.error("OIDC discovery failed (attempt %d/%d): %s", i + 1, tries, e)
            time.sleep(delay)
            delay = min(delay * 2, 60)
    log.error("OIDC discovery still failing; logins will retry on demand")


# --- state cookie: HMAC-signed JSON (state, nonce, PKCE verifier, return_to, expiry); the browser holds it, not us
def _sig(payload: str) -> str:
    return hmac.new(config.SECRET_KEY.encode(), b"oidc-state." + payload.encode(), "sha256").hexdigest()


def seal(data: dict) -> str:
    p = base64.urlsafe_b64encode(json.dumps({**data, "exp": time.time() + STATE_TTL_S}).encode()).decode()
    return f"{p}.{_sig(p)}"


def unseal(cookie: str | None) -> dict:
    try:
        p, s = (cookie or "").rsplit(".", 1)
        if not hmac.compare_digest(s, _sig(p)):
            raise ValueError
        data = json.loads(base64.urlsafe_b64decode(p))
        if data["exp"] < time.time():
            raise ValueError
        return data
    except (ValueError, KeyError, TypeError):
        raise OidcError("your login attempt expired or the browser blocked the cookie: please try again") from None


def safe_return(path: str | None) -> str:
    """Only same-site relative paths: no scheme, host, '//' or backslash tricks."""
    if not path or not path.startswith("/") or path.startswith("//") or "\\" in path or any(ord(c) < 32 for c in path):
        return "/"
    return path


def begin(return_to: str | None) -> tuple[str, str]:
    """-> (IdP authorize URL, sealed cookie value)"""
    d = discovery()
    state, nonce, verifier = secrets.token_urlsafe(24), secrets.token_urlsafe(24), secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    q = urlencode({"response_type": "code", "client_id": config.OIDC_CLIENT_ID, "redirect_uri": config.OIDC_REDIRECT_URI,
                   "scope": config.OIDC_SCOPES, "state": state, "nonce": nonce, "code_challenge": challenge,
                   "code_challenge_method": "S256"})
    sep = "&" if "?" in d["authorization_endpoint"] else "?"
    return d["authorization_endpoint"] + sep + q, seal({"s": state, "n": nonce, "v": verifier, "r": safe_return(return_to)})


def exchange(code: str, verifier: str) -> dict:
    d = discovery()
    data = {"grant_type": "authorization_code", "code": code, "redirect_uri": config.OIDC_REDIRECT_URI,
            "code_verifier": verifier, "client_id": config.OIDC_CLIENT_ID}
    kw: dict = {}
    if config.OIDC_CLIENT_SECRET:
        if "client_secret_basic" in d.get("token_endpoint_auth_methods_supported", ["client_secret_basic"]):
            kw["auth"] = (config.OIDC_CLIENT_ID, config.OIDC_CLIENT_SECRET)
        else:
            data["client_secret"] = config.OIDC_CLIENT_SECRET
    r = _http("POST", d["token_endpoint"], data=data, **kw)
    try:
        body = r.json()
    except ValueError:
        body = {}
    if r.status_code != 200 or "id_token" not in body:
        log.error("OIDC token endpoint answered %s: %s", r.status_code, r.text[:300])
        why = str(body.get("error", f"HTTP {r.status_code}"))[:60]
        raise OidcError(f"the identity provider rejected the login ({why}); check client id/secret and redirect URI")
    return body


def verify_id_token(token: str, nonce: str) -> dict:
    d = discovery()
    try:
        with _lock:
            cli = _jwks.setdefault(d["jwks_uri"], PyJWKClient(d["jwks_uri"], ssl_context=_ssl(), timeout=10))
            cli.refreshed = getattr(cli, "refreshed", 0)
        try:
            key = cli.get_signing_key_from_jwt(token).key
        except jwt.PyJWKClientError:  # unknown kid: the IdP may have rotated its keys, refetch (rate-limited) and retry
            if time.monotonic() - getattr(cli, "refreshed", 0) < JWKS_REFRESH_MIN_S:
                raise
            cli.refreshed = time.monotonic()
            cli.get_signing_keys(refresh=True)
            key = cli.get_signing_key_from_jwt(token).key
        claims = jwt.decode(token, key, algorithms=ALGS, audience=config.OIDC_CLIENT_ID, issuer=d["issuer"],
                            leeway=LEEWAY_S, options={"require": ["exp", "iss", "aud", "sub"]})
    except jwt.PyJWTError as e:
        log.error("OIDC id_token rejected: %s: %s", type(e).__name__, e)
        raise OidcError(f"the identity provider's token was rejected: {e}") from e
    if not hmac.compare_digest(str(claims.get("nonce", "")), nonce):
        raise OidcError("the identity provider's token was rejected: nonce mismatch")
    aud = claims["aud"] if isinstance(claims["aud"], list) else [claims["aud"]]
    if (len(aud) > 1 or "azp" in claims) and claims.get("azp") != config.OIDC_CLIENT_ID:
        raise OidcError("the identity provider's token was rejected: azp does not match the client id")
    return claims


def userinfo(access_token: str, sub: str) -> dict:
    ep = discovery().get("userinfo_endpoint")
    if not ep or not access_token:
        return {}
    r = _http("GET", ep, headers={"Authorization": f"Bearer {access_token}"})
    try:
        body = r.json()
        assert r.status_code == 200 and body.get("sub") == sub  # must be about the same person
        return body
    except (ValueError, AssertionError, AttributeError):
        log.error("OIDC userinfo unusable (HTTP %s)", r.status_code)
        return {}


def dig(claims: dict, path: str):
    """claims['a.b'] looks up 'a.b' first, then claims['a']['b']."""
    if path in claims:
        return claims[path]
    cur = claims
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def end_session_url(id_token: str | None) -> str | None:
    if not config.OIDC_END_SESSION:
        return None
    try:
        ep = discovery().get("end_session_endpoint")
    except OidcError:
        return None
    if not ep:
        return None
    q = {"post_logout_redirect_uri": f"{config.PUBLIC_URL}/login", "client_id": config.OIDC_CLIENT_ID}
    if id_token:
        q["id_token_hint"] = id_token
    return ep + ("&" if "?" in ep else "?") + urlencode(q)
