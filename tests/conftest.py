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
