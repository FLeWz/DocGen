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
COOKIE_SECURE = _e("COOKIE_SECURE", "false").lower() == "true"
SMTP_HOST = _e("SMTP_HOST", "mail")
SMTP_PORT = int(_e("SMTP_PORT", "1025"))
SMTP_USER = _e("SMTP_USER", "")
SMTP_PASSWORD = _e("SMTP_PASSWORD", "")
SMTP_STARTTLS = _e("SMTP_STARTTLS", "false").lower() == "true"
SMTP_FROM = _e("SMTP_FROM", "DocGen <noreply@docgen.local>")
REPOS_ROOT = _e("REPOS_ROOT", "/repos")
FILES_ROOT = _e("FILES_ROOT", "/files")
MAX_IMAGE_BYTES = 10 * 1024 * 1024
SESSION_DAYS = 14
INVITE_DAYS = 7
PDF_URL = _e("PDF_URL", "http://pdf:3000").rstrip("/")
PDF_TIMEOUT_S = float(_e("PDF_TIMEOUT_S", "120"))
PDF_MAX_BYTES = int(_e("PDF_MAX_BYTES", str(100 * 1024 * 1024)))
