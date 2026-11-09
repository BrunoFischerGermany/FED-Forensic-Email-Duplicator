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
from fed.core.msg import t


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
    source: str = "manual"


KNOWN_PROVIDERS = {
    "imap.gmail.com": Provider.GOOGLE,
    "outlook.office365.com": Provider.MICROSOFT,
    "outlook.office.com": Provider.MICROSOFT,
    "imap-mail.outlook.com": Provider.MICROSOFT,
    "imap.outlook.com": Provider.MICROSOFT,
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


ISPDB = "https://autoconfig.thunderbird.net/v1.1/"


def _http_get(url: str, proxy: str, timeout: float) -> bytes | None:
    import urllib.request

    handlers = [urllib.request.ProxyHandler({"http": proxy, "https": proxy})] if proxy else []
    opener = urllib.request.build_opener(*handlers)
    req = urllib.request.Request(url, headers={"User-Agent": "FED-ForensicEmailDuplicator"})
    try:
        with opener.open(req, timeout=timeout) as r:
            if r.status == 200:
                return r.read(512 * 1024)
    except Exception:
        return None
    return None


def _dns(name: str, rtype: str) -> list:
    try:
        import dns.resolver
    except ImportError:
        return []
    try:
        return list(dns.resolver.resolve(name, rtype, lifetime=5))
    except Exception:
        return []


def _port_open(host: str, port: int, timeout: float) -> bool:
    import socket

    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def discover(
    email: str,
    proxy: str = "",
    timeout: float = 8.0,
    http_get=None,
    dns_lookup=None,
    port_open=None,
) -> tuple[ImapSettings | None, list[str]]:
    """Führt die Autoerkennung aus. Rückgabe: (Einstellungen oder None, Protokoll je Versuch).

    ``http_get``, ``dns_lookup`` und ``port_open`` sind für Tests austauschbar.
    """
    http_get = http_get or (lambda url: _http_get(url, proxy, timeout))
    dns_lookup = dns_lookup or _dns
    port_open = port_open or (lambda h, p: _port_open(h, p, timeout))
    log: list[str] = []
    from urllib.parse import quote

    from fed.core.naming import email_domain, normalize_email

    email = normalize_email(email)
    domain = email_domain(email)

    def try_xml(step: int, label: str, url: str) -> ImapSettings | None:
        data = http_get(url)
        if data:
            try:
                s = parse_autoconfig_xml(data, email)
            except ET.ParseError:
                s = None
            if s:
                s.source = label
                log.append(t("{step}. {label}: gefunden ({url})", step=step, label=label, url=url))
                return s
        log.append(t("{step}. {label}: nicht gefunden ({url})", step=step, label=label, url=url))
        return None

    s = (try_xml(1, "autoconfig subdomain", f"https://autoconfig.{domain}/mail/config-v1.1.xml?emailaddress={quote(email, safe='@')}")
         or try_xml(2, "well-known", f"https://{domain}/.well-known/autoconfig/mail/config-v1.1.xml")
         or try_xml(3, "Thunderbird-ISPDB", f"{ISPDB}{domain}"))
    if s:
        return s, log

    mx = sorted(dns_lookup(domain, "MX"), key=lambda r: getattr(r, "preference", 0))
    if mx:
        mx_host = str(mx[0].exchange).rstrip(".").lower()
        parts = mx_host.split(".")
        mx_domain = ".".join(parts[-2:]) if len(parts) >= 2 else mx_host
        s = try_xml(4, f"MX ({mx_host}) -> ISPDB", f"{ISPDB}{mx_domain}")
        if s:
            return s, log
    else:
        log.append(t("4. MX: kein Eintrag"))

    srv = sorted(dns_lookup(f"_imaps._tcp.{domain}", "SRV"), key=lambda r: getattr(r, "priority", 0))
    if srv and str(srv[0].target).rstrip(".") not in ("", "."):
        host = str(srv[0].target).rstrip(".")
        log.append(t("5. DNS-SRV: gefunden ({host}:{port})", host=host, port=srv[0].port))
        return ImapSettings(host, int(srv[0].port), "ssl", email,
                            KNOWN_PROVIDERS.get(host.lower(), Provider.GENERIC), "DNS-SRV"), log
    log.append(t("5. DNS-SRV _imaps._tcp: nicht gefunden"))

    for host in (f"imap.{domain}", f"mail.{domain}"):
        if port_open(host, 993):
            log.append(t("6. Raten: {host}:993 erreichbar", host=host))
            return ImapSettings(host, 993, "ssl", email, Provider.GENERIC, "guessed"), log
    log.append(t("6. Raten: imap./mail. nicht erreichbar"))
    return None, log
