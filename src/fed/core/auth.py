"""Anmeldung am IMAP-Server: Passwort oder OAuth (Google, Microsoft 365).

``make_connector`` liefert eine Funktion, die bei jedem Aufruf eine neue,
angemeldete Verbindung herstellt. Sie wird für die erste Verbindung und für
Reconnects benutzt; ein abgelaufenes OAuth-Token wird dabei automatisch
erneuert. Passwörter und Tokens bleiben nur im Arbeitsspeicher.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field

from fed.core import oauth
from fed.core.case import Case
from fed.core.imap_reader import ReadOnlyImap
from fed.core.msg import t

AUTH_METHODS = ("password", "app_password_google", "oauth_google", "oauth_microsoft")

GMAIL_HOSTS = {"imap.gmail.com", "imap.googlemail.com"}
APP_PASSWORD_URL = "https://myaccount.google.com/apppasswords"
YAHOO_APP_PASSWORD_URL = "https://login.yahoo.com/account/security"
_APP_PW = re.compile(r"^\s*([a-z]{4})\s*([a-z]{4})\s*([a-z]{4})\s*([a-z]{4})\s*$")


def is_gmail(host: str) -> bool:
    return host.strip().lower().rstrip(".") in GMAIL_HOSTS


def is_yahoo(host: str) -> bool:
    return host.strip().lower().endswith("mail.yahoo.com")


def is_microsoft(host: str) -> bool:
    """IMAP-Server von Microsoft (Exchange Online / Outlook.com). Dort gibt es seit 2022 (Microsoft 365)
    bzw. 16.09.2024 (Outlook.com) keine Anmeldung mit Passwort oder App-Passwort mehr, nur OAuth."""
    return host.strip().lower().rstrip(".") in oauth.MICROSOFT_IMAP_HOSTS


def normalize_app_password(password: str) -> str:
    """Google zeigt App-Passwörter in Vierergruppen an (``abcd efgh ijkl mnop``); IMAP braucht
    die 16 Buchstaben ohne Leerzeichen. Andere Eingaben bleiben unverändert."""
    m = _APP_PW.match(password)
    return "".join(m.groups()) if m else password


def microsoft_password_hint() -> str:
    return t("Microsoft (Outlook.com, Hotmail, Live, Microsoft 365) nimmt für IMAP weder das Kontopasswort "
             "noch ein App-Passwort an – nur die Anmeldung per OAuth. Bitte „Microsoft 365 (OAuth)“ wählen. "
             "IMAP muss im Postfach erlaubt sein: Outlook.com → Einstellungen → E-Mail → Weiterleitung und IMAP; "
             "Microsoft 365 → Administrator (Admin Center → Benutzer → E-Mail-Apps).")


def explain_login_error(host: str, error: str) -> str:
    """Ergänzt Anmeldefehler bei Gmail/Microsoft um einen Hinweis auf App-Passwort bzw. OAuth."""
    if is_microsoft(host):
        return error + " – " + microsoft_password_hint()
    if not is_gmail(host):
        return error
    low = error.lower()
    if "application-specific password required" in low:
        return error + t(" – Google verlangt ein App-Passwort (nicht das normale Kontopasswort), "
                         "erstellen unter {url}; Voraussetzung: 2-Schritt-Bestätigung", url=APP_PASSWORD_URL)
    if "authenticationfailed" in low or "invalid credentials" in low:
        return error + t(" – bei Google nur App-Passwort (16 Buchstaben, {url}) oder OAuth; "
                         "das normale Kontopasswort wird abgelehnt. In Google Workspace muss der Administrator "
                         "App-Passwörter erlauben.", url=APP_PASSWORD_URL)
    return error


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
        raise ValueError(t("Genau eine Anmeldeart angeben: Passwort oder OAuth"))

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
        msg = t("Angemeldet als {login}, gesichert wird {target} "
                "(nur korrekt bei geteiltem Postfach oder Stellvertretung)", login=token.account, target=target_email)
        case.log("WARNING", "oauth_account_mismatch", {"login": token.account, "target": target_email})
        return msg
    return None
