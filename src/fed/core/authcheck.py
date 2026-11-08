"""Echtheitsprüfung der gesicherten Mails: DKIM-Signaturen und Authentication-Results.

Warum bei der Sicherung: Anbieter tauschen DKIM-Schlüssel aus. Monate später ist der öffentliche
Schlüssel oft nicht mehr im DNS – die Signatur ist dann nicht mehr prüfbar. FED prüft deshalb beim
Sichern und speichert die abgefragten DNS-Einträge (mit Abrufzeit und SHA-256) in der Case-Datei.

Ergebnis je Mail (``dkim``):

- ``pass``       mindestens eine Signatur gültig
- ``fail``       signiert, aber keine Signatur gültig (Inhalt oder Kopf verändert, Schlüssel passt nicht)
- ``permerror``  Signatur oder Schlüssel fehlerhaft bzw. Schlüssel nicht (mehr) im DNS
- ``temperror``  DNS nicht erreichbar – später erneut prüfbar
- ``none``       nicht signiert

Zusätzlich wird der oberste Kopf ``Authentication-Results`` ausgewertet: das Urteil des
empfangenden Anbieters zu SPF, DKIM und DMARC zum Empfangszeitpunkt (SPF lässt sich nachträglich
nicht selbst prüfen, dafür fehlt die IP des einliefernden Servers).
"""

from __future__ import annotations

import hashlib
import re
import threading
from dataclasses import dataclass, field
from email.parser import BytesHeaderParser
from email.policy import compat32

from fed.core.case import utc_now

# Für Tests/Validierung: feste DNS-Antworten statt echter Abfragen ({"sel._domainkey.domain": "v=DKIM1; …"})
DNS_OVERRIDE: dict[str, str] | None = None


class DnsUnavailable(Exception):
    """DNS nicht erreichbar (Zeitüberschreitung, kein Netz)."""


@dataclass
class DnsRecord:
    name: str
    record: str | None  # TXT-Inhalt, None = nicht vorhanden
    status: str  # ok | nxdomain | error
    fetched_utc: str
    error: str = ""

    @property
    def sha256(self) -> str | None:
        return hashlib.sha256(self.record.encode()).hexdigest() if self.record is not None else None


class DnsCache:
    """Fragt jeden DKIM-Schlüssel einmal ab (thread-sicher) und merkt sich die Antworten."""

    def __init__(self, timeout: float = 5.0) -> None:
        self.timeout = timeout
        self.records: dict[str, DnsRecord] = {}
        self._lock = threading.Lock()

    def lookup(self, name: str) -> DnsRecord:
        name = name.rstrip(".").lower()
        with self._lock:
            if name in self.records:
                return self.records[name]
        rec = self._query(name)
        with self._lock:
            return self.records.setdefault(name, rec)

    def _query(self, name: str) -> DnsRecord:
        now = utc_now()
        if DNS_OVERRIDE is not None:
            txt = DNS_OVERRIDE.get(name)
            return DnsRecord(name, txt, "ok" if txt is not None else "nxdomain", now)
        import dns.exception
        import dns.resolver

        try:
            ans = dns.resolver.resolve(name, "TXT", lifetime=self.timeout)
        except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer):
            return DnsRecord(name, None, "nxdomain", now)
        except (dns.exception.Timeout, dns.resolver.NoNameservers, OSError) as e:
            return DnsRecord(name, None, "error", now, str(e) or type(e).__name__)
        txt = "".join(b.decode("utf-8", "replace") for r in ans for b in r.strings)
        return DnsRecord(name, txt, "ok", now)

    def dnsfunc(self, name: bytes, timeout: float = 5) -> bytes | None:
        """Schnittstelle für dkimpy."""
        rec = self.lookup(name.decode("ascii", "replace"))
        if rec.status == "error":
            raise DnsUnavailable(rec.error)
        return rec.record.encode() if rec.record is not None else None


@dataclass
class AuthResult:
    dkim: str = "none"  # pass | fail | permerror | temperror | none
    dkim_domain: str = ""  # Domain (d=) der gültigen bzw. ersten Signatur
    dkim_selector: str = ""
    dkim_detail: str = ""
    signatures: int = 0
    provider: dict = field(default_factory=dict)  # authserv, spf, dkim, dmarc, arc (aus Authentication-Results)


_TAG = re.compile(r"(?:^|;)\s*([a-z])\s*=\s*([^;]*)", re.IGNORECASE)
_AR_METHOD = re.compile(r"\b(spf|dkim|dmarc|arc)\s*=\s*([a-z]+)", re.IGNORECASE)


def _tags(sig_header: str) -> dict[str, str]:
    value = re.sub(r"\s+", "", sig_header.split(":", 1)[-1])
    return {k.lower(): v for k, v in _TAG.findall(value)}


def parse_authentication_results(raw: bytes) -> dict:
    """Oberster ``Authentication-Results``-Kopf (vom empfangenden Anbieter zuletzt eingefügt)."""
    msg = BytesHeaderParser(policy=compat32).parsebytes(raw)
    values = msg.get_all("Authentication-Results") or []
    if not values:
        return {}
    v = re.sub(r"\s+", " ", str(values[0])).strip()
    out = {"authserv": v.split(";", 1)[0].strip()}
    for method, result in _AR_METHOD.findall(v):
        out.setdefault(method.lower(), result.lower())
    return out


def check(raw: bytes, dns: DnsCache) -> AuthResult:
    """DKIM aller Signaturen prüfen und Authentication-Results auslesen."""
    import dkim

    res = AuthResult(provider=parse_authentication_results(raw))
    msg = BytesHeaderParser(policy=compat32).parsebytes(raw)
    sigs = msg.get_all("DKIM-Signature") or []
    res.signatures = len(sigs)
    if not sigs:
        return res
    first = _tags("x:" + str(sigs[0]))
    res.dkim_domain, res.dkim_selector = first.get("d", ""), first.get("s", "")
    outcomes = []
    for idx, s in enumerate(sigs):
        tags = _tags("x:" + str(s))
        d = dkim.DKIM(raw)
        try:
            ok = d.verify(idx=idx, dnsfunc=dns.dnsfunc)
        except DnsUnavailable as e:
            outcomes.append(("temperror", tags, f"DNS: {e}"))
            continue
        except (dkim.KeyFormatError, dkim.ParameterError, dkim.MessageFormatError) as e:
            outcomes.append(("permerror", tags, str(e)))
            continue
        except dkim.ValidationError as e:
            text = str(e)
            if "body hash mismatch" in text:
                text = "Text verändert (Body-Hash stimmt nicht)"
            outcomes.append(("permerror" if "public key" in text.lower() else "fail", tags, text))
            continue
        except Exception as e:  # dkimpy wirft je nach Fehler unterschiedliche Typen
            outcomes.append(("permerror", tags, f"{type(e).__name__}: {e}"))
            continue
        if ok:
            outcomes.append(("pass", tags, ""))
            continue
        # dkimpy meldet manche Fälle nur mit False – anhand der DNS-Antwort unterscheiden
        rec = dns.records.get(f"{tags.get('s', '')}._domainkey.{tags.get('d', '')}".lower())
        if rec is not None and rec.status == "error":
            outcomes.append(("temperror", tags, f"DNS nicht erreichbar: {rec.error}"))
        elif rec is not None and rec.record is None:
            outcomes.append(("permerror", tags, f"Schlüssel nicht im DNS ({rec.name})"))
        else:
            outcomes.append(("fail", tags, "Signatur ungültig (Inhalt oder signierte Kopfzeilen verändert)"))
    for rank in ("pass", "fail", "permerror", "temperror"):
        hit = next((o for o in outcomes if o[0] == rank), None)
        if hit:
            res.dkim, tags, res.dkim_detail = hit
            res.dkim_domain, res.dkim_selector = tags.get("d", res.dkim_domain), tags.get("s", res.dkim_selector)
            break
    return res
