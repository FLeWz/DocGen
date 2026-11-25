import os


def _e(key: str, default: str | None = None) -> str:
    v = os.environ.get(key, default)
    if v is None:
        raise RuntimeError(f"missing env var {key}")
    return v


DATABASE_URL = _e("DATABASE_URL")
MIGRATE_DATABASE_URL = _e("MIGRATE_DATABASE_URL", "")
APP_DB_PASSWORD = _e("APP_DB_PASSWORD", "")
SECRET_KEY = _e("SECRET_KEY")
PUBLIC_URL = _e("PUBLIC_URL", "http://localhost:8080").rstrip("/")
ALLOW_SIGNUP = _e("ALLOW_SIGNUP", "0") == "1"  # invite-only unless explicitly opened
COOKIE_SECURE = _e("COOKIE_SECURE", "false").lower() == "true" or PUBLIC_URL.startswith("https://")
# SMTP_* are read at send time (mail.settings), so presets and tests see the live environment
AUTH_MODE = _e("AUTH_MODE", "local").strip().lower()  # local = built-in passwords, oidc = OpenID Connect only
if AUTH_MODE not in ("local", "oidc"):
    raise RuntimeError(f"AUTH_MODE must be local or oidc, got {AUTH_MODE!r}")


def _flag(key: str, default: str) -> bool:
    return _e(key, default).strip().lower() in ("1", "true", "yes", "on")


def _role_map(raw: str) -> dict[str, str]:
    """'{"grp": "admin"}' (JSON) or 'grp=admin,other=editor'."""
    raw = raw.strip()
    if not raw:
        return {}
    if raw.startswith("{"):
        import json
        m = {str(k): str(v) for k, v in json.loads(raw).items()}
    else:
        m = dict(p.strip().rsplit("=", 1) for p in raw.split(",") if p.strip())
    m = {k.strip(): v.strip().lower() for k, v in m.items()}
    bad = {v for v in m.values() if v not in ("owner", "admin", "editor", "viewer")}
    if bad:
        raise RuntimeError(f"OIDC_ROLE_MAP: unknown role(s) {sorted(bad)}")
    return m


# --- OpenID Connect (AUTH_MODE=oidc), see docs/OIDC.md
OIDC_PROVIDER_NAME = _e("OIDC_PROVIDER_NAME", "SSO").strip() or "SSO"
OIDC_ISSUER = _e("OIDC_ISSUER", "").strip().rstrip("/")
OIDC_CLIENT_ID = _e("OIDC_CLIENT_ID", "").strip()
OIDC_CLIENT_SECRET = _e("OIDC_CLIENT_SECRET", "").strip()  # empty = public client (PKCE only)
OIDC_REDIRECT_URI = _e("OIDC_REDIRECT_URI", "").strip() or f"{PUBLIC_URL}/api/auth/oidc/callback"
OIDC_SCOPES = _e("OIDC_SCOPES", "openid profile email").strip() or "openid profile email"
OIDC_EMAIL_CLAIM = _e("OIDC_EMAIL_CLAIM", "email").strip() or "email"
OIDC_NAME_CLAIM = _e("OIDC_NAME_CLAIM", "name").strip() or "name"
OIDC_USERNAME_CLAIM = _e("OIDC_USERNAME_CLAIM", "preferred_username").strip()  # used as email only if it contains '@'
OIDC_GROUPS_CLAIM = _e("OIDC_GROUPS_CLAIM", "").strip()  # dotted path allowed, e.g. realm_access.roles
OIDC_ROLE_MAP = _role_map(_e("OIDC_ROLE_MAP", ""))
OIDC_ALLOWED_EMAIL_DOMAINS = {d.strip().lower().lstrip("@") for d in _e("OIDC_ALLOWED_EMAIL_DOMAINS", "").split(",") if d.strip()}
OIDC_VERIFY_TLS = _flag("OIDC_VERIFY_TLS", "true")
OIDC_CA_BUNDLE = _e("OIDC_CA_BUNDLE", "").strip()
OIDC_END_SESSION = _flag("OIDC_END_SESSION", "true")
OIDC_AUTO_PROVISION = _flag("OIDC_AUTO_PROVISION", "1")
OIDC_LINK_BY_EMAIL = _flag("OIDC_LINK_BY_EMAIL", "0")
OIDC_DEFAULT_ORG = _e("OIDC_DEFAULT_ORG", "DocGen").strip() or "DocGen"
OIDC_DEFAULT_ROLE = _e("OIDC_DEFAULT_ROLE", "viewer").strip().lower()
if OIDC_DEFAULT_ROLE not in ("owner", "admin", "editor", "viewer"):
    raise RuntimeError(f"OIDC_DEFAULT_ROLE must be owner, admin, editor or viewer, got {OIDC_DEFAULT_ROLE!r}")
if AUTH_MODE == "oidc" and not (OIDC_ISSUER and OIDC_CLIENT_ID):
    raise RuntimeError("AUTH_MODE=oidc needs OIDC_ISSUER and OIDC_CLIENT_ID")
REPOS_ROOT = _e("REPOS_ROOT", "/repos")
FILES_ROOT = _e("FILES_ROOT", "/files")
MAX_IMAGE_BYTES = 10 * 1024 * 1024
SESSION_DAYS = 14
INVITE_DAYS = 7
PDF_URL = _e("PDF_URL", "http://pdf:3000").rstrip("/")
PDF_TIMEOUT_S = float(_e("PDF_TIMEOUT_S", "120"))
PDF_MAX_BYTES = int(_e("PDF_MAX_BYTES", str(100 * 1024 * 1024)))
