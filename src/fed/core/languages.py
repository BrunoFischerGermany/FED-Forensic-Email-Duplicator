"""Verfügbare Sprachen – ermittelt aus den Dateien in ``fed/i18n``.

Oberfläche: Deutsch ist die Quellsprache, jede weitere Sprache bringt ``fed_<code>.qm`` mit.
Bericht: je Sprache ``report_<code>.json``. Neue Sprachen: siehe docs/translating.md.
"""

from __future__ import annotations

from importlib import resources

SOURCE = "de"
FALLBACK = "en"


def _files() -> list[str]:
    try:
        return [f.name for f in (resources.files("fed") / "i18n").iterdir()]
    except (FileNotFoundError, NotADirectoryError):
        return []


def ui_languages() -> list[str]:
    """Sprachen der Oberfläche, z. B. ``["de", "en", "fr"]``."""
    codes = {n[4:-3] for n in _files() if n.startswith("fed_") and n.endswith(".qm")}
    return [SOURCE, *sorted(codes - {SOURCE})]


def report_languages() -> list[str]:
    """Sprachen des Berichts, z. B. ``["de", "en"]``."""
    return sorted(n[7:-5] for n in _files() if n.startswith("report_") and n.endswith(".json"))


def report_language_for(language: str) -> str:
    """Berichtssprache für eine Oberflächensprache (ohne eigene Berichtstexte: Englisch)."""
    return language if language in report_languages() else FALLBACK
