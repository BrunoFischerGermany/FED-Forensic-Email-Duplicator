"""Microsoft: kein Passwort/App-Passwort per IMAP, nur OAuth – Erkennung, Mandant, Hinweise."""

import pytest

from fed.core import oauth
from fed.core.auth import explain_login_error, is_microsoft
from fed.core.autoconfig import KNOWN_PROVIDERS, Provider


@pytest.mark.parametrize("host", ["outlook.office365.com", "OUTLOOK.office365.com.", "imap-mail.outlook.com",
                                  "outlook.office.com", "imap.outlook.com"])
def test_microsoft_hosts(host):
    assert is_microsoft(host)
    assert KNOWN_PROVIDERS[host.lower().rstrip(".")] is Provider.MICROSOFT


@pytest.mark.parametrize("host", ["imap.gmail.com", "imap.gmx.net", "outlook.example.de", ""])
def test_other_hosts(host):
    assert not is_microsoft(host)


@pytest.mark.parametrize("email,consumer", [
    ("a@outlook.com", True), ("a@outlook.de", True), ("a@hotmail.co.uk", True), ("a@live.de", True),
    ("a@msn.com", True), ("a@windowslive.com", True), ("a@firma.de", False), ("a@outlookfirma.de", False),
    ("a@live-firma.de", False), ("", False)])
def test_consumer_accounts(email, consumer):
    assert oauth.is_microsoft_consumer(email) is consumer


def test_tenant_for_private_accounts():
    assert oauth.microsoft_tenant("organizations", "max@hotmail.de") == "consumers"
    assert oauth.microsoft_tenant("", "max@outlook.com") == "consumers"
    assert oauth.microsoft_tenant("organizations", "max@firma.de") == "organizations"
    # bewusst eingetragene Werte bleiben unverändert
    assert oauth.microsoft_tenant("common", "max@hotmail.de") == "common"
    tid = "11111111-2222-3333-4444-555555555555"
    assert oauth.microsoft_tenant(tid, "max@hotmail.de") == tid
    assert "/consumers/" in oauth.microsoft("consumers").auth_url


def test_login_error_hint():
    msg = explain_login_error("outlook.office365.com", "LOGIN failed.")
    assert msg.startswith("LOGIN failed.") and "OAuth" in msg and "IMAP" in msg
    assert explain_login_error("imap.gmx.net", "LOGIN failed.") == "LOGIN failed."


@pytest.mark.parametrize("err,needle", [
    ("microsoft: unauthorized_client – AADSTS700016 The client does not exist or is not enabled for consumers",
     "common"),
    ("invalid_grant: AADSTS65001: The user or administrator has not consented", "Administratorzustimmung"),
    ("Gerätecode: invalid_client – AADSTS7000218: The request body must contain client_assertion",
     "Öffentliche Clientflows"),
    ("access_denied: AADSTS53003: Access has been blocked by Conditional Access policies", "bedingten Zugriff"),
])
def test_oauth_error_hints(err, needle):
    assert needle in oauth.explain_error("microsoft", err)


def test_oauth_error_hint_only_for_known_errors():
    assert oauth.explain_error("microsoft", "Zeitüberschreitung bei der Anmeldung im Browser") == ""
    assert oauth.explain_error("google", "AADSTS65001") == ""



@pytest.mark.parametrize("lang", ["de", "en"])
def test_help_text_present(lang):
    from fed.gui.help import help_text

    text = help_text("oauth", lang)
    assert text.startswith("# ") and "IMAP.AccessAsUser.All" in text and "consumers" in text
    assert "## Microsoft" in text and "## Google" in text


def test_help_text_falls_back_to_english():
    from fed.gui.help import help_text

    assert help_text("oauth", "fr") == help_text("oauth", "en")


def test_help_files_have_same_structure():
    """Beide Sprachen haben dieselben Überschriften-Ebenen und Tabellenzeilen (nichts vergessen)."""
    from fed.gui.help import help_text

    def shape(t):
        return [ln.split(" ")[0] for ln in t.splitlines() if ln.startswith(("#", "|", "1.", "2.", "3.", "4.", "5.", "6."))]

    assert shape(help_text("oauth", "de")) == shape(help_text("oauth", "en"))
