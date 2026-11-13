import imaplib
import threading

import fake_oauth
import pytest

from fed.core import oauth
from fed.core.cancel import Cancelled, CancelToken
from fed.core.imap_reader import ReadOnlyImap


def test_pkce_rfc7636_vector():
    assert oauth.pkce_challenge("dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk") == \
        "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"


def test_providers():
    assert "https://mail.google.com/" in oauth.GOOGLE.scopes
    ms = oauth.microsoft("contoso.onmicrosoft.com")
    assert "contoso.onmicrosoft.com" in ms.auth_url
    assert "https://outlook.office.com/IMAP.AccessAsUser.All" in ms.scopes and "offline_access" in ms.scopes


def test_id_token_account():
    assert oauth.id_token_account(fake_oauth._jwt({"email": "a@b.de"})) == "a@b.de"
    assert oauth.id_token_account(fake_oauth._jwt({"preferred_username": "x@y.de"})) == "x@y.de"
    assert oauth.id_token_account("kaputt") is None


def test_authorize_success_and_no_token_in_repr(oauth_provider):
    t = oauth.authorize(oauth_provider.provider(), oauth_provider.client_id,
                        open_browser=fake_oauth.browser, timeout_s=10)
    assert t.account == "test@example.de" and t.refresh_token and not t.expired
    assert t.access_token not in repr(t) and t.access_token not in str(t.audit())


def test_state_mismatch_rejected(oauth_provider):
    oauth_provider.tamper_state = True
    try:
        with pytest.raises(oauth.OAuthError, match="state"):
            oauth.authorize(oauth_provider.provider(), oauth_provider.client_id,
                            open_browser=fake_oauth.browser, timeout_s=10)
    finally:
        oauth_provider.tamper_state = False


def test_user_denies(oauth_provider):
    oauth_provider.deny = True
    try:
        with pytest.raises(oauth.OAuthError, match="access_denied"):
            oauth.authorize(oauth_provider.provider(), oauth_provider.client_id,
                            open_browser=fake_oauth.browser, timeout_s=10)
    finally:
        oauth_provider.deny = False


def test_missing_client_id(oauth_provider):
    with pytest.raises(oauth.OAuthError, match="Client-ID"):
        oauth.authorize(oauth_provider.provider(), "", open_browser=fake_oauth.browser)


def test_cancel_while_waiting(oauth_provider):
    token = CancelToken()
    threading.Timer(0.5, token.cancel).start()
    with pytest.raises(Cancelled):
        oauth.authorize(oauth_provider.provider(), oauth_provider.client_id, cancel=token,
                        open_browser=lambda url: None, timeout_s=10)


def test_timeout(oauth_provider):
    with pytest.raises(oauth.OAuthError, match="Zeitüberschreitung"):
        oauth.authorize(oauth_provider.provider(), oauth_provider.client_id,
                        open_browser=lambda url: None, timeout_s=1)


def test_refresh_keeps_refresh_token(oauth_provider):
    p = oauth_provider.provider()
    t = oauth.authorize(p, oauth_provider.client_id, open_browser=fake_oauth.browser, timeout_s=10)
    t2 = oauth.refresh(p, t, oauth_provider.client_id)
    assert t2.access_token != t.access_token and t2.refresh_token == t.refresh_token
    assert t2.account == "test@example.de"


def test_device_code(oauth_provider):
    p = oauth_provider.provider()
    dc = oauth.device_code_start(p, oauth_provider.client_id)
    assert dc.user_code == "ABCD-EFGH" and dc.verification_uri
    slept = []
    t = oauth.device_code_poll(p, dc, oauth_provider.client_id, sleep=slept.append)
    assert t.account == "test@example.de" and slept == [1]


def test_client_secret_required_when_configured(oauth_provider):
    oauth_provider.client_secret = "geheim"
    try:
        with pytest.raises(oauth.OAuthError):
            oauth.authorize(oauth_provider.provider(), oauth_provider.client_id,
                            open_browser=fake_oauth.browser, timeout_s=10)
        t = oauth.authorize(oauth_provider.provider(), oauth_provider.client_id, client_secret="geheim",
                            open_browser=fake_oauth.browser, timeout_s=10)
        assert t.access_token
    finally:
        oauth_provider.client_secret = ""


def test_xoauth2_login_against_dovecot(imap_server, oauth_provider):
    t = oauth.authorize(oauth_provider.provider(), oauth_provider.client_id,
                        open_browser=fake_oauth.browser, timeout_s=10)
    c = ReadOnlyImap(imap_server.host, imap_server.port, cafile=str(imap_server.cafile))
    c.connect()
    c.login_xoauth2(imap_server.user, t.access_token)
    assert any(f.name == "INBOX" for f in c.list_folders())
    c.logout()

    bad = ReadOnlyImap(imap_server.host, imap_server.port, cafile=str(imap_server.cafile))
    bad.connect()
    with pytest.raises(imaplib.IMAP4.error):
        bad.login_xoauth2(imap_server.user, "ungueltiges-token")
    bad.logout()


def test_builtin_microsoft_client_id():
    from fed.core.config import FED_MICROSOFT_CLIENT_ID, OAuthMicrosoft

    assert OAuthMicrosoft().effective_client_id == FED_MICROSOFT_CLIENT_ID
    assert OAuthMicrosoft().builtin
    own = OAuthMicrosoft(client_id=" 11111111-2222-3333-4444-555555555555 ")
    assert own.effective_client_id == "11111111-2222-3333-4444-555555555555" and not own.builtin
    import uuid
    uuid.UUID(FED_MICROSOFT_CLIENT_ID)


def test_hint_authenticated_not_connected():
    from fed.core import oauth

    h = oauth.explain_error("microsoft", "User is authenticated but not connected.")
    assert "IMAP" in h and "outlook.live.com" in h


def test_linkify():
    from fed.gui.assistant import linkify

    h = linkify("Outlook.com: outlook.live.com → x\n3. account.live.com/activity: <b> a@b.de")
    assert '<a href="https://outlook.live.com">outlook.live.com</a>' in h
    assert '<a href="https://account.live.com/activity">account.live.com/activity</a>' in h
    assert "Outlook.com:" in h and 'href="https://Outlook' not in h
    assert "&lt;b&gt;" in h and "<br>" in h and "b.de</a>" not in h
