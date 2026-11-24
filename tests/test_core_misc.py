from pathlib import Path

import pytest

from fed.core import config
from fed.core.autoconfig import Provider, parse_autoconfig_xml
from fed.core.hashing import hash_bytes
from fed.core.imap_reader import ForbiddenCommand, guard
from fed.core.oauth import pkce_pair, xoauth2_string

ROOT = Path(__file__).resolve().parents[1]


def test_example_config_loads():
    cfg = config.load(ROOT / "config.example.toml")
    assert cfg.programm.hash_extra == ["md5", "sha512"]
    assert cfg.programm.zip_zeit_quelle == "internaldate"
    assert cfg.oauth_microsoft.tenant == "organizations"


def test_invalid_hash_rejected(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text('[programm]\nhash_extra = ["crc32"]\n', encoding="utf-8")
    with pytest.raises(config.ConfigError):
        config.load(p)


def test_report_content_settings_defaults_and_validation(tmp_path):
    """Neue Bericht-Einstellungen: Vorgaben und Validierung (DKIM-Liste, DNS-Modus)."""
    cfg = config.Config()
    assert cfg.programm.bericht_dkimliste == "auto" and cfg.programm.bericht_dkimliste_max == 500
    assert cfg.programm.bericht_dns == "csv"

    p = tmp_path / "config.toml"
    # immer/nie werden wie bei der Mailliste normalisiert
    p.write_text('[programm]\nbericht_dkimliste = "immer"\nbericht_dns = "tabelle"\n', encoding="utf-8")
    cfg = config.load(p)
    assert cfg.programm.bericht_dkimliste == "liste" and cfg.programm.bericht_dns == "tabelle"

    p.write_text('[programm]\nbericht_dns = "xml"\n', encoding="utf-8")
    with pytest.raises(config.ConfigError):
        config.load(p)
    p.write_text('[programm]\nbericht_dkimliste = "quatsch"\n', encoding="utf-8")
    with pytest.raises(config.ConfigError):
        config.load(p)


def test_tsa_qualified_default_and_roundtrip(tmp_path):
    """Zeitstempel-Dienst: Vorgabe nicht qualifiziert; Laden/Speichern des qualifiziert-Flags."""
    assert config.Config().tsa_qualifiziert is False

    p = tmp_path / "config.toml"
    p.write_text('[zeitstempel]\ntsa_url = "https://tsr.open-tsa.eu"\nqualifiziert = true\n', encoding="utf-8")
    cfg = config.load(p)
    assert cfg.tsa_url == "https://tsr.open-tsa.eu" and cfg.tsa_qualifiziert is True

    out = tmp_path / "out.toml"
    config.save(cfg, out)
    assert config.load(out).tsa_qualifiziert is True


def test_hashes():
    h = hash_bytes(b"abc", ["md5", "sha512"])
    assert h["sha256"] == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    assert h["md5"] == "900150983cd24fb0d6963f7d28e17f72"
    assert set(h) == {"sha256", "md5", "sha512"}


@pytest.mark.parametrize("cmd", ["STORE 1 +FLAGS (\\Seen)", "UID MOVE 5 Trash", "SELECT INBOX", "expunge"])
def test_guard_blocks_writes(cmd):
    with pytest.raises(ForbiddenCommand):
        guard(cmd)


@pytest.mark.parametrize("cmd", ["EXAMINE INBOX", "UID FETCH 1 (BODY.PEEK[])", "LIST \"\" \"*\""])
def test_guard_allows_reads(cmd):
    guard(cmd)


def test_autoconfig_xml():
    xml = """<clientConfig version="1.1"><emailProvider id="gmail.com">
      <incomingServer type="imap"><hostname>imap.gmail.com</hostname><port>993</port>
      <socketType>SSL</socketType><username>%EMAILADDRESS%</username></incomingServer>
    </emailProvider></clientConfig>"""
    s = parse_autoconfig_xml(xml, "max@gmail.com")
    assert s.host == "imap.gmail.com" and s.port == 993 and s.security == "ssl"
    assert s.username == "max@gmail.com" and s.provider is Provider.GOOGLE


def test_oauth_helpers():
    verifier, challenge = pkce_pair()
    assert 43 <= len(verifier) <= 128 and "=" not in challenge
    assert xoauth2_string("a@b.de", "T") == b"user=a@b.de\x01auth=Bearer T\x01\x01"


def test_expected_hashes_in_sync():
    import csv
    import hashlib
    import sys

    sys.path.insert(0, str(ROOT / "validation"))
    import testmailbox

    with (ROOT / "validation" / "expected_hashes.csv").open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert [r["sha256"] for r in rows] == [hashlib.sha256(m.raw).hexdigest() for m in testmailbox.mails()]


def test_languages_and_report_fallback():
    from fed.core import report
    from fed.core.languages import report_language_for, report_languages, ui_languages

    assert ui_languages()[0] == "de" and "en" in ui_languages()
    assert {"de", "en"} <= set(report_languages())
    assert report_language_for("xx") == "en"
    assert set(report.LABELS["de"]) == set(report.LABELS["en"])
    assert report.labels("xx")["title"] == report.LABELS["en"]["title"]


def test_config_snapshot_redacts_secrets(tmp_path):
    """Kopie der config.toml im Fall: Client-Secret und Proxy-Passwort geschwärzt, Kommentare bleiben;
    der Einstellungsvergleich sieht dadurch keine Scheinänderung."""
    from fed.core import config as cfgmod
    from fed.core.case import Case, CaseInfo

    text = ('# Kommentar bleibt\n[proxy]\nurl = "http://max:Geheim123@proxy.local:3128"  # Dienst-Proxy\n\n'
            '[oauth.google]\nclient_id = "123.apps.googleusercontent.com"\nclient_secret = "GOCSPX-abc"\n'
            '[oauth.microsoft]\nclient_id = ""\n')
    red, keys = cfgmod.redact_text(text)
    assert keys == ["proxy.url", "oauth.google.client_secret"]
    assert "Geheim123" not in red and "GOCSPX" not in red and "max" not in red
    assert 'url = "http://***@proxy.local:3128"  # Dienst-Proxy' in red and "# Kommentar bleibt" in red
    assert 'client_secret = "***"' in red and "123.apps.googleusercontent.com" in red
    assert cfgmod.redact_text("[proxy]\nurl = \"http://proxy.local:3128\"\n")[1] == []  # nichts zu schwärzen
    odd, keys = cfgmod.redact_text('oauth = { google = { client_secret = "GOCSPX-x" } }\n')  # Inline-Tabelle
    assert "GOCSPX" not in odd and keys == ["oauth.google.client_secret"]
    p = tmp_path / "config.toml"
    p.write_text(text, encoding="utf-8")
    cfg = cfgmod.load(p)
    case = Case.create(tmp_path, CaseInfo("AZ-S", "Tester"))
    case.store_config_snapshot(cfg.raw_text, None)
    stored = case.conn.execute("SELECT toml_text FROM config_snapshot").fetchone()[0]
    assert "Geheim123" not in stored and "GOCSPX" not in stored
    every = " ".join(r[0] for r in case.conn.execute("SELECT details FROM log"))
    assert "Geheim123" not in every and "oauth.google.client_secret" in every
    assert case.record_settings(cfg, "attempt") == []  # geschwärzt = wirksam (geschwärzt) -> keine Änderung
    case.close()
