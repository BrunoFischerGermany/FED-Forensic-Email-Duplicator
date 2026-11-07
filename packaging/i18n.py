"""Übersetzungen aktualisieren: Texte einsammeln, aus ``en.json`` füllen, ``.qm`` erzeugen.

    python packaging/i18n.py           # fed_en.ts + fed_en.qm neu erzeugen
    python packaging/i18n.py --check   # nur prüfen: fehlt eine Übersetzung? (Rückgabewert 1)

Quelltexte in ``self.tr(...)`` sind Deutsch. ``src/fed/i18n/en.json`` ordnet ihnen die
englischen Texte zu; ``"Kontext|Text"`` überschreibt für eine einzelne Klasse.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
I18N = ROOT / "src" / "fed" / "i18n"
GUI = sorted((ROOT / "src" / "fed" / "gui").glob("*.py"))


def _tool(name: str) -> str:
    exe = shutil.which(f"pyside6-{name}") or shutil.which(f"pyside6-{name}", path=str(Path(sys.executable).parent))
    if not exe:
        raise SystemExit(f"pyside6-{name} nicht gefunden (pip install PySide6)")
    return exe


def collect(ts: Path) -> None:
    subprocess.run([_tool("lupdate"), "-no-obsolete", "-locations", "none", *map(str, GUI), "-ts", str(ts)],
                   check=True, capture_output=True)


def fill(ts: Path, table: dict[str, str]) -> list[tuple[str, str]]:
    tree = ET.parse(ts)
    root = tree.getroot()
    root.set("language", "en")
    root.set("sourcelanguage", "de")
    missing = []
    for ctx in root.findall("context"):
        name = ctx.findtext("name")
        for msg in ctx.findall("message"):
            src = msg.findtext("source")
            tr = msg.find("translation")
            text = table.get(f"{name}|{src}", table.get(src))
            if text is None:
                missing.append((name, src))
                tr.set("type", "unfinished")
                tr.text = ""
            else:
                tr.attrib.pop("type", None)
                tr.text = text
    tree.write(ts, encoding="utf-8", xml_declaration=True)
    return missing


def main(argv: list[str]) -> int:
    table = json.loads((I18N / "en.json").read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory() as tmp:
        ts = Path(tmp) / "fed_en.ts"
        collect(ts)
        missing = fill(ts, table)
        for ctx, src in missing:
            print(f"fehlt: [{ctx}] {src!r}")
        if "--check" in argv:
            return 1 if missing else 0
        shutil.copy(ts, I18N / "fed_en.ts")
        subprocess.run([_tool("lrelease"), str(I18N / "fed_en.ts"), "-qm", str(I18N / "fed_en.qm")],
                       check=True, capture_output=True)
    print(f"{I18N / 'fed_en.qm'} – {len(missing)} fehlende Übersetzungen")
    return 1 if missing else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
