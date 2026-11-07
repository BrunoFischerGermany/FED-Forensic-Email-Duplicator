"""Nachgebauter OAuth2-Anbieter für Tests (Autorisierung, Token, Gerätecode, Introspection).

Prüft wie ein echter Anbieter: Client-ID, Redirect-URI, ``state`` wird
durchgereicht, PKCE (S256) beim Code-Tausch, Refresh-Token. Der Endpunkt
``/introspect`` wird von Dovecot (``passdb oauth2``) zur Prüfung der
Access-Tokens bei ``AUTHENTICATE XOAUTH2`` aufgerufen.
"""

from __future__ import annotations

import base64
import hashlib
import http.server
import json
import secrets
import threading
import urllib.parse
from dataclasses import dataclass, field


def _jwt(claims: dict) -> str:
    def enc(d):
        return base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()

    return f"{enc({'alg': 'none'})}.{enc(claims)}.sig"


@dataclass
class FakeOAuth:
    client_id: str = "test-client"
    client_secret: str = ""
    account: str = "test@example.de"
    port: int = 0
    codes: dict = field(default_factory=dict)
    tokens: dict = field(default_factory=dict)
    refresh_tokens: dict = field(default_factory=dict)
    device_pending: dict = field(default_factory=dict)
    deny: bool = False
    tamper_state: bool = False
    _server: http.server.HTTPServer | None = None

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def provider(self, name: str = "fake"):
        from fed.core.oauth import OAuthProvider

        return OAuthProvider(name=name, auth_url=f"{self.base}/authorize", token_url=f"{self.base}/token",
                             scopes=("imap", "openid", "email"), redirect_host="127.0.0.1",
                             device_url=f"{self.base}/devicecode")

    def _issue(self) -> dict:
        at, rt = secrets.token_urlsafe(16), secrets.token_urlsafe(16)
        self.tokens[at] = self.account
        self.refresh_tokens[rt] = self.account
        return {"access_token": at, "refresh_token": rt, "expires_in": 3600, "token_type": "Bearer",
                "scope": "imap openid email", "id_token": _jwt({"email": self.account})}

    def start(self) -> FakeOAuth:
        fake = self

        class H(http.server.BaseHTTPRequestHandler):
            def _json(self, code, obj):
                body = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                u = urllib.parse.urlsplit(self.path)
                q = dict(urllib.parse.parse_qsl(u.query))
                if u.path != "/authorize":
                    return self._json(404, {})
                if q.get("client_id") != fake.client_id or q.get("code_challenge_method") != "S256":
                    return self._json(400, {"error": "invalid_request"})
                state = "falsch" if fake.tamper_state else q["state"]
                if fake.deny:
                    params = {"error": "access_denied", "error_description": "Benutzer hat abgelehnt", "state": state}
                else:
                    code = secrets.token_urlsafe(12)
                    fake.codes[code] = (q["code_challenge"], q["redirect_uri"])
                    params = {"code": code, "state": state}
                self.send_response(302)
                self.send_header("Location", f"{q['redirect_uri']}?{urllib.parse.urlencode(params)}")
                self.end_headers()

            def do_POST(self):
                n = int(self.headers.get("Content-Length", 0))
                f = dict(urllib.parse.parse_qsl(self.rfile.read(n).decode()))
                path = urllib.parse.urlsplit(self.path).path
                if path == "/introspect":
                    acc = fake.tokens.get(f.get("token", ""))
                    return self._json(200, {"active": "true", "email": acc} if acc else {"active": "false"})
                if path == "/devicecode":
                    dc = secrets.token_urlsafe(12)
                    fake.device_pending[dc] = 1  # einmal "pending", dann OK
                    return self._json(200, {"device_code": dc, "user_code": "ABCD-EFGH",
                                            "verification_uri": f"{fake.base}/device", "interval": 1,
                                            "expires_in": 60, "message": "Code ABCD-EFGH eingeben"})
                if path != "/token" or f.get("client_id") != fake.client_id:
                    return self._json(400, {"error": "invalid_client"})
                if fake.client_secret and f.get("client_secret") != fake.client_secret:
                    return self._json(401, {"error": "invalid_client"})
                g = f.get("grant_type")
                if g == "authorization_code":
                    entry = fake.codes.pop(f.get("code", ""), None)
                    if not entry:
                        return self._json(400, {"error": "invalid_grant"})
                    challenge, redirect = entry
                    v = base64.urlsafe_b64encode(hashlib.sha256(f.get("code_verifier", "").encode()).digest())
                    if v.rstrip(b"=").decode() != challenge or f.get("redirect_uri") != redirect:
                        return self._json(400, {"error": "invalid_grant", "error_description": "PKCE/Redirect"})
                    return self._json(200, fake._issue())
                if g == "refresh_token":
                    if f.get("refresh_token") not in fake.refresh_tokens:
                        return self._json(400, {"error": "invalid_grant"})
                    r = fake._issue()
                    r.pop("refresh_token")
                    return self._json(200, r)
                if g == "urn:ietf:params:oauth:grant-type:device_code":
                    left = fake.device_pending.get(f.get("device_code", ""))
                    if left is None:
                        return self._json(400, {"error": "expired_token"})
                    if left > 0:
                        fake.device_pending[f["device_code"]] = left - 1
                        return self._json(400, {"error": "authorization_pending"})
                    return self._json(200, fake._issue())
                return self._json(400, {"error": "unsupported_grant_type"})

            def log_message(self, *a):
                pass

        self._server = http.server.ThreadingHTTPServer(("127.0.0.1", self.port), H)
        self.port = self._server.server_address[1]
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        return self

    def stop(self) -> None:
        if self._server:
            self._server.shutdown()
            self._server.server_close()


def browser(url: str) -> None:
    """„Browser“ für Tests: folgt der Weiterleitung zurück an FED."""
    import urllib.request

    try:
        urllib.request.urlopen(url, timeout=10).read()
    except Exception:
        pass
