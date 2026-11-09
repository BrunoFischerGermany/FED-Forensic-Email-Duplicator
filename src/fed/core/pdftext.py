"""Schriften und Schreibrichtung für den PDF-Bericht (fpdf2).

* Ersatzschriften für Zeichen, die DejaVu nicht hat: Chinesisch/Japanisch (Noto Sans SC – enthält
  auch Hiragana, Katakana und japanische Kanji), Myanmar, Arabisch, Hebräisch, Emojis.
* Textformung (HarfBuzz über ``uharfbuzz``): verbundene arabische Buchstaben, Myanmar-Silben,
  Rechts-nach-links für Arabisch und Hebräisch.
* Korrektur für fpdf2: Abschnitte gemischter Schreibrichtung werden nach Unicode-Regel L2
  umgeordnet. fpdf2 2.9 setzt sie in logischer Reihenfolge – in einer deutschen Zeile stand eine
  Zahl nach arabischem Text auf der falschen Seite („Rechnung فاتورة 2026“). Geprüft in
  ``tests/test_intl.py`` gegen fpdf2s eigene, korrekte ``get_reordered_string()``.
"""

from __future__ import annotations

import logging
from importlib import resources

FALLBACK_FONTS = [  # Reihenfolge = Vorrang
    ("NotoArabic", "NotoSansArabic-Regular.ttf"),
    ("NotoHebrew", "NotoSansHebrew-Regular.ttf"),
    ("NotoMyanmar", "NotoSansMyanmar-Regular.ttf"),
    ("NotoCJK", "NotoSansSC-Regular.ttf"),
    ("NotoEmoji", "NotoEmoji-Regular.ttf"),
]
_log = logging.getLogger(__name__)
_patched = False


def shaping_available() -> bool:
    try:
        import uharfbuzz  # noqa: F401
    except ImportError:
        return False
    return True


def _split_bidi_fragments_l2(self):
    """Ersatz für ``BidiParagraph.split_bidi_fragments``: Läufe gleicher Einbettungsebene bilden und
    nach Regel L2 umordnen (nur oberhalb der Absatzebene – die Absatzrichtung selbst setzt fpdf2)."""
    from fpdf.enums import TextDirection

    if not self.characters:
        return ()
    runs: list[list] = []
    for c in self.characters:
        if runs and runs[-1][1] == c.embedding_level:
            runs[-1][0] += c.character
        else:
            runs.append([c.character, c.embedding_level])
    base = self.base_embedding_level
    for level in range(max(r[1] for r in runs), base, -1):
        out, rev = [], []
        for r in runs:
            if r[1] >= level:
                rev.append(r)
            else:
                out += reversed(rev)
                rev = []
                out.append(r)
        out += reversed(rev)
        runs = out
    result = []
    for text, level in runs:
        if self.alias and self.sentinel:
            text = text.replace(self.sentinel, self.alias)
        result.append((text, TextDirection.RTL if level % 2 else TextDirection.LTR))
    return tuple(result)


def patch_fpdf_bidi() -> None:
    global _patched
    if _patched:
        return
    from fpdf.bidi import BidiParagraph

    BidiParagraph.split_bidi_fragments = _split_bidi_fragments_l2
    _patched = True


# Schriften, die Textformung oder Rechts-nach-links brauchen (Hebräisch, Arabisch, Syrisch, Thaana,
# indische Schriften, Thai/Lao, Tibetisch, Myanmar, Khmer, Mongolisch, Präsentationsformen)
_COMPLEX = ((0x0590, 0x08FF), (0x0900, 0x0DFF), (0x0E00, 0x0EFF), (0x0F00, 0x0FFF), (0x1000, 0x109F),
            (0x1780, 0x18AF), (0xA9E0, 0xA9FF), (0xAA60, 0xAA7F), (0xFB1D, 0xFDFF), (0xFE70, 0xFEFF))


def needs_shaping(text: str) -> bool:
    """True, wenn ``text`` Zeichen enthält, die ohne Textformung falsch erschienen (z. B. Arabisch)."""
    return any(lo <= ord(ch) <= hi for ch in set(text) for lo, hi in _COMPLEX)


def setup(pdf, shaping: bool = True) -> bool:
    """Ersatzschriften registrieren und – wenn nötig und verfügbar – Textformung einschalten.
    Ohne Formung ist der Satz etwa viermal schneller; CJK und Emojis brauchen sie nicht.
    Rückgabe: Formung an?"""
    fonts = resources.files("fed") / "resources" / "fonts"
    names = []
    for name, file in FALLBACK_FONTS:
        path = fonts / file
        if path.is_file():
            pdf.add_font(name, "", str(path))
            names.append(name)
    pdf.set_fallback_fonts(names, exact_match=False)
    if not shaping:
        return False
    if shaping_available():
        patch_fpdf_bidi()
        pdf.set_text_shaping(True)
        return True
    _log.warning("uharfbuzz fehlt – Arabisch/Myanmar im PDF ohne Textformung")
    return False
