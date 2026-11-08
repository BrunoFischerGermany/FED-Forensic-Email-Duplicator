import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "validation"))

import dovecot_server  # noqa: E402
import fake_oauth  # noqa: E402
import testmailbox  # noqa: E402


@pytest.fixture(scope="session")
def oauth_provider():
    """Nachgebauter OAuth-Anbieter (Autorisierung, Token, Introspection für Dovecot)."""
    fake = fake_oauth.FakeOAuth().start()
    yield fake
    fake.stop()


@pytest.fixture(scope="session")
def imap_server(oauth_provider):
    """Lokaler Dovecot mit dem Testpostfach (Passwort und XOAUTH2); übersprungen, wenn Dovecot fehlt."""
    if not dovecot_server.available():
        pytest.skip("dovecot/openssl nicht installiert")
    # eigener Ordner unter /tmp, damit Dovecot-Dienste ihn lesen können
    srv = dovecot_server.start(oauth_introspection_url=f"{oauth_provider.base}/introspect")
    yield srv
    srv.stop()


@pytest.fixture
def imap(imap_server):
    from fed.core.imap_reader import ReadOnlyImap

    c = ReadOnlyImap(imap_server.host, imap_server.port, cafile=str(imap_server.cafile))
    c.connect()
    c.login_password(imap_server.user, imap_server.password)
    yield c
    c.logout()


@pytest.fixture
def test_mails():
    return testmailbox.mails()


@pytest.fixture(autouse=True)
def _dkim_test_dns():
    """DKIM-Schlüssel des Testpostfachs lokal beantworten – Tests fragen nie echte DNS-Server."""
    from fed.core import authcheck
    from validation import testmailbox

    old = authcheck.DNS_OVERRIDE
    authcheck.DNS_OVERRIDE = testmailbox.dkim_dns()
    yield
    authcheck.DNS_OVERRIDE = old


# Ein Konto aus dem Testpostfach, gesichert in einen frischen Case (für Abschluss und Betrachter)
@pytest.fixture
def acquired(tmp_path, imap_server):
    def connect():
        c = ReadOnlyImap(imap_server.host, imap_server.port, cafile=str(imap_server.cafile))
        c.connect()
        c.login_password(imap_server.user, imap_server.password)
        return c

    logo = tmp_path / "logo.png"
    logo.write_bytes(bytes.fromhex(
        "89504e470d0a1a0a0000000d49484452000000010000000108020000009077"
        "53de0000000c4944415408d763f8cfc00000030101009c18e5d20000000049454e44ae426082"))
    from fed.core.acquisition import AccountSpec, register_account, run_attempt, scan
    from fed.core.case import Case, CaseInfo
    from fed.core.config import Config
    from fed.core.imap_reader import ReadOnlyImap

    case = Case.create(tmp_path, CaseInfo("2026-0042", "Max Mustermann", "Test"))
    case.store_config_snapshot('[agentur]\nname = "Muster Forensik GmbH"\nadresse = "Musterstr. 1"\n', logo)
    imap = connect()
    acc = register_account(case, AccountSpec(imap_server.user, "A-01", imap_server.host, imap_server.port), imap)
    scan(case, acc, imap)
    imap.logout()
    run_attempt(case, acc, connect, cfg=Config())
    yield case
    case.close()
