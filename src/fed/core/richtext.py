"""Formatierte Notizen (Case-Anmerkungen, Notiz zum Konto) – ohne Qt.

Gespeichert wird ein kleines, bereinigtes HTML; alles andere wird beim Einlesen verworfen:

* Absätze ``<p>`` und Aufzählungen ``<ul><li>``
* ``<b>``, ``<i>``, ``<u>``
* ``<span style="color:#rrggbb; background-color:#rrggbb">`` (Schriftfarbe, Marker)

Daneben steht immer der reine Text (Spalte ``notes``) für TXT-Bericht, CSV, Log und ältere
Versionen. Keine Links, keine Bilder, keine Skripte – die Notiz ist Teil des Beweisberichts.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser

COLORS = {  # Auswahl im Editor (Name -> Farbe); im Bericht genauso
    "red": "#c62828",
    "orange": "#e65100",
    "green": "#2e7d32",
    "blue": "#1565c0",
}
HIGHLIGHT = "#fff176"  # Marker (gelb)
_HEX = re.compile(r"#[0-9a-fA-F]{6}")


@dataclass(frozen=True)
class Style:
    bold: bool = False
    italic: bool = False
    underline: bool = False
    color: str | None = None  # "#rrggbb"
    background: str | None = None  # "#rrggbb" – Marker


@dataclass
class Block:
    kind: str = "p"  # "p" | "li"
    runs: list[tuple[str, Style]] = field(default_factory=list)

    @property
    def text(self) -> str:
        return "".join(t for t, _ in self.runs)


def _parse_style(css: str) -> dict:
    out = {}
    for part in css.split(";"):
        key, _, value = part.partition(":")
        key, value = key.strip().lower(), value.strip()
        m = _HEX.fullmatch(value)
        if m and key == "color":
            out["color"] = value.lower()
        elif m and key in ("background-color", "background"):
            out["background"] = value.lower()
    return out


class _Parser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: list[Block] = []
        self.stack: list[Style] = [Style()]
        self.tags: list[str] = []
        self.cur: Block | None = None

    def _block(self, kind: str) -> Block:
        if self.cur is None or self.cur.runs or self.cur.kind != kind:
            if self.cur is not None and not self.cur.runs:
                self.blocks.remove(self.cur)
            self.cur = Block(kind)
            self.blocks.append(self.cur)
        return self.cur

    def handle_starttag(self, tag, attrs) -> None:
        a = dict(attrs)
        s = self.stack[-1]
        if tag in ("p", "div"):
            self.cur = None
            self._block("p")
        elif tag == "li":
            self.cur = None
            self._block("li")
        elif tag == "br":
            # Zeilenumbruch innerhalb eines Absatzes = neuer Absatz derselben Art
            kind = self.cur.kind if self.cur else "p"
            self.cur = None
            self._block(kind)
            return
        if tag in ("b", "strong"):
            s = Style(True, s.italic, s.underline, s.color, s.background)
        elif tag in ("i", "em"):
            s = Style(s.bold, True, s.underline, s.color, s.background)
        elif tag == "u":
            s = Style(s.bold, s.italic, True, s.color, s.background)
        elif tag in ("span", "font", "mark"):
            st = _parse_style(a.get("style") or "")
            if tag == "font" and a.get("color") and _HEX.fullmatch(a["color"]):
                st["color"] = a["color"].lower()
            if tag == "mark":
                st.setdefault("background", HIGHLIGHT)
            s = Style(s.bold, s.italic, s.underline, st.get("color", s.color), st.get("background", s.background))
        if tag in ("b", "strong", "i", "em", "u", "span", "font", "mark"):
            self.stack.append(s)
            self.tags.append(tag)

    def handle_endtag(self, tag) -> None:
        if tag in ("p", "div", "li"):
            self.cur = None
        elif tag in self.tags:
            while self.tags:  # bis zum passenden Tag schließen (fehlerhaftes HTML)
                t = self.tags.pop()
                self.stack.pop()
                if t == tag:
                    break

    def handle_data(self, data) -> None:
        if not data or (self.cur is None and not data.strip()):
            return
        text = re.sub(r"[\r\n\t]+", " ", data)
        blk = self.cur or self._block("p")
        blk.runs.append((text, self.stack[-1]))


def parse(value: str | None) -> list[Block]:
    """Bereinigtes Modell aus gespeichertem HTML (oder aus reinem Text, wenn kein HTML)."""
    if not value:
        return []
    if "<" not in value:
        return from_plain(value)
    p = _Parser()
    p.feed(value)
    p.close()
    blocks = []
    for b in p.blocks:
        runs = _merge(b.runs)
        if b.kind == "p" and not runs:
            runs = []  # leerer Absatz = Leerzeile
        blocks.append(Block(b.kind, runs))
    while blocks and not blocks[-1].text.strip():
        blocks.pop()
    while blocks and not blocks[0].text.strip():
        blocks.pop(0)
    return blocks


def _merge(runs: list[tuple[str, Style]]) -> list[tuple[str, Style]]:
    out: list[tuple[str, Style]] = []
    for text, style in runs:
        if out and out[-1][1] == style:
            out[-1] = (out[-1][0] + text, style)
        else:
            out.append((text, style))
    return out


def from_plain(text: str) -> list[Block]:
    lines = text.strip("\n").splitlines()
    return [Block("p", [(ln, Style())] if ln else []) for ln in lines]


def to_html(blocks: list[Block]) -> str:
    """Kanonisches, bereinigtes HTML (so wird es gespeichert)."""
    out, in_list = [], False
    for b in blocks:
        if b.kind == "li" and not in_list:
            out.append("<ul>")
            in_list = True
        elif b.kind != "li" and in_list:
            out.append("</ul>")
            in_list = False
        inner = "".join(_run_html(t, s) for t, s in b.runs)
        out.append(f"<li>{inner}</li>" if b.kind == "li" else f"<p>{inner}</p>")
    if in_list:
        out.append("</ul>")
    return "".join(out)


def _run_html(text: str, s: Style) -> str:
    h = html.escape(text, quote=False)
    css = []
    if s.color:
        css.append(f"color:{s.color}")
    if s.background:
        css.append(f"background-color:{s.background}")
    if css:
        h = f'<span style="{"; ".join(css)}">{h}</span>'
    if s.underline:
        h = f"<u>{h}</u>"
    if s.italic:
        h = f"<i>{h}</i>"
    if s.bold:
        h = f"<b>{h}</b>"
    return h


def sanitize(value: str | None) -> str:
    return to_html(parse(value)) if value else ""


def to_plain(value: str | list[Block] | None) -> str:
    """Reiner Text: Absätze als Zeilen, Aufzählungspunkte mit „• “."""
    blocks = parse(value) if isinstance(value, str) or value is None else value
    return "\n".join(("• " if b.kind == "li" else "") + b.text.rstrip() for b in blocks).strip("\n")


def is_formatted(blocks: list[Block]) -> bool:
    """True, wenn mehr als reiner Text drinsteckt (dann lohnt das HTML)."""
    return any(b.kind == "li" or any(s != Style() for _, s in b.runs) for b in blocks)


def normalize(html_value: str | None, plain_value: str | None = None) -> tuple[str, str | None]:
    """Für das Speichern: (reiner Text, bereinigtes HTML oder None, wenn unformatiert)."""
    blocks = parse(html_value) if html_value else from_plain(plain_value or "")
    plain = to_plain(blocks)
    return plain, (to_html(blocks) if is_formatted(blocks) else None)


def hex_rgb(value: str) -> tuple[int, int, int]:
    return int(value[1:3], 16), int(value[3:5], 16), int(value[5:7], 16)
