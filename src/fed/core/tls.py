"""Gemeinsames CA-Bundle für alle TLS-Verbindungen (IMAP, OAuth/HTTP, Autoconfig).

Das eigenständige Paket (Nuitka) bringt keine System-Zertifikate mit, und Python liest
sie auf macOS nicht aus dem Schlüsselbund. Ohne mitgeliefertes CA-Bundle schlägt die
Zertifikatsprüfung dann fehl ("unable to get local issuer certificate"). Deshalb bündeln
wir ``certifi`` und lassen jeden TLS-Pfad darauf zeigen.
"""
from __future__ import annotations

import os
import ssl


def ca_bundle() -> str | None:
    """Pfad zu einem CA-Bundle: ein gesetztes ``SSL_CERT_FILE``, sonst ``certifi``, sonst System."""
    env = os.environ.get("SSL_CERT_FILE")
    if env and os.path.isfile(env):
        return env
    try:
        import certifi

        path = certifi.where()
        if os.path.isfile(path):
            return path
    except Exception:
        pass
    sys_cafile = ssl.get_default_verify_paths().cafile
    return sys_cafile if sys_cafile and os.path.isfile(sys_cafile) else None


def ssl_context(cafile: str | None = None) -> ssl.SSLContext:
    """Verifizierender Default-Context; fällt ohne expliziten ``cafile`` auf das gebündelte zurück."""
    return ssl.create_default_context(cafile=cafile or ca_bundle())


def install() -> None:
    """Macht das CA-Bundle für alle Default-TLS-Contexts auffindbar (urllib, imaplib, …).

    Setzt ``SSL_CERT_FILE``/``REQUESTS_CA_BUNDLE``, sofern nicht schon vorhanden. OpenSSL liest
    ``SSL_CERT_FILE`` beim Anlegen jedes Default-Contexts, daher wirkt das prozessweit – auch für
    Stellen, die ``ssl.create_default_context()`` ohne ``cafile`` nutzen. Früh beim Start aufrufen.
    """
    bundle = ca_bundle()
    if bundle:
        os.environ.setdefault("SSL_CERT_FILE", bundle)
        os.environ.setdefault("REQUESTS_CA_BUNDLE", bundle)
