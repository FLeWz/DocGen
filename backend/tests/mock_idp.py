"""A small OpenID Provider for tests and e2e (NOT for production): discovery, JWKS, authorize (auto-approves the
current `idp.user`), token (checks PKCE + client auth), userinfo and end_session, RSA-signed tokens.

    python -m tests.mock_idp --port 9000 --issuer http://idp:9000     # e2e (compose profile oidc-test)
`idp.tamper` injects faults for the negative tests: state, nonce, aud, iss, azp, exp_delta, bad_sig, drop (claims)."""
import argparse
import base64
import hashlib
import json
import os
import secrets
import time
from urllib.parse import urlencode

import jwt
import uvicorn
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI, Form, Request
from fastapi.responses import JSONResponse, RedirectResponse


def _key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


class Idp:
    def __init__(self, issuer="http://127.0.0.1:0", client_id="docgen", client_secret="shh"):
        self.issuer, self.client_id, self.client_secret = issuer, client_id, client_secret
        self.key, self.other_key, self.kid = _key(), _key(), "k1"
        self.user = {"sub": "u-1", "email": "ann@example.com", "email_verified": True, "name": "Ann Example"}
        self.tamper: dict = {}
        self.codes: dict[str, dict] = {}
        self.logouts: list[dict] = []
        self.userinfo_only: set[str] = set()  # claims served only by /userinfo, never in the id_token

    def jwk(self):
        j = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(self.key.public_key()))
        return {**j, "kid": self.kid, "use": "sig", "alg": "RS256"}

    def id_token(self, c: dict, access_token: str) -> str:
        t, now = self.tamper, int(time.time())
        claims = {"iss": t.get("iss", self.issuer), "aud": t.get("aud", self.client_id), "iat": now,
                  "exp": now + t.get("exp_delta", 300), "nonce": t.get("nonce", c["nonce"]),
                  **{k: v for k, v in self.user.items() if k not in self.userinfo_only}}
        if "azp" in t:
            claims["azp"] = t["azp"]
        for k in t.get("drop", []):
            claims.pop(k, None)
        return jwt.encode(claims, self.other_key if t.get("bad_sig") else self.key, algorithm="RS256", headers={"kid": self.kid})


def make_app(idp: Idp) -> FastAPI:
    app = FastAPI()

    @app.get("/.well-known/openid-configuration")
    def discovery():
        i = idp.issuer
        return {"issuer": i, "authorization_endpoint": f"{i}/authorize", "token_endpoint": f"{i}/token",
                "jwks_uri": f"{i}/jwks", "userinfo_endpoint": f"{i}/userinfo", "end_session_endpoint": f"{i}/logout",
                "response_types_supported": ["code"], "id_token_signing_alg_values_supported": ["RS256"],
                "code_challenge_methods_supported": ["S256"],
                "token_endpoint_auth_methods_supported": ["client_secret_basic", "client_secret_post", "none"]}

    @app.get("/jwks")
    def jwks():
        return {"keys": [idp.jwk()]}

    @app.get("/authorize")
    def authorize(request: Request):
        q = request.query_params
        if q.get("client_id") != idp.client_id or q.get("response_type") != "code" or q.get("code_challenge_method") != "S256":
            return JSONResponse({"error": "invalid_request"}, status_code=400)
        code = secrets.token_urlsafe(16)
        idp.codes[code] = {"nonce": q["nonce"], "challenge": q["code_challenge"], "redirect_uri": q["redirect_uri"],
                           "scope": q["scope"]}
        return RedirectResponse(q["redirect_uri"] + ("&" if "?" in q["redirect_uri"] else "?")
                                + urlencode({"code": code, "state": idp.tamper.get("state", q["state"])}), status_code=302)

    @app.post("/token")
    async def token(request: Request, code: str = Form(), code_verifier: str = Form(), redirect_uri: str = Form(),
                    client_id: str = Form(None), client_secret: str = Form(None)):
        auth = request.headers.get("authorization", "")
        if auth.startswith("Basic "):
            client_id, _, client_secret = base64.b64decode(auth[6:]).decode().partition(":")
        c = idp.codes.pop(code, None)
        digest = base64.urlsafe_b64encode(hashlib.sha256(code_verifier.encode()).digest()).rstrip(b"=").decode()
        if (not c or client_id != idp.client_id or (idp.client_secret and client_secret != idp.client_secret)
                or c["challenge"] != digest or c["redirect_uri"] != redirect_uri):
            return JSONResponse({"error": "invalid_grant"}, status_code=400)
        at = secrets.token_urlsafe(16)
        idp.codes[f"at:{at}"] = {}
        return {"access_token": at, "token_type": "Bearer", "expires_in": 300, "id_token": idp.id_token(c, at)}

    @app.get("/userinfo")
    def userinfo(request: Request):
        at = request.headers.get("authorization", "")[7:]
        if f"at:{at}" not in idp.codes:
            return JSONResponse({"error": "invalid_token"}, status_code=401)
        return {k: v for k, v in idp.user.items() if k not in idp.tamper.get("drop", [])}

    @app.get("/logout")
    def logout(request: Request):
        q = dict(request.query_params)
        idp.logouts.append(q)
        return RedirectResponse(q.get("post_logout_redirect_uri", "/"), status_code=302)

    @app.post("/_admin/user")  # e2e: switch who the next login is
    async def set_user(request: Request):
        idp.user = await request.json()
        return {"ok": True}

    return app


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--port", type=int, default=9000)
    p.add_argument("--issuer", default=os.environ.get("MOCK_IDP_ISSUER", "http://idp:9000"))
    a = p.parse_args()
    uvicorn.run(make_app(Idp(a.issuer, os.environ.get("OIDC_CLIENT_ID", "docgen"), os.environ.get("OIDC_CLIENT_SECRET", "mock-secret"))),
                host="0.0.0.0", port=a.port, log_level="warning")
