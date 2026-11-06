import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "validation"))

import dovecot_server
import testmailbox


@pytest.fixture(scope="session")
def imap_server():
    """Lokaler Dovecot mit dem Testpostfach; übersprungen, wenn Dovecot fehlt."""
    if not dovecot_server.available():
        pytest.skip("dovecot/openssl nicht installiert")
    srv = dovecot_server.start()  # eigener Ordner unter /tmp, damit Dovecot-Dienste ihn lesen können
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
