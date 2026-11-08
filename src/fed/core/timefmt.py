"""Zeitangaben einheitlich: UTC (maßgeblich) und zusätzlich Ortszeit des Rechners.

Die Ortszeit wird mit den Zeitzonenregeln des Systems für das jeweilige Datum
berechnet (Sommer-/Winterzeit korrekt). Ist keine lokale Zeitzone ermittelbar,
wird nur UTC angegeben.
"""

from __future__ import annotations

from datetime import UTC, datetime


def parse(value: str | datetime | None) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        try:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def local(value) -> datetime | None:
    dt = parse(value)
    if dt is None:
        return None
    try:
        return dt.astimezone()
    except (OverflowError, OSError, ValueError):
        return None


# Kurznamen der Zeitzonen je Sprache (Oberfläche: set_language; Bericht: with language(...))
ZONE_NAMES = {"de": {"CET": "MEZ", "CEST": "MESZ"}}
# Sprache wie die Meldungen des Kerns (fed.core.msg): Oberfläche bzw. Bericht
from fed.core.msg import current_language, language, set_language  # noqa: E402, F401


def offset(dt: datetime) -> str:
    """``+01:00`` bzw. ``MEZ +01:00`` (deutsch) / ``CET +01:00`` (englisch), wenn ein kurzer Zonenname bekannt ist."""
    off = dt.strftime("%z")
    off = f"{off[:3]}:{off[3:]}" if len(off) == 5 else off
    name = dt.tzname() or ""
    name = ZONE_NAMES.get(current_language(), {}).get(name, name)
    return f"{name} {off}" if name and len(name) <= 5 and not name.startswith(("+", "-", "UTC")) else off


def utc_str(value, seconds: bool = True) -> str:
    dt = parse(value)
    if dt is None:
        return "–"
    return dt.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S" if seconds else "%Y-%m-%d %H:%M") + " UTC"


def local_str(value, with_date: bool = True) -> str:
    lt = local(value)
    if lt is None:
        return ""
    return lt.strftime("%Y-%m-%d %H:%M:%S" if with_date else "%H:%M:%S") + " " + offset(lt)


def both(value, sep: str = " / ") -> str:
    """``2026-11-07 08:57:37 UTC / 2026-11-07 09:57:37 MEZ +01:00`` (lokal entfällt, wenn nicht ermittelbar)."""
    u = utc_str(value)
    loc = local_str(value)
    return f"{u}{sep}{loc}" if loc and u != "–" else u


def both_short(value) -> str:
    """Kompakt für Tabellen: UTC mit Datum, darunter die lokale Uhrzeit (Datum nur, wenn abweichend)."""
    dt = parse(value)
    if dt is None:
        return "–"
    lt = local(value)
    u = utc_str(dt)
    if lt is None:
        return u
    same_day = lt.date() == dt.astimezone(UTC).date()
    return f"{u}\n{local_str(dt, with_date=not same_day)}"
