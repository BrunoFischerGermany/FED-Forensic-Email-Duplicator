"""OAuth2-Anmeldung für Google und Microsoft 365 (Authorization Code + PKCE).

Ablauf: System-Browser öffnen, Redirect auf ``http://127.0.0.1:<port>``,
Code gegen Token tauschen, Token per ``AUTHENTICATE XOAUTH2`` an IMAP.
Tokens bleiben nur im Arbeitsspeicher. Client-IDs kommen aus der Config
(je Agentur eigene Registrierung, siehe docs/oauth-einrichtung.md).
"""

from __future__ import annotations

import base64
import hashlib
import secrets
from dataclasses import dataclass


@dataclass(frozen=True)
class OAuthProvider:
    name: str
    auth_url: str
    token_url: str
    scopes: tuple[str, ...]


GOOGLE = OAuthProvider(
    name="google",
    auth_url="https://accounts.google.com/o/oauth2/v2/auth",
    token_url="https://oauth2.googleapis.com/token",
    scopes=("https://mail.google.com/",),
)


def microsoft(tenant: str = "organizations") -> OAuthProvider:
    return OAuthProvider(
        name="microsoft",
        auth_url=f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/authorize",
        token_url=f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token",
        scopes=("https://outlook.office.com/IMAP.AccessAsUser.All", "offline_access"),
    )


def pkce_pair() -> tuple[str, str]:
    """(code_verifier, code_challenge) nach RFC 7636, Methode S256."""
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def xoauth2_string(user: str, access_token: str) -> bytes:
    """Initiale Client-Antwort für ``AUTHENTICATE XOAUTH2`` (vor Base64)."""
    return f"user={user}\x01auth=Bearer {access_token}\x01\x01".encode()


def authorize(provider: OAuthProvider, client_id: str, client_secret: str = "", login_hint: str = "") -> str:
    """Interaktive Anmeldung im Browser, liefert ein Access-Token."""
    raise NotImplementedError("OAuth-Ablauf folgt in Meilenstein 3")
