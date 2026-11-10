"""Meldungen des Programmkerns in der Sprache der Oberfläche bzw. des Berichts (ohne Qt).

Quelltexte sind deutsch; Übersetzungen stehen in ``fed/i18n/core_<sprache>.json`` (Schlüssel =
deutscher Text mit ``{platzhalter}``). ``packaging/i18n.py --check`` meldet fehlende Einträge.

Werte, die in der Case-Datei gespeichert werden (z. B. DKIM-Begründung, Empfänger-Hinweis), werden
als sprachneutraler Code abgelegt und erst bei der Anzeige mit ``describe()`` übersetzt – so passt
ein englischer Bericht auch zu einer auf Deutsch durchgeführten Sicherung.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from contextvars import ContextVar
from functools import cache
from importlib import resources

SOURCE = "de"
_default_language = SOURCE
_language: ContextVar[str | None] = ContextVar("fed_language", default=None)


def set_language(lang: str) -> None:
    """Sprache der Oberfläche (gilt in allen Threads, solange kein ``language()``-Block aktiv ist)."""
    global _default_language
    _default_language = lang or SOURCE


@contextmanager
def language(lang: str):
    """Andere Sprache nur innerhalb des Blocks, z. B. für einen Bericht in Englisch."""
    token = _language.set(lang or SOURCE)
    try:
        yield
    finally:
        _language.reset(token)


def current_language() -> str:
    return _language.get() or _default_language


@cache
def catalog(lang: str) -> dict[str, str]:
    if lang == SOURCE:
        return {}
    try:
        text = (resources.files("fed") / "i18n" / f"core_{lang}.json").read_text(encoding="utf-8")
    except (FileNotFoundError, OSError):
        return {}
    return json.loads(text)


def t(text: str, /, **kw) -> str:
    """Übersetzt ``text`` (deutscher Quelltext) und setzt die Platzhalter ein."""
    lang = current_language()
    out = catalog(lang).get(text, text) if lang != SOURCE else text
    if lang not in (SOURCE, "en") and out == text:
        out = catalog("en").get(text, text)  # unvollständige Sprache: lieber Englisch als Deutsch
    return out.format(**kw) if kw else out


def N_(text: str) -> str:
    return text


# --------------------------------------------------------- gespeicherte Codes
# Code -> Anzeige-Text (deutscher Quelltext, übersetzt über den Katalog)
CODES = {
    # DKIM-Begründung (message.dkim_detail)
    "body_hash_mismatch": N_("Text verändert (Body-Hash stimmt nicht)"),
    "signature_invalid": N_("Signatur ungültig (Inhalt oder signierte Kopfzeilen verändert)"),
    "dns_unreachable": N_("DNS nicht erreichbar: {0}"),
    "key_not_in_dns": N_("Schlüssel nicht im DNS ({0})"),
    # Empfänger-Hinweis (message.recipient_note)
    "bcc_only": N_("nur BCC"),
    "no_to_cc": N_("kein To/CC"),
    "undisclosed-recipients": N_("undisclosed-recipients"),
    # Herkunft der Servereinstellungen (account.autoconfig_source)
    "manual": N_("manuell"),
    "guessed": N_("geraten"),
    # Prüfung des ZIP (ZipResult.problems, Log zip_verified)
    "crc_error": N_("ZIP-Prüfsumme (CRC) fehlerhaft"),
    "missing_in_zip": N_("fehlt im ZIP: {0}"),
    "unexpected_in_zip": N_("unerwartet im ZIP: {0}"),
    "hash_mismatch": N_("Hash abweichend: {0}"),
    "not_in_case": N_("nicht in der Fall-Datei, nicht übernommen: {0}"),
}
# Werte älterer Case-Dateien (vor den Codes, deutsch gespeichert)
LEGACY = {
    "Text verändert (Body-Hash stimmt nicht)": "body_hash_mismatch",
    "Signatur ungültig (Inhalt oder signierte Kopfzeilen verändert)": "signature_invalid",
    "nur BCC": "bcc_only",
    "kein To/CC": "no_to_cc",
    "manuell": "manual",
    "geraten": "guessed",
}
LEGACY_PREFIX = {"DNS nicht erreichbar: ": "dns_unreachable", "Schlüssel nicht im DNS (": "key_not_in_dns"}


def code(name: str, arg: str | None = None) -> str:
    """Sprachneutraler Wert für die Case-Datei, z. B. ``key_not_in_dns: sel._domainkey.example``."""
    assert name in CODES, name
    return f"{name}: {arg}" if arg is not None else name


def describe(value: str | None) -> str:
    """Gespeicherten Code (oder alten deutschen Wert) in der aktuellen Sprache anzeigen."""
    if not value:
        return ""
    if value in LEGACY:
        value = LEGACY[value]
    for prefix, name in LEGACY_PREFIX.items():
        if value.startswith(prefix):
            value = f"{name}: {value[len(prefix):].rstrip(')') if name == 'key_not_in_dns' else value[len(prefix):]}"
    name, sep, arg = value.partition(": ")
    if name in CODES:
        return t(CODES[name]).format(arg) if sep else t(CODES[name])
    return value  # z. B. englischer Originaltext von dkimpy
