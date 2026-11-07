"""Translation tool: collect texts, fill them from ``<lang>.json``, build ``.qm`` files.

    python packaging/i18n.py               # update fed_<lang>.ts + fed_<lang>.qm for every language
    python packaging/i18n.py --check       # only check: is a translation missing? (exit code 1)
    python packaging/i18n.py --new fr      # start a new language: creates fr.json and report_fr.json

Source texts in ``self.tr(...)`` are German. ``src/fed/i18n/<lang>.json`` maps them to the
target language; a key ``"ClassName|text"`` overrides the translation for one class only.
See docs/translating.md.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
I18N = ROOT / "src" / "fed" / "i18n"
GUI = sorted((ROOT / "src" / "fed" / "gui").glob("*.py"))
SOURCE = "de"
TEMPLATE = "en"  # new languages start from the English texts


def _tool(name: str) -> str:
    exe = shutil.which(f"pyside6-{name}") or shutil.which(f"pyside6-{name}", path=str(Path(sys.executable).parent))
    if not exe:
        raise SystemExit(f"pyside6-{name} not found (pip install PySide6)")
    return exe


def languages() -> list[str]:
    return sorted(p.stem for p in I18N.glob("*.json") if re.fullmatch(r"[a-z]{2,3}(_[A-Z]{2})?", p.stem))


def collect(ts: Path) -> None:
    subprocess.run([_tool("lupdate"), "-no-obsolete", "-locations", "none", *map(str, GUI), "-ts", str(ts)],
                   check=True, capture_output=True)


def sources() -> list[tuple[str, str]]:
    with tempfile.TemporaryDirectory() as tmp:
        ts = Path(tmp) / "src.ts"
        collect(ts)
        root = ET.parse(ts).getroot()
        return [(c.findtext("name"), m.findtext("source")) for c in root.findall("context")
                for m in c.findall("message")]


def fill(ts: Path, lang: str, table: dict[str, str]) -> list[tuple[str, str]]:
    tree = ET.parse(ts)
    root = tree.getroot()
    root.set("language", lang)
    root.set("sourcelanguage", SOURCE)
    missing = []
    for ctx in root.findall("context"):
        name = ctx.findtext("name")
        for msg in ctx.findall("message"):
            src = msg.findtext("source")
            tr = msg.find("translation")
            text = table.get(f"{name}|{src}", table.get(src))
            if not text:
                missing.append((name, src))
                tr.set("type", "unfinished")
                tr.text = ""
            else:
                tr.attrib.pop("type", None)
                tr.text = text
    tree.write(ts, encoding="utf-8", xml_declaration=True)
    return missing


def check_placeholders(lang: str, table: dict[str, str]) -> list[str]:
    """``{0}``, ``{1}`` … must stay in the translation, otherwise the program fails at runtime."""
    bad = []
    for src, text in table.items():
        src_text = src.split("|", 1)[-1]
        if text and set(re.findall(r"\{\d+\}", src_text)) != set(re.findall(r"\{\d+\}", text)):
            bad.append(f"[{lang}] placeholders differ: {src_text!r} -> {text!r}")
    return bad


def check_report(lang: str) -> list[str]:
    ref = json.loads((I18N / f"report_{TEMPLATE}.json").read_text(encoding="utf-8"))
    path = I18N / f"report_{lang}.json"
    if not path.exists():
        return [f"[{lang}] report_{lang}.json missing – reports fall back to English"]
    rep = json.loads(path.read_text(encoding="utf-8"))
    out = [f"[{lang}] report text missing: {k}" for k in ref if not rep.get(k)]
    out += [f"[{lang}] report placeholders differ: {k}" for k, v in ref.items()
            if rep.get(k) and set(re.findall(r"\{\w+\}", v)) != set(re.findall(r"\{\w+\}", rep[k]))]
    return out


def new_language(code: str) -> int:
    if not re.fullmatch(r"[a-z]{2,3}(_[A-Z]{2})?", code):
        print(f"invalid language code {code!r} (examples: fr, it, pt_BR)")
        return 2
    target = I18N / f"{code}.json"
    if target.exists():
        print(f"{target} already exists")
        return 2
    base = json.loads((I18N / f"{TEMPLATE}.json").read_text(encoding="utf-8"))
    table = {src: base.get(src, "") for _ctx, src in sources()}
    table |= {k: v for k, v in base.items() if "|" in k}
    target.write_text(json.dumps(dict(sorted(table.items())), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    shutil.copy(I18N / f"report_{TEMPLATE}.json", I18N / f"report_{code}.json")
    print(f"created {target.relative_to(ROOT)} and report_{code}.json – both contain the English texts;")
    print("replace every value with the translation, then run: python packaging/i18n.py")
    return 0


def main(argv: list[str]) -> int:
    if argv[:1] == ["--new"]:
        return new_language(argv[1] if len(argv) > 1 else "")
    problems = 0
    for lang in languages():
        table = json.loads((I18N / f"{lang}.json").read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as tmp:
            ts = Path(tmp) / f"fed_{lang}.ts"
            collect(ts)
            missing = fill(ts, lang, table)
            for ctx, src in missing:
                print(f"[{lang}] missing: [{ctx}] {src!r}")
            other = check_placeholders(lang, table) + check_report(lang)
            for line in other:
                print(line)
            problems += len(missing) + len(other)
            if "--check" not in argv:
                shutil.copy(ts, I18N / f"fed_{lang}.ts")
                subprocess.run([_tool("lrelease"), str(I18N / f"fed_{lang}.ts"), "-qm",
                                str(I18N / f"fed_{lang}.qm")], check=True, capture_output=True)
                print(f"fed_{lang}.qm – {len(missing)} missing")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
