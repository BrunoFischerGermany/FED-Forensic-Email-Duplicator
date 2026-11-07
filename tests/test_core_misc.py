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
