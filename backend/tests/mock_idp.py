"""A small OpenID Connect provider that mimics Microsoft Entra ID's v2.0 endpoint, for end-to-end SSO tests.

It signs real RS256 id_tokens, serves discovery + JWKS, enforces the authorization-code flow and PKCE, and lets a
test choose exactly which claims (or which mistakes) the "identity provider" produces.
"""
import base64
import hashlib
import secrets
import socket
import threading
import time
from typing import Any
from urllib.parse import urlencode

import uvicorn
from authlib.jose import JsonWebKey, jwt
from fastapi import FastAPI, Form, Request
from fastapi.responses import JSONResponse, RedirectResponse

TENANT = "11111111-2222-3333-4444-555555555555"       # stands in for Imperial's tenant id
CLIENT_ID, CLIENT_SECRET = "mock-client-id", "mock-client-secret"


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class MockIdP:
    def __init__(self):
        self.port = _free_port()
        self.base = f"http://127.0.0.1:{self.port}"
        self.issuer = f"{self.base}/{TENANT}/v2.0"
        self.key = JsonWebKey.generate_key("RSA", 2048, is_private=True, options={"kid": "k1"})
        self.other_key = JsonWebKey.generate_key("RSA", 2048, is_private=True, options={"kid": "k1"})  # same kid, wrong key
        self.codes: dict[str, dict[str, Any]] = {}
        self.reset()
        self.app = self._build()
        self.server = uvicorn.Server(uvicorn.Config(self.app, host="127.0.0.1", port=self.port, log_level="error"))
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    def reset(self):
        """Claims the next sign-in will carry (Entra v2: UPN in preferred_username, email only if configured)."""
        self.claims: dict[str, Any] = {"oid": "oid-0001", "tid": TENANT, "name": "Test Person",
                                       "preferred_username": "tp123@ic.ac.uk", "email": "test.person@imperial.ac.uk"}
        self.sign_with_wrong_key = False
        self.override_nonce: str | None = None
        self.audience: str = CLIENT_ID
        self.expires_in = 3600

    def start(self):
        self.thread.start()
        for _ in range(100):
            try:
                socket.create_connection(("127.0.0.1", self.port), 0.1).close()
                return
            except OSError:
                time.sleep(0.05)
        raise RuntimeError("mock idp did not start")

    def stop(self):
        self.server.should_exit = True

    # ---------------------------------------------------------------- app
    def _build(self) -> FastAPI:
        app = FastAPI()
        me = self

        @app.get(f"/{TENANT}/v2.0/.well-known/openid-configuration")
        def discovery():
            return {"issuer": me.issuer, "authorization_endpoint": f"{me.base}/{TENANT}/oauth2/v2.0/authorize",
                    "token_endpoint": f"{me.base}/{TENANT}/oauth2/v2.0/token", "jwks_uri": f"{me.base}/{TENANT}/discovery/v2.0/keys",
                    "response_types_supported": ["code", "id_token"], "subject_types_supported": ["pairwise"],
                    "id_token_signing_alg_values_supported": ["RS256"], "scopes_supported": ["openid", "profile", "email"],
                    "token_endpoint_auth_methods_supported": ["client_secret_post", "client_secret_basic"],
                    "code_challenge_methods_supported": ["S256"]}

        @app.get(f"/{TENANT}/discovery/v2.0/keys")
        def keys():
            return {"keys": [me.key.as_dict(is_private=False)]}

        @app.get(f"/{TENANT}/oauth2/v2.0/authorize")
        def authorize(request: Request):
            q = dict(request.query_params)
            if q.get("response_type") != "code" or q.get("client_id") != CLIENT_ID or "openid" not in q.get("scope", "").split():
                return JSONResponse({"error": "invalid_request"}, status_code=400)
            code = secrets.token_urlsafe(16)
            me.codes[code] = {"nonce": q.get("nonce"), "challenge": q.get("code_challenge"), "redirect_uri": q["redirect_uri"]}
            return RedirectResponse(q["redirect_uri"] + "?" + urlencode({"code": code, "state": q.get("state", "")}))

        @app.post(f"/{TENANT}/oauth2/v2.0/token")
        async def token(request: Request, grant_type: str = Form(...), code: str = Form(...), redirect_uri: str = Form(...),
                        client_id: str = Form(None), client_secret: str = Form(None), code_verifier: str = Form(None)):
            auth = request.headers.get("authorization", "")
            if auth.startswith("Basic "):
                client_id, _, client_secret = base64.b64decode(auth[6:]).decode().partition(":")
            if client_id != CLIENT_ID or client_secret != CLIENT_SECRET:
                return JSONResponse({"error": "invalid_client"}, status_code=401)
            grant = me.codes.pop(code, None)
            if grant_type != "authorization_code" or not grant or grant["redirect_uri"] != redirect_uri:
                return JSONResponse({"error": "invalid_grant"}, status_code=400)
            if grant["challenge"]:
                digest = base64.urlsafe_b64encode(hashlib.sha256((code_verifier or "").encode()).digest()).rstrip(b"=").decode()
                if digest != grant["challenge"]:
                    return JSONResponse({"error": "invalid_grant", "error_description": "PKCE verification failed"}, status_code=400)
            now = int(time.time())
            claims = {"iss": me.issuer, "aud": me.audience, "iat": now, "nbf": now, "exp": now + me.expires_in,
                      "sub": "pairwise-" + me.claims.get("oid", "x"), "ver": "2.0",
                      "nonce": me.override_nonce if me.override_nonce is not None else grant["nonce"], **me.claims}
            signing = me.other_key if me.sign_with_wrong_key else me.key
            id_token = jwt.encode({"alg": "RS256", "kid": "k1"}, claims, signing).decode()
            return {"token_type": "Bearer", "access_token": secrets.token_urlsafe(24), "id_token": id_token, "expires_in": 3599}

        return app
