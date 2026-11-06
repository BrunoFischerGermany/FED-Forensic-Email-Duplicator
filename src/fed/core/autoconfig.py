"""Automatische Ermittlung der IMAP-Einstellungen (Thunderbird-Logik).

Reihenfolge, erster Treffer gewinnt, jeder Versuch wird geloggt:

1. ``https://autoconfig.<domain>/mail/config-v1.1.xml``
2. ``https://<domain>/.well-known/autoconfig/mail/config-v1.1.xml``
3. ISPDB ``https://autoconfig.thunderbird.net/v1.1/<domain>``
4. MX-Eintrag -> ISPDB für die MX-Domain (Google Workspace, Microsoft 365)
5. DNS-SRV ``_imaps._tcp.<domain>`` (RFC 6186)
6. Raten: ``imap.<domain>``, ``mail.<domain>`` auf Port 993
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from enum import StrEnum


class Provider(StrEnum):
    GENERIC = "generic"
    GOOGLE = "google"
    MICROSOFT = "microsoft"


@dataclass
class ImapSettings:
    host: str
    port: int = 993
    security: str = "ssl"  # ssl | starttls
    username: str = ""
    provider: Provider = Provider.GENERIC
    source: str = "manuell"


KNOWN_PROVIDERS = {
    "imap.gmail.com": Provider.GOOGLE,
    "outlook.office365.com": Provider.MICROSOFT,
}


def parse_autoconfig_xml(xml: bytes | str, email: str) -> ImapSettings | None:
    """Liest den IMAP-Eintrag (bevorzugt SSL) aus einer Thunderbird-Autoconfig-Datei."""
    root = ET.fromstring(xml)
    servers = root.findall("./emailProvider/incomingServer[@type='imap']")
    servers.sort(key=lambda s: (s.findtext("socketType") or "") != "SSL")
    for s in servers:
        host = (s.findtext("hostname") or "").strip()
        if not host:
            continue
        local, _, domain = email.partition("@")
        username = (s.findtext("username") or "%EMAILADDRESS%")
        username = (
            username.replace("%EMAILADDRESS%", email)
            .replace("%EMAILLOCALPART%", local)
            .replace("%EMAILDOMAIN%", domain)
        )
        socket_type = (s.findtext("socketType") or "SSL").upper()
        return ImapSettings(
            host=host,
            port=int(s.findtext("port") or 993),
            security="ssl" if socket_type == "SSL" else "starttls",
            username=username,
            provider=KNOWN_PROVIDERS.get(host.lower(), Provider.GENERIC),
            source="autoconfig",
        )
    return None


def discover(email: str, proxy: str = "") -> tuple[ImapSettings | None, list[str]]:
    """Führt die Autoerkennung aus. Rückgabe: (Einstellungen oder None, Protokoll)."""
    raise NotImplementedError("Autoerkennung folgt in Meilenstein 2")
