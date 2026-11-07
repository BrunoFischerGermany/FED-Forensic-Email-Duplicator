"""Dateinamen und Pfade im ZIP.

Schema der .eml-Dateien: ``<Eingangszeit UTC>_UID<8 Stellen>.eml``,
z. B. ``20261106T143012Z_UID00004711.eml``. Kein Betreff im Namen.

Pfad im ZIP: ``<E-Mail-Adresse>/<IMAP-Ordner>/<Dateiname>``.
"""

from __future__ import annotations

import base64
import re
from datetime import UTC, datetime

# In Windows ungültige Zeichen und Steuerzeichen
_INVALID = re.compile(r'[<>:"|?*\\\x00-\x1f]')
_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def eml_filename(internaldate: datetime, uid: int) -> str:
    """Dateiname einer Mail aus Eingangszeit und UID."""
    if internaldate.tzinfo is None:
        raise ValueError("internaldate muss zeitzonenbehaftet sein")
    ts = internaldate.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"{ts}_UID{uid:08d}.eml"


def sanitize_component(name: str) -> str:
    """Macht einen Ordnernamen für Windows/ZIP sicher (Original bleibt im Log)."""
    cleaned = _INVALID.sub("_", name).rstrip(" .")
    if not cleaned:
        cleaned = "_"
    if cleaned.split(".")[0].upper() in _RESERVED:
        cleaned = f"_{cleaned}"
    return cleaned


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
