"""Dateinamen und Pfade im ZIP.

Schema der .eml-Dateien: ``<Eingangszeit UTC>_UID<8 Stellen>.eml``,
z. B. ``20261106T143012Z_UID00004711.eml``. Kein Betreff im Namen.

Pfad im ZIP: ``<E-Mail-Adresse>/<IMAP-Ordner>/<Dateiname>``.
"""

from __future__ import annotations

import base64
import re
import string
import unicodedata
from datetime import UTC, datetime
from fed.core.msg import t

# Nicht erlaubt in Datei-/Ordnernamen (Windows, macOS, Linux, ZIP); „/“ und „\“ trennen Ordner
_INVALID = re.compile(r'[<>:"|?*/\\\x00-\x1f\x7f]')
_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def normalize_email(address: str) -> str:
    """Leerzeichen entfernen, Unicode-Normalform NFC (macOS liefert Umlaute teils zerlegt)."""
    return unicodedata.normalize("NFC", address.strip())


def idna_host(host: str) -> str:
    """Hostname/Domain mit Umlauten in die ASCII-Form für DNS und TLS (IDNA 2008/UTS 46).

    ``ärzte.de`` -> ``xn--rzte-koa.de``, ``straße.de`` -> ``xn--strae-oqa.de`` (IDNA 2003
    würde fälschlich ``strasse.de`` liefern). Reine ASCII-Namen bleiben unverändert.
    """
    host = unicodedata.normalize("NFC", host.strip()).rstrip(".")
    if host.isascii():
        return host.lower()
    try:
        import idna

        return idna.encode(host, uts46=True).decode("ascii")
    except ImportError:
        return host.encode("idna").decode("ascii").lower()
    except idna.IDNAError as e:
        raise ValueError(t("Ungültiger Domainname: {host} ({error})", host=host, error=e)) from e


def email_domain(address: str) -> str:
    """ASCII-Domain einer E-Mail-Adresse (für DNS, Autoconfig, Serversuche)."""
    return idna_host(address.rsplit("@", 1)[-1])


def next_evidence(existing: list[str]) -> str:
    """Vorschlag für die nächste Evidence-Nummer: letzte Zahl im zuletzt vergebenen Wert hochzählen.

    ``A-01`` -> ``A-02``, ``VG-2026-009`` -> ``VG-2026-010``, ``Asservat 3a`` -> ``Asservat 4a``,
    ``A-99`` -> ``A-100``. Führende Nullen bleiben erhalten; schon vergebene Werte werden übersprungen.
    Ohne Ziffer im letzten Wert: leerer Vorschlag.
    """
    used = {e.strip().casefold() for e in existing if e.strip()}
    last = next((e.strip() for e in reversed(existing) if e.strip()), "")
    m = list(re.finditer(r"\d+", last))
    if not m:
        return ""
    m = m[-1]
    n, width = int(m.group()), len(m.group())
    for _ in range(10_000):
        n += 1
        cand = f"{last[:m.start()]}{n:0{width}d}{last[m.end():]}"
        if cand.casefold() not in used:
            return cand
    return ""


def eml_filename(internaldate: datetime, uid: int) -> str:
    """Dateiname einer Mail aus Eingangszeit und UID."""
    if internaldate.tzinfo is None:
        raise ValueError("internaldate muss zeitzonenbehaftet sein")
    ts = internaldate.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"{ts}_UID{uid:08d}.eml"


def sanitize_component(name: str) -> str:
    """Macht einen Namen als *einen* Ordner- oder Dateinamen sicher (Windows, macOS, Linux, ZIP):
    ``/`` und ``\\`` würden sonst Unterordner erzeugen, z. B. Aktenzeichen ``VG-1234/26`` ->
    ``VG-1234_26``. Das Original bleibt in Case-Datei, Log und Bericht erhalten."""
    cleaned = _INVALID.sub("_", name.strip()).rstrip(" .")
    if not cleaned:
        cleaned = "_"
    if cleaned.split(".")[0].upper() in _RESERVED:
        cleaned = f"_{cleaned}"
    return cleaned


def file_name(text: str, spaces_to_underscore: bool = True) -> str:
    """Name für Case-Ordner und Ausgabedateien (aus Casenumber/Evidence-Nummer): wie
    :func:`sanitize_component`, auf Wunsch zusätzlich Leerzeichen -> ``_``
    (``VG-1234/26, EV-1`` -> ``VG-1234_26,_EV-1``). Mail-Ordner im ZIP bleiben unberührt."""
    name = sanitize_component(text)
    return re.sub(r"\s+", "_", name) if spaces_to_underscore else name


def folder_to_zip_path(folder: str, delimiter: str | None) -> str:
    """IMAP-Ordnerpfad (dekodiert) in ZIP-Pfad mit ``/`` umwandeln."""
    parts = folder.split(delimiter) if delimiter else [folder]
    return "/".join(sanitize_component(p) for p in parts)


def zip_member_path(account: str, folder: str, delimiter: str | None, filename: str) -> str:
    return f"{sanitize_component(account)}/{folder_to_zip_path(folder, delimiter)}/{filename}"


def decode_imap_utf7(raw: bytes | str) -> str:
    """Dekodiert Modified UTF-7 (RFC 3501, 5.1.3), z. B. ``Entw&APw-rfe`` -> ``Entwürfe``."""
    s = raw.decode("ascii") if isinstance(raw, bytes) else raw
    out: list[str] = []
    i = 0
    while i < len(s):
        ch = s[i]
        if ch != "&":
            out.append(ch)
            i += 1
            continue
        end = s.index("-", i)
        chunk = s[i + 1 : end]
        if chunk == "":
            out.append("&")
        else:
            b64 = chunk.replace(",", "/")
            b64 += "=" * (-len(b64) % 4)
            out.append(base64.b64decode(b64).decode("utf-16-be"))
        i = end + 1
    return "".join(out)


class _PatternFormatter(string.Formatter):
    """``{jahr:2}`` = die letzten 2 Ziffern des Jahres (2026 -> 26); sonst normales ``format``."""

    def format_field(self, value, format_spec):
        if isinstance(value, _Year) and format_spec.isdigit() and not format_spec.startswith("0"):
            return str(value.year)[-int(format_spec):]
        return super().format_field(int(value) if isinstance(value, _Year) else value, format_spec)


class _Year(int):
    @property
    def year(self) -> int:
        return int(self)


def suggest_case_number(pattern: str, when: datetime | None = None, lfd: int = 1) -> str:
    """Vorschlag für ein neues Aktenzeichen aus dem Muster der Einstellungen.

    Platzhalter: ``{jahr}`` (2026), ``{jahr:2}`` (26), ``{monat}`` (03), ``{tag}`` (09),
    ``{lfd}`` bzw. ``{lfd:04}`` (laufende Nummer, 0001). Ungültiges Muster -> ``ValueError``.
    """
    when = when or datetime.now()
    try:
        return _PatternFormatter().format(pattern, jahr=_Year(when.year), monat=f"{when.month:02d}",
                                          tag=f"{when.day:02d}", lfd=lfd)
    except (KeyError, ValueError, IndexError, AttributeError) as e:
        raise ValueError(str(e)) from e
