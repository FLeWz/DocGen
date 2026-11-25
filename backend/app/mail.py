"""Outgoing mail over SMTP (smtplib). Settings come from SMTP_* env vars, read at send time (see README).

Presets: SMTP_PRESET=microsoft365 -> smtp.office365.com:587, starttls, auth login (every value overridable).
Auth: auto (smtplib picks from what the server offers) | login | plain | xoauth2 (Entra client credentials) | none."""
import logging
import os
import smtplib
import socket
import ssl
import threading
import time
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formataddr, parseaddr

import httpx

log = logging.getLogger(__name__)

PRESETS = {"microsoft365": {"SMTP_HOST": "smtp.office365.com", "SMTP_PORT": "587", "SMTP_SECURITY": "starttls",
                            "SMTP_AUTH": "login"}}
DEFAULTS = {"SMTP_HOST": "mail", "SMTP_PORT": "1025", "SMTP_SECURITY": "none", "SMTP_AUTH": "auto",
            "SMTP_TIMEOUT": "15", "SMTP_VERIFY_TLS": "true", "SMTP_FROM": "DocGen <noreply@docgen.local>"}
OAUTH_SCOPE = "https://outlook.office365.com/.default"


class MailError(Exception):
    """Message is safe to show to an admin (never contains the password)."""


@dataclass
class Settings:
    host: str
    port: int
    security: str
    username: str
    password: str
    auth: str
    sender: str
    sender_name: str
    reply_to: str
    timeout: float
    verify_tls: bool
    ca_bundle: str
    ehlo: str
    oauth_tenant: str
    oauth_client_id: str
    oauth_client_secret: str
    oauth_token_url: str


def settings(env=None) -> Settings:
    env = os.environ if env is None else env
    preset = (env.get("SMTP_PRESET") or "").strip().lower()
    if preset and preset not in PRESETS:
        raise MailError(f"unknown SMTP_PRESET {preset!r} (known: {', '.join(PRESETS)})")

    def get(key, legacy=None):  # explicit env > legacy name > preset > default; empty counts as unset
        for k in (key, legacy):
            if k and (env.get(k) or "").strip():
                return env[k].strip()
        return PRESETS.get(preset, {}).get(key) or DEFAULTS.get(key, "")

    security = get("SMTP_SECURITY").lower()
    if not (env.get("SMTP_SECURITY") or "").strip() and (env.get("SMTP_STARTTLS") or "").lower() == "true":
        security = "starttls"  # pre-round-15 name
    password = env.get("SMTP_PASSWORD", "")
    if env.get("SMTP_PASSWORD_FILE"):
        try:
            with open(env["SMTP_PASSWORD_FILE"], encoding="utf-8") as f:
                password = f.read().rstrip("\r\n")
        except OSError as e:
            raise MailError(f"cannot read SMTP_PASSWORD_FILE: {e}") from e
    s = Settings(
        host=get("SMTP_HOST"), port=int(get("SMTP_PORT")), security=security, username=get("SMTP_USERNAME", "SMTP_USER"),
        password=password, auth=get("SMTP_AUTH").lower(), sender=get("SMTP_FROM"), sender_name=get("SMTP_FROM_NAME"),
        reply_to=get("SMTP_REPLY_TO"), timeout=float(get("SMTP_TIMEOUT")),
        verify_tls=get("SMTP_VERIFY_TLS").lower() not in ("0", "false", "no"), ca_bundle=get("SMTP_CA_BUNDLE"),
        ehlo=get("SMTP_EHLO_HOSTNAME"), oauth_tenant=get("SMTP_OAUTH_TENANT_ID"),
        oauth_client_id=get("SMTP_OAUTH_CLIENT_ID"), oauth_client_secret=get("SMTP_OAUTH_CLIENT_SECRET"),
        oauth_token_url=get("SMTP_OAUTH_TOKEN_URL"))
    if s.security not in ("none", "starttls", "ssl"):
        raise MailError(f"SMTP_SECURITY must be none, starttls or ssl, got {s.security!r}")
    if s.auth not in ("auto", "login", "plain", "xoauth2", "none"):
        raise MailError(f"SMTP_AUTH must be auto, login, plain, xoauth2 or none, got {s.auth!r}")
    return s


_token: dict = {}  # (tenant, client) -> (access_token, monotonic expiry); shortcut: per process, not shared


def oauth_token(s: Settings) -> str:
    """Client-credentials token for Exchange Online SMTP, cached until shortly before it expires."""
    key = (s.oauth_tenant, s.oauth_client_id)
    tok, exp = _token.get(key, (None, 0))
    if tok and time.monotonic() < exp:
        return tok
    if not (s.oauth_tenant and s.oauth_client_id and s.oauth_client_secret):
        raise MailError("SMTP_AUTH=xoauth2 needs SMTP_OAUTH_TENANT_ID, SMTP_OAUTH_CLIENT_ID and SMTP_OAUTH_CLIENT_SECRET")
    url = s.oauth_token_url or f"https://login.microsoftonline.com/{s.oauth_tenant}/oauth2/v2.0/token"
    try:
        r = httpx.post(url, data={"grant_type": "client_credentials", "client_id": s.oauth_client_id,
                                  "client_secret": s.oauth_client_secret, "scope": OAUTH_SCOPE}, timeout=s.timeout)
        body = r.json()
    except (httpx.HTTPError, ValueError) as e:
        raise MailError(f"OAuth token request to {url} failed: {e}") from e
    if r.status_code != 200 or "access_token" not in body:
        raise MailError(f"OAuth token request rejected ({r.status_code}): {body.get('error_description') or body.get('error') or r.text[:200]}")
    _token[key] = (body["access_token"], time.monotonic() + max(60, int(body.get("expires_in", 3600)) - 60))
    return body["access_token"]


def _tls(s: Settings) -> ssl.SSLContext:
    ctx = ssl.create_default_context(cafile=s.ca_bundle or None)
    if not s.verify_tls:
        ctx.check_hostname, ctx.verify_mode = False, ssl.CERT_NONE
    return ctx


def explain(e: Exception, s: Settings) -> str:
    where = f"{s.host}:{s.port}"
    if isinstance(e, smtplib.SMTPAuthenticationError):
        hint = ""
        if b"5.7.139" in e.smtp_error or b"5.7.57" in e.smtp_error or b"5.7.3" in e.smtp_error:
            hint = (" Hint (Exchange Online): SMTP AUTH is disabled for this mailbox or tenant (Security Defaults/MFA, or "
                    "Set-CASMailbox -SmtpClientAuthenticationDisabled $false is missing); use an app password or SMTP_AUTH=xoauth2.")
        return f"authentication failed at {where}: {e.smtp_code} {e.smtp_error.decode(errors='replace')}.{hint}"
    if isinstance(e, smtplib.SMTPSenderRefused):
        return (f"server {where} refused sender {e.sender}: {e.smtp_code} {e.smtp_error.decode(errors='replace')} "
                "(for Microsoft 365 SMTP_FROM must be the authenticated mailbox or an alias with Send As rights)")
    if isinstance(e, smtplib.SMTPRecipientsRefused):
        return f"recipient refused by {where}: {e.recipients}"
    if isinstance(e, (socket.timeout, TimeoutError)) or "timed out" in str(e):  # smtplib wraps timeouts
        return f"timed out after {s.timeout:g}s talking to {where} (check host/port/firewall, SMTP_TIMEOUT)"
    if isinstance(e, ssl.SSLCertVerificationError):
        return f"TLS certificate of {where} not trusted: {e.verify_message} (set SMTP_CA_BUNDLE, or SMTP_VERIFY_TLS=false for tests only)"
    if isinstance(e, ssl.SSLError):
        return f"TLS error with {where}: {e} (wrong SMTP_SECURITY? port 465 = ssl, 587 = starttls, 25/1025 = none)"
    if isinstance(e, ConnectionRefusedError):
        return f"connection refused by {where} (is the server up, is SMTP_PORT right?)"
    if isinstance(e, socket.gaierror):
        return f"cannot resolve SMTP_HOST {s.host!r}: {e}"
    if isinstance(e, smtplib.SMTPNotSupportedError):
        return f"{where} does not support what is configured: {e} (SMTP_SECURITY/SMTP_AUTH)"
    if isinstance(e, smtplib.SMTPServerDisconnected):
        return f"{where} closed the connection: {e} (wrong SMTP_SECURITY for this port?)"
    return f"{type(e).__name__} talking to {where}: {e}"


def build(s: Settings, to: str, subject: str, body: str) -> EmailMessage:
    msg = EmailMessage()
    addr = parseaddr(s.sender)
    msg["From"] = formataddr((s.sender_name, addr[1])) if s.sender_name else s.sender
    msg["To"], msg["Subject"] = to, subject
    if s.reply_to:
        msg["Reply-To"] = s.reply_to
    msg.set_content(body)
    return msg


def deliver(to: str, subject: str, body: str, env=None) -> None:
    """Send synchronously; raises MailError with a readable message."""
    s = settings(env)
    try:
        if s.auth == "xoauth2":
            token = oauth_token(s)
        kw = dict(host=s.host, port=s.port, timeout=s.timeout, local_hostname=s.ehlo or None)
        with (smtplib.SMTP_SSL(context=_tls(s), **kw) if s.security == "ssl" else smtplib.SMTP(**kw)) as c:
            c.ehlo()
            if s.security == "starttls":
                c.starttls(context=_tls(s))
                c.ehlo()
            if s.auth != "none" and (s.username or s.auth == "xoauth2"):
                c.user, c.password = s.username, s.password  # read by auth_login/auth_plain
                if s.auth == "login":
                    c.auth("LOGIN", c.auth_login, initial_response_ok=False)
                elif s.auth == "plain":
                    c.auth("PLAIN", c.auth_plain)
                elif s.auth == "xoauth2":
                    c.auth("XOAUTH2", lambda ch=None: f"user={s.username or s.sender}\x01auth=Bearer {token}\x01\x01")
                else:
                    c.login(s.username, s.password)
            c.send_message(build(s, to, subject, body))
    except MailError:
        raise
    except (OSError, smtplib.SMTPException, UnicodeError) as e:
        raise MailError(explain(e, s)) from e


def send_mail(to: str, subject: str, body: str) -> bool:
    try:
        deliver(to, subject, body)
        return True
    except MailError as e:
        log.error("sending mail to %s failed: %s", to, e)
        return False


def send_test(to: str) -> str | None:
    """None on success, else the exact error."""
    try:
        deliver(to, "DocGen test email", "If you can read this, DocGen can send mail with the current SMTP settings.")
        return None
    except MailError as e:
        return str(e)


def send_bg(to: str, subject: str, body: str, wait: float = 5.0) -> bool | None:
    """Send in a thread; wait at most `wait` s so a dead server never hangs the request.
    True = sent, False = failed (logged), None = still sending."""
    res: list[bool] = []
    t = threading.Thread(target=lambda: res.append(send_mail(to, subject, body)), daemon=True)
    t.start()
    t.join(wait)
    return res[0] if res else None
