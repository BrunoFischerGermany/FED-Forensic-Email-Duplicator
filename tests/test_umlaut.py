"""Umlaute in E-Mail-Adressen: IDN-Domain (ärzte.de) und Umlaute im Namen (bärbel.böhmer@…)."""

import hashlib
import unicodedata
import zipfile

import pytest

from fed.core.acquisition import AccountSpec, register_account, run_attempt, scan
from fed.core.autoconfig import discover
from fed.core.case import Case, CaseInfo
from fed.core.config import Config
from fed.core.finalize import finish_case
from fed.core.imap_reader import ImapError, ReadOnlyImap
from fed.core.naming import email_domain, idna_host, normalize_email
from validation import testmailbox

ADDR = testmailbox.UMLAUT_USER


def test_idna_2008():
    assert idna_host("ärzte.de") == "xn--rzte-koa.de"
    assert idna_host("straße.de") == "xn--strae-oqa.de"  # IDNA 2003 ergäbe strasse.de
    assert idna_host("IMAP.Example.DE") == "imap.example.de"
    assert idna_host("imap.müller.de.") == "imap.xn--mller-kva.de"
    assert email_domain("bärbel.böhmer@Ärzte.de") == "xn--rzte-koa.de"
    with pytest.raises(ValueError):
        idna_host("ä..b.de")


def test_normalize_email_nfc():
    nfd = unicodedata.normalize("NFD", " bärbel.böhmer@ärzte.de ")
    assert nfd.strip() != ADDR
    assert normalize_email(nfd) == ADDR
    assert AccountSpec(nfd, "A-1", "imap.ärzte.de").email == ADDR


def test_autoconfig_uses_ascii_domain_and_quoted_address():
    urls = []

    def get(url):
        urls.append(url)
        return None

    discover(ADDR, http_get=get, dns_lookup=lambda *a: [], port_open=lambda *a: False)
    assert urls[0] == ("https://autoconfig.xn--rzte-koa.de/mail/config-v1.1.xml?emailaddress="
                       "b%C3%A4rbel.b%C3%B6hmer@%C3%A4rzte.de")
    assert all(u.isascii() for u in urls)


def _connect(srv):
    def c():
        imap = ReadOnlyImap(srv.host, srv.port, cafile=str(srv.cafile))
        imap.connect()
        imap.login_password(ADDR, testmailbox.UMLAUT_PASSWORD)
        return imap

    return c


def test_login_with_umlauts(imap_server):
    imap = _connect(imap_server)()
    assert any(f.name == "INBOX" for f in imap.list_folders())
    imap.logout()
    bad = ReadOnlyImap(imap_server.host, imap_server.port, cafile=str(imap_server.cafile))
    bad.connect()
    with pytest.raises(Exception, match=r"AUTHENTICATIONFAILED|Authentication failed"):
        bad.login_password(ADDR, "Grüße-falsch")
    bad.logout()


def test_umlaut_login_needs_auth_plain(imap_server):
    c = ReadOnlyImap(imap_server.host, imap_server.port, cafile=str(imap_server.cafile))
    c.connect()
    c.imap.capabilities = tuple(x for x in c.imap.capabilities if x != "AUTH=PLAIN")
    with pytest.raises(ImapError, match="AUTHENTICATE PLAIN"):
        c.login_password(ADDR, testmailbox.UMLAUT_PASSWORD)
    c.logout()


def test_acquisition_and_zip_with_umlaut_address(tmp_path, imap_server, test_mails):
    connect = _connect(imap_server)
    case = Case.create(tmp_path, CaseInfo("2026-Ü1", "Bärbel Böhmer"))
    imap = connect()
    acc = register_account(case, AccountSpec(ADDR, "A-Ä1", imap_server.host, imap_server.port), imap)
    scan(case, acc, imap)
    imap.logout()
    res = run_attempt(case, acc, connect, cfg=Config())
    assert res.status == "done" and res.saved == len(test_mails)
    assert (case.work_dir / ADDR).is_dir()

    fin = finish_case(case, Config(), language="en")
    want = {hashlib.sha256(m.raw).hexdigest() for m in test_mails}
    with zipfile.ZipFile(fin.zip.path) as zf:
        emls = [i for i in zf.infolist() if i.filename.endswith(".eml")]
        assert {i.filename.split("/")[0] for i in emls} == {ADDR}
        assert all(i.flag_bits & 0x800 for i in emls)  # Dateinamen als UTF-8 gekennzeichnet
        assert {hashlib.sha256(zf.read(i)).hexdigest() for i in emls} == want
        assert f"{ADDR}/Entwürfe/" in zf.namelist()
    assert fin.zip.verified
    assert ADDR in fin.report_txt.read_text(encoding="utf-8")
    case.close()
