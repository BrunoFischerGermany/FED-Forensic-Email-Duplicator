import re
import subprocess
import sys
from pathlib import Path

import pytest

from fed.core import msg

ROOT = Path(__file__).resolve().parent.parent


def test_translate_and_language_blocks():
    with msg.language("de"):
        assert msg.t("Konto {id} unbekannt", id=3) == "Konto 3 unbekannt"
    with msg.language("en"):
        assert msg.t("Konto {id} unbekannt", id=3) == "Account 3 unknown"
        with msg.language("de"):  # verschachtelt, z. B. deutscher Bericht aus englischer Oberfläche
            assert msg.t("Case ist abgeschlossen") == "Case ist abgeschlossen"
        assert msg.t("Case ist abgeschlossen") == "Case is closed"
    with msg.language("fr"):  # unvollständige Sprache: lieber Englisch als Deutsch
        assert msg.t("Case ist abgeschlossen") == "Case is closed"


def test_codes_and_legacy_values():
    assert msg.code("key_not_in_dns", "s._domainkey.x") == "key_not_in_dns: s._domainkey.x"
    with msg.language("en"):
        assert msg.describe("key_not_in_dns: s._domainkey.x") == "Key not in DNS (s._domainkey.x)"
        assert msg.describe("body_hash_mismatch") == "Text modified (body hash does not match)"
        assert msg.describe("bcc_only") == "BCC only"
        assert msg.describe("hash_mismatch: a/b.eml") == "Hash differs: a/b.eml"
        # Werte aus älteren, deutsch gespeicherten Case-Dateien
        assert msg.describe("nur BCC") == "BCC only"
        assert msg.describe("Schlüssel nicht im DNS (s._domainkey.x)") == "Key not in DNS (s._domainkey.x)"
        assert msg.describe("DNS nicht erreichbar: timeout") == "DNS not reachable: timeout"
        assert msg.describe("manuell") == "manual"
        assert msg.describe("unbekannter Originaltext") == "unbekannter Originaltext"
    with msg.language("de"):
        assert msg.describe("key_not_in_dns: s._domainkey.x") == "Schlüssel nicht im DNS (s._domainkey.x)"
        assert msg.describe("") == ""


def test_catalog_complete():
    sys.path.insert(0, str(ROOT / "packaging"))
    import i18n

    assert i18n.check_core("en") == []
    assert "Konto {id} unbekannt" in i18n.core_sources()


@pytest.mark.parametrize("lang", ["en", "de"])
def test_cli_help_language(lang):
    env = {"FED_LANG": lang, "PYTHONPATH": str(ROOT / "src"), "PATH": ""}
    out = subprocess.run([sys.executable, "-m", "fed.cli", "acquire", "--help"], capture_output=True, text=True,
                         env=env, cwd=ROOT).stdout
    if lang == "en":
        assert "Date range from (YYYY-MM-DD)" in out and "Partial acquisition: Sender contains" in out
        assert not re.search(r"[äöüß]|\b(Zeitraum|Konto|Neubeginn|Passwort|mehrfach)\b", out), out
    else:
        assert "Zeitraum ab (JJJJ-MM-TT)" in out and "Teilsicherung: Absender enthält" in out


def test_gui_helper_tr_literals_are_translatable():
    """``_tr("…")`` mit festem Text erkennt lupdate nicht – solche Texte müssen anderswo markiert sein
    (QT_TRANSLATE_NOOP/self.tr/translate), sonst blieben sie auf Englisch deutsch."""
    import html

    ts = (ROOT / "src" / "fed" / "i18n" / "fed_en.ts").read_text(encoding="utf-8")
    sources = {html.unescape(s) for s in re.findall(r"<source>(.*?)</source>", ts, re.S)}
    missing = []
    for path in (ROOT / "src" / "fed" / "gui").glob("*.py"):
        for lit in re.findall(r'(?<![\w.])_tr\("((?:[^"\\]|\\.)*)"\)', path.read_text(encoding="utf-8")):
            text = lit.encode().decode("unicode_escape").encode("latin-1").decode("utf-8")
            if text not in sources:
                missing.append(f"{path.name}: {text}")
    assert not missing, missing
