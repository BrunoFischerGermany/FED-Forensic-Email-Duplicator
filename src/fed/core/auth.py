"""Anmeldung am IMAP-Server: Passwort oder OAuth (Google, Microsoft 365).

``make_connector`` liefert eine Funktion, die bei jedem Aufruf eine neue,
angemeldete Verbindung herstellt. Sie wird für die erste Verbindung und für
Reconnects benutzt; ein abgelaufenes OAuth-Token wird dabei automatisch
erneuert. Passwörter und Tokens bleiben nur im Arbeitsspeicher.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from fed.core import oauth
from fed.core.case import Case
from fed.core.imap_reader import ReadOnlyImap

AUTH_METHODS = ("password", "oauth_google", "oauth_microsoft")


@dataclass
class OAuthSession:
    provider: oauth.OAuthProvider
    client_id: str
    client_secret: str = ""
    proxy: str = ""
    token: oauth.Token | None = field(default=None, repr=False)
    on_refresh: Callable[[oauth.Token], None] | None = None

    def login_interactive(self, login_hint: str = "", device_code: bool = False,
                          show_device_code: Callable[[oauth.DeviceCode], None] | None = None,
                          cancel=None, open_browser=None) -> oauth.Token:
        if device_code:
            dc = oauth.device_code_start(self.provider, self.client_id, self.proxy)
            if show_device_code:
                show_device_code(dc)
            self.token = oauth.device_code_poll(self.provider, dc, self.client_id, cancel, self.proxy)
        else:
            kw = {"open_browser": open_browser} if open_browser else {}
            self.token = oauth.authorize(self.provider, self.client_id, self.client_secret, login_hint,
                                         cancel=cancel, proxy=self.proxy, **kw)
        return self.token

    def access_token(self) -> str:
        if self.token is None:
            raise oauth.OAuthError("Nicht angemeldet")
        if self.token.expired:
            self.token = oauth.refresh(self.provider, self.token, self.client_id, self.client_secret, self.proxy)
            if self.on_refresh:
                self.on_refresh(self.token)
        return self.token.access_token


def make_connector(
    host: str,
    port: int,
    security: str,
    user: str,
    *,
    password: str | None = None,
    oauth_session: OAuthSession | None = None,
    timeout: float = 30,
    cafile: str | None = None,
) -> Callable[[], ReadOnlyImap]:
    if (password is None) == (oauth_session is None):
        raise ValueError("Genau eine Anmeldeart angeben: Passwort oder OAuth")

    def connect() -> ReadOnlyImap:
        c = ReadOnlyImap(host, port, security, timeout=timeout, cafile=cafile)
        c.connect()
        try:
            if oauth_session is not None:
                c.login_xoauth2(user, oauth_session.access_token())
            else:
                c.login_password(user, password)
        except Exception:
            c.logout()
            raise
        return c

    return connect


def log_oauth_login(case: Case, token: oauth.Token, target_email: str) -> str | None:
    """Protokolliert, welches Konto zugestimmt hat. Gibt eine Warnung zurück, wenn es abweicht."""
    case.log("INFO", "oauth_login", token.audit())
    if token.account and token.account.lower() != target_email.lower():
        msg = (f"Angemeldet als {token.account}, gesichert wird {target_email} "
               "(nur korrekt bei geteiltem Postfach oder Stellvertretung)")
        case.log("WARNING", "oauth_account_mismatch", {"login": token.account, "target": target_email})
        return msg
    return None
