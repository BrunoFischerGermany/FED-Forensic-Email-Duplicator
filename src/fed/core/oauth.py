"""OAuth2-Anmeldung für Google und Microsoft 365.

Verfahren: Authorization Code + PKCE (RFC 7636) mit Loopback-Redirect
(RFC 8252): Der System-Browser öffnet die Anmeldeseite, der Anbieter leitet
auf ``http://127.0.0.1:<port>/`` bzw. ``http://localhost:<port>/`` zurück,
FED tauscht den Code gegen ein Token. Für Microsoft gibt es zusätzlich den
Gerätecode-Ablauf (Anmeldung auf einem anderen Gerät).

Tokens bleiben nur im Arbeitsspeicher. Protokolliert werden Anbieter,
angemeldetes Konto (aus dem ID-Token), Scopes und Zeitpunkt – nie Tokens.
Client-IDs kommen aus der Config (je Agentur eigene Registrierung, siehe
``docs/oauth-einrichtung.md``).
"""

from __future__ import annotations

import base64
import hashlib
import http.server
import json
import re
import secrets
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from collections.abc import Callable
from dataclasses import dataclass, field

from fed.core.cancel import Cancelled, CancelToken
from fed.core.msg import current_language, t


class OAuthError(RuntimeError):
    pass


@dataclass(frozen=True)
class OAuthProvider:
    name: str
    auth_url: str
    token_url: str
    scopes: tuple[str, ...]
    redirect_host: str = "127.0.0.1"
    device_url: str | None = None
    extra_auth_params: tuple[tuple[str, str], ...] = ()


GOOGLE = OAuthProvider(
    name="google",
    auth_url="https://accounts.google.com/o/oauth2/v2/auth",
    token_url="https://oauth2.googleapis.com/token",
    scopes=("https://mail.google.com/", "openid", "email"),
    redirect_host="127.0.0.1",
    extra_auth_params=(("access_type", "offline"), ("prompt", "consent")),
)


MICROSOFT_IMAP_HOSTS = {"outlook.office365.com", "outlook.office.com", "imap-mail.outlook.com", "imap.outlook.com"}
# private Microsoft-Konten (MSA): diese Domains (und Länder-Varianten) gehören nicht zu einem Firmen-Tenant
_MS_CONSUMER = re.compile(r"^(outlook|hotmail|live|windowslive|msn|passport)\.[a-z.]+$")


def is_microsoft_consumer(email: str) -> bool:
    """Privates Microsoft-Konto (Outlook.com, Hotmail, Live, MSN – auch outlook.de, hotmail.co.uk …)."""
    domain = email.strip().lower().rpartition("@")[2]
    return bool(_MS_CONSUMER.match(domain)) or domain == "msn.com"


def microsoft_tenant(tenant: str, email: str = "") -> str:
    """Mandant für die Anmeldung. Private Konten können sich am Endpunkt ``organizations`` nicht anmelden –
    dann wird ``consumers`` genommen. Eine fest eingetragene Tenant-ID oder ``common`` bleibt unverändert."""
    tenant = (tenant or "organizations").strip()
    if tenant.lower() == "organizations" and is_microsoft_consumer(email):
        return "consumers"
    return tenant


def explain_error(provider: str, error: str) -> str:
    """Hinweis zu typischen Anmeldefehlern bei Microsoft (AADSTS-Codes); sonst leer."""
    if provider != "microsoft":
        return ""
    low = error.lower()
    if any(k in low for k in ("aadsts50020", "aadsts9002331", "aadsts70000", "not enabled for consumers",
                              "unauthorized_client", "aadsts50194", "aadsts500200")):
        return t("Die App-Registrierung erlaubt diesen Kontotyp nicht. Für private Konten (Outlook.com, Hotmail) "
                 "in Entra unter „Unterstützte Kontotypen“ auch persönliche Microsoft-Konten zulassen und "
                 "tenant = \"common\" eintragen – siehe docs/oauth-einrichtung.md.")
    if any(k in low for k in ("aadsts65001", "aadsts90094", "consent_required")):
        return t("Die Zustimmung fehlt: Ein Administrator des Tenants muss der App die Berechtigung "
                 "IMAP.AccessAsUser.All erteilen (Entra → App-Registrierung → API-Berechtigungen → "
                 "Administratorzustimmung erteilen).")
    if "aadsts7000218" in low:
        return t("In der App-Registrierung „Öffentliche Clientflows zulassen“ auf Ja stellen "
                 "(nötig für den Gerätecode).")
    if "aadsts53003" in low or "aadsts530" in low:
        return t("Eine Richtlinie für bedingten Zugriff des Tenants blockiert die Anmeldung – Administrator fragen.")
    return ""


def microsoft(tenant: str = "organizations") -> OAuthProvider:
    base = f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0"
    return OAuthProvider(
        name="microsoft",
        auth_url=f"{base}/authorize",
        token_url=f"{base}/token",
        scopes=("https://outlook.office.com/IMAP.AccessAsUser.All", "offline_access", "openid", "email", "profile"),
        redirect_host="localhost",
        device_url=f"{base}/devicecode",
        extra_auth_params=(("response_mode", "query"), ("prompt", "select_account")),
    )


@dataclass
class Token:
    access_token: str = field(repr=False)
    expires_at: float
    refresh_token: str | None = field(default=None, repr=False)
    scope: str = ""
    account: str | None = None  # aus dem ID-Token (email / preferred_username)
    provider: str = ""

    @property
    def expired(self) -> bool:
        return time.time() > self.expires_at - 60

    def audit(self) -> dict:
        """Was ins Log darf (ohne Tokens)."""
        return {"provider": self.provider, "account": self.account, "scope": self.scope,
                "expires_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(self.expires_at)),
                "refresh_token": self.refresh_token is not None}


# ---------------------------------------------------------------- Hilfen
def pkce_pair() -> tuple[str, str]:
    """(code_verifier, code_challenge) nach RFC 7636, Methode S256."""
    verifier = secrets.token_urlsafe(64)
    return verifier, pkce_challenge(verifier)


def pkce_challenge(verifier: str) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()


def xoauth2_string(user: str, access_token: str) -> bytes:
    """Initiale Client-Antwort für ``AUTHENTICATE XOAUTH2`` (imaplib kodiert sie Base64)."""
    return f"user={user}\x01auth=Bearer {access_token}\x01\x01".encode()


def id_token_account(id_token: str | None) -> str | None:
    """Liest ``email`` bzw. ``preferred_username`` aus dem ID-Token.

    Das ID-Token kommt direkt per TLS vom Token-Endpunkt des Anbieters; es dient
    nur der Protokollierung, welches Konto zugestimmt hat.
    """
    if not id_token or id_token.count(".") != 2:
        return None
    payload = id_token.split(".")[1]
    payload += "=" * (-len(payload) % 4)
    try:
        claims = json.loads(base64.urlsafe_b64decode(payload))
    except (ValueError, json.JSONDecodeError):
        return None
    return claims.get("email") or claims.get("preferred_username") or claims.get("upn")


def _post(url: str, data: dict, proxy: str = "", timeout: float = 30) -> dict:
    handlers = [urllib.request.ProxyHandler({"http": proxy, "https": proxy})] if proxy else []
    opener = urllib.request.build_opener(*handlers)
    req = urllib.request.Request(url, data=urllib.parse.urlencode(data).encode(),
                                 headers={"Content-Type": "application/x-www-form-urlencoded",
                                          "Accept": "application/json",
                                          "User-Agent": "FED-ForensicEmailDuplicator"})
    try:
        with opener.open(req, timeout=timeout) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:
            body = json.loads(e.read())
        except (ValueError, json.JSONDecodeError):
            body = {"error": f"HTTP {e.code}"}
        return body


def _token_from_response(resp: dict, provider: OAuthProvider, old_refresh: str | None = None) -> Token:
    if "access_token" not in resp:
        raise OAuthError(f"{provider.name}: {resp.get('error', 'error')} – {resp.get('error_description', '')}".strip())
    return Token(
        access_token=resp["access_token"],
        expires_at=time.time() + int(resp.get("expires_in", 3600)),
        refresh_token=resp.get("refresh_token") or old_refresh,
        scope=resp.get("scope", ""),
        account=id_token_account(resp.get("id_token")),
        provider=provider.name,
    )


def _free_port(host: str) -> int:
    with socket.socket() as s:
        s.bind((host if host != "localhost" else "127.0.0.1", 0))
        return s.getsockname()[1]


# ---------------------------------------------------------------- Loopback-Ablauf
_PAGE = """<!doctype html><html lang="{lang}"><meta charset="utf-8"><title>FED</title>
<body style="font-family:system-ui;margin:4em;color:#16181D"><h1>{title}</h1><p>{text}</p></body></html>"""


def authorize(
    provider: OAuthProvider,
    client_id: str,
    client_secret: str = "",
    login_hint: str = "",
    cancel: CancelToken | None = None,
    timeout_s: float = 300,
    open_browser: Callable[[str], object] = webbrowser.open,
    proxy: str = "",
) -> Token:
    """Interaktive Anmeldung im System-Browser; liefert ein Token."""
    if not client_id:
        raise OAuthError(t("Keine Client-ID für {provider} in der config.toml – siehe docs/oauth-einrichtung.md", provider=provider.name))
    cancel = cancel or CancelToken()
    verifier, challenge = pkce_pair()
    state = secrets.token_urlsafe(24)
    port = _free_port(provider.redirect_host)
    redirect_uri = f"http://{provider.redirect_host}:{port}/"
    result: dict = {}
    done = threading.Event()

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(self.path).query))
            if not q:
                self.send_response(404)
                self.end_headers()
                return
            if q.get("state") != state:
                result["error"] = t("state stimmt nicht überein (möglicher Angriff) – Anmeldung verworfen")
            elif "error" in q:
                result["error"] = f"{q['error']}: {q.get('error_description', '')}"
            else:
                result["code"] = q.get("code")
            ok = "code" in result
            body = _PAGE.format(
                lang=current_language(),
                title=t("Anmeldung abgeschlossen") if ok else t("Anmeldung fehlgeschlagen"),
                text=t("Sie können dieses Fenster schließen und zu FED zurückkehren.") if ok else result.get("error", ""),
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            done.set()

        def log_message(self, *args):  # Konsole sauber halten
            pass

    server = http.server.HTTPServer(("127.0.0.1", port), Handler)
    server.timeout = 0.5
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": " ".join(provider.scopes),
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        **dict(provider.extra_auth_params),
    }
    if login_hint:
        params["login_hint"] = login_hint
    url = f"{provider.auth_url}?{urllib.parse.urlencode(params)}"
    try:
        threading.Thread(target=open_browser, args=(url,), daemon=True).start()
        deadline = time.monotonic() + timeout_s
        while not done.is_set():
            if cancel.is_cancelled:
                raise Cancelled()
            if time.monotonic() > deadline:
                raise OAuthError(t("Zeitüberschreitung bei der Anmeldung im Browser"))
            server.handle_request()
    finally:
        server.server_close()
    if "error" in result:
        raise OAuthError(result["error"])
    data = {"grant_type": "authorization_code", "code": result["code"], "redirect_uri": redirect_uri,
            "client_id": client_id, "code_verifier": verifier}
    if client_secret:
        data["client_secret"] = client_secret
    return _token_from_response(_post(provider.token_url, data, proxy), provider)


def refresh(provider: OAuthProvider, token: Token, client_id: str, client_secret: str = "", proxy: str = "") -> Token:
    if not token.refresh_token:
        raise OAuthError(t("Kein Refresh-Token – erneute Anmeldung nötig"))
    data = {"grant_type": "refresh_token", "refresh_token": token.refresh_token, "client_id": client_id}
    if client_secret:
        data["client_secret"] = client_secret
    new = _token_from_response(_post(provider.token_url, data, proxy), provider, token.refresh_token)
    new.account = new.account or token.account
    return new


# ---------------------------------------------------------------- Gerätecode (Microsoft)
@dataclass
class DeviceCode:
    user_code: str
    verification_uri: str
    message: str
    device_code: str = field(repr=False)
    interval: int = 5
    expires_at: float = 0.0


def device_code_start(provider: OAuthProvider, client_id: str, proxy: str = "") -> DeviceCode:
    if not provider.device_url:
        raise OAuthError(t("{provider} unterstützt hier keinen Gerätecode", provider=provider.name))
    if not client_id:
        raise OAuthError(t("Keine Client-ID für {provider} in der config.toml – siehe docs/oauth-einrichtung.md", provider=provider.name))
    r = _post(provider.device_url, {"client_id": client_id, "scope": " ".join(provider.scopes)}, proxy)
    if "device_code" not in r:
        raise OAuthError(t("Gerätecode: {error}", error=f"{r.get('error', 'error')} – {r.get('error_description', '')}"))
    return DeviceCode(r["user_code"], r.get("verification_uri") or r.get("verification_url", ""),
                      r.get("message", ""), r["device_code"], int(r.get("interval", 5)),
                      time.time() + int(r.get("expires_in", 900)))


def device_code_poll(provider: OAuthProvider, dc: DeviceCode, client_id: str,
                     cancel: CancelToken | None = None, proxy: str = "",
                     sleep: Callable[[float], None] = time.sleep) -> Token:
    cancel = cancel or CancelToken()
    interval = dc.interval
    while time.time() < dc.expires_at:
        cancel.raise_if_cancelled()
        r = _post(provider.token_url, {"grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                                       "client_id": client_id, "device_code": dc.device_code}, proxy)
        if "access_token" in r:
            return _token_from_response(r, provider)
        err = r.get("error")
        if err == "authorization_pending":
            sleep(interval)
        elif err == "slow_down":
            interval += 5
            sleep(interval)
        else:
            raise OAuthError(t("Gerätecode: {error}", error=f"{err} – {r.get('error_description', '')}"))
    raise OAuthError(t("Gerätecode abgelaufen"))


def provider_for(name: str, tenant: str = "organizations") -> OAuthProvider:
    if name == "google":
        return GOOGLE
    if name == "microsoft":
        return microsoft(tenant)
    raise ValueError(f"Unbekannter OAuth-Anbieter: {name}")
