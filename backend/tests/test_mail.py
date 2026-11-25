"""SMTP against a local aiosmtpd server: none / starttls / ssl, login / plain / xoauth2, failures, presets."""
import base64
import datetime
import ipaddress
import socket
import ssl
import time

import pytest
from aiosmtpd.controller import Controller
from aiosmtpd.smtp import AuthResult, LoginPassword
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from app import mail

USER, PWD, TOKEN = "docgen@example.test", "s3cret-pass", "tok-abc"


def _cert(tmp_path):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(1).not_valid_before(now - datetime.timedelta(days=1)).not_valid_after(now + datetime.timedelta(days=1))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost"), x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), False).sign(key, hashes.SHA256()))
    crt, k = tmp_path / "c.pem", tmp_path / "k.pem"
    crt.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    k.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL,
                                    serialization.NoEncryption()))
    return str(crt), str(k)


class Handler:
    def __init__(self):
        self.msgs = []

    async def handle_DATA(self, server, session, envelope):
        self.msgs.append(envelope)
        return "250 OK"

    async def auth_XOAUTH2(self, server, args):  # args = ["XOAUTH2", b64] (initial response)
        raw = base64.b64decode(args[1]).decode()
        ok = raw == f"user={USER}\x01auth=Bearer {TOKEN}\x01\x01"
        return AuthResult(success=ok, handled=ok)


def _auth(server, session, envelope, mechanism, auth_data):
    ok = isinstance(auth_data, LoginPassword) and auth_data.login == USER.encode() and auth_data.password == PWD.encode()
    return AuthResult(success=ok, handled=ok)  # failure: let aiosmtpd answer 535


@pytest.fixture
def smtpd(tmp_path):
    started = []

    def start(mode="none", auth=False):
        crt, key = _cert(tmp_path)
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(crt, key)
        h = Handler()
        kw = dict(authenticator=_auth, auth_required=True, auth_require_tls=False) if auth else {}
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        c = Controller(h, hostname="127.0.0.1", port=port, **kw, **({"ssl_context": ctx} if mode == "ssl" else {}),
                       **({"tls_context": ctx} if mode == "starttls" else {}))
        c.start()
        started.append(c)
        return h, port, crt

    yield start
    for c in started:
        c.stop()


def env(port, **kw):
    return {"SMTP_HOST": "127.0.0.1", "SMTP_PORT": str(port), "SMTP_FROM": USER, "SMTP_TIMEOUT": "5", **kw}


def test_plain_none(smtpd):
    h, port, _ = smtpd()
    mail.deliver("a@b.co", "Hi", "body", env(port, SMTP_FROM_NAME="DocGen", SMTP_REPLY_TO="r@b.co"))
    m = h.msgs[0]
    assert m.rcpt_tos == ["a@b.co"] and b"Reply-To: r@b.co" in m.content and b"From: DocGen <docgen@example.test>" in m.content


@pytest.mark.parametrize("auth", ["auto", "login", "plain"])
def test_starttls_auth(smtpd, auth):
    h, port, ca = smtpd("starttls", auth=True)
    mail.deliver("a@b.co", "Hi", "body", env(port, SMTP_SECURITY="starttls", SMTP_CA_BUNDLE=ca, SMTP_AUTH=auth,
                                            SMTP_USERNAME=USER, SMTP_PASSWORD=PWD))
    assert len(h.msgs) == 1


def test_ssl_and_password_file(smtpd, tmp_path):
    h, port, ca = smtpd("ssl", auth=True)
    pf = tmp_path / "pw"
    pf.write_text(PWD + "\n")
    mail.deliver("a@b.co", "Hi", "body", env(port, SMTP_SECURITY="ssl", SMTP_CA_BUNDLE=ca, SMTP_USERNAME=USER,
                                            SMTP_PASSWORD_FILE=str(pf)))
    assert len(h.msgs) == 1


def test_tls_untrusted_and_verify_off(smtpd):
    h, port, _ = smtpd("starttls")
    with pytest.raises(mail.MailError, match="not trusted"):
        mail.deliver("a@b.co", "Hi", "b", env(port, SMTP_SECURITY="starttls"))
    mail.deliver("a@b.co", "Hi", "b", env(port, SMTP_SECURITY="starttls", SMTP_VERIFY_TLS="false"))
    assert len(h.msgs) == 1


def test_xoauth2_with_cached_token(smtpd, monkeypatch):
    h, port, ca = smtpd("starttls")
    h.msgs.clear()
    calls = []

    class R:
        status_code = 200
        text = ""
        def json(self): return {"access_token": TOKEN, "expires_in": 3600}

    def post(url, data, timeout):
        calls.append((url, data))
        return R()
    monkeypatch.setattr(mail.httpx, "post", post)
    mail._token.clear()
    e = env(port, SMTP_SECURITY="starttls", SMTP_CA_BUNDLE=ca, SMTP_AUTH="xoauth2", SMTP_USERNAME=USER,
            SMTP_OAUTH_TENANT_ID="tid", SMTP_OAUTH_CLIENT_ID="cid", SMTP_OAUTH_CLIENT_SECRET="sec")
    mail.deliver("a@b.co", "Hi", "b", e)
    mail.deliver("a@b.co", "Hi", "b", e)
    assert len(h.msgs) == 2 and len(calls) == 1  # second send reuses the cached token
    assert calls[0][0] == "https://login.microsoftonline.com/tid/oauth2/v2.0/token"
    assert calls[0][1]["scope"] == "https://outlook.office365.com/.default" and calls[0][1]["grant_type"] == "client_credentials"


def test_xoauth2_token_rejected_and_missing_config(monkeypatch):
    mail._token.clear()
    with pytest.raises(mail.MailError, match="needs SMTP_OAUTH_TENANT_ID"):
        mail.deliver("a@b.co", "s", "b", {"SMTP_AUTH": "xoauth2"})

    class R:
        status_code = 401
        text = ""
        def json(self): return {"error": "invalid_client", "error_description": "AADSTS7000215: Invalid client secret"}
    monkeypatch.setattr(mail.httpx, "post", lambda *a, **k: R())
    with pytest.raises(mail.MailError, match="AADSTS7000215"):
        mail.deliver("a@b.co", "s", "b", {"SMTP_AUTH": "xoauth2", "SMTP_OAUTH_TENANT_ID": "t", "SMTP_OAUTH_CLIENT_ID": "c",
                                          "SMTP_OAUTH_CLIENT_SECRET": "s"})


def test_failures(smtpd):
    h, port, _ = smtpd(auth=True)
    with pytest.raises(mail.MailError, match="authentication failed.*535"):
        mail.deliver("a@b.co", "s", "b", env(port, SMTP_USERNAME=USER, SMTP_PASSWORD="wrong", SMTP_AUTH="plain"))
    with pytest.raises(mail.MailError, match="refused|closed"):
        mail.deliver("a@b.co", "s", "b", env(1))  # nothing listens on port 1
    with pytest.raises(mail.MailError, match="cannot resolve"):
        mail.deliver("a@b.co", "s", "b", {"SMTP_HOST": "nonexistent.invalid", "SMTP_TIMEOUT": "5"})


def test_timeout(tmp_path):
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)  # accepts the TCP connection, never sends a greeting
    t = time.monotonic()
    with pytest.raises(mail.MailError, match="timed out after 1s"):
        mail.deliver("a@b.co", "s", "b", env(srv.getsockname()[1], SMTP_TIMEOUT="1"))
    assert time.monotonic() - t < 4
    srv.close()


def test_auth_error_hint_for_exchange():
    import smtplib
    s = mail.settings({"SMTP_PRESET": "microsoft365"})
    msg = mail.explain(smtplib.SMTPAuthenticationError(535, b"5.7.139 Authentication unsuccessful, SmtpClientAuthentication is disabled"), s)
    assert "smtp.office365.com:587" in msg and "Set-CASMailbox" in msg


def test_presets_and_defaults():
    d = mail.settings({})
    assert (d.host, d.port, d.security, d.auth, d.verify_tls) == ("mail", 1025, "none", "auto", True)
    m = mail.settings({"SMTP_PRESET": "microsoft365", "SMTP_USERNAME": "u@t.com"})
    assert (m.host, m.port, m.security, m.auth, m.username) == ("smtp.office365.com", 587, "starttls", "login", "u@t.com")
    o = mail.settings({"SMTP_PRESET": "microsoft365", "SMTP_PORT": "25", "SMTP_AUTH": "plain", "SMTP_HOST": ""})  # empty = unset
    assert (o.host, o.port, o.auth) == ("smtp.office365.com", 25, "plain")
    assert mail.settings({"SMTP_STARTTLS": "true", "SMTP_USER": "x"}).security == "starttls"  # legacy names
    assert mail.settings({"SMTP_STARTTLS": "true", "SMTP_SECURITY": "ssl"}).security == "ssl"
    for bad in ({"SMTP_PRESET": "gmail"}, {"SMTP_SECURITY": "tls"}, {"SMTP_AUTH": "ntlm"}):
        with pytest.raises(mail.MailError):
            mail.settings(bad)


def test_send_bg_never_hangs(monkeypatch):
    monkeypatch.setattr(mail, "send_mail", lambda *a: time.sleep(2) or True)
    t = time.monotonic()
    assert mail.send_bg("a@b.co", "s", "b", wait=0.2) is None and time.monotonic() - t < 1
    monkeypatch.setattr(mail, "send_mail", lambda *a: False)
    assert mail.send_bg("a@b.co", "s", "b") is False


def test_test_mail_endpoint(client, monkeypatch):
    from .conftest import signup
    signup(client, "a@b.co")
    monkeypatch.setattr(mail, "deliver", lambda to, *a, **k: (_ for _ in ()).throw(mail.MailError("boom 535")) if to == "bad@x.co" else None)
    assert client.post("/api/invites/test-mail", json={}).json() == {"ok": True, "error": None}
    assert client.post("/api/invites/test-mail", json={"to": "bad@x.co"}).json() == {"ok": False, "error": "boom 535"}
