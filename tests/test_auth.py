import hashlib
import time

import fake_oauth
import pytest

from fed.core.acquisition import AccountSpec, register_account, run_attempt, scan
from fed.core.auth import OAuthSession, log_oauth_login, make_connector
from fed.core.case import Case, CaseInfo
from fed.core.config import Config


def test_connector_requires_exactly_one_method():
    with pytest.raises(ValueError):
        make_connector("h", 993, "ssl", "u")
    with pytest.raises(ValueError):
        make_connector("h", 993, "ssl", "u", password="x", oauth_session=object())


def test_oauth_acquisition_with_token_refresh(tmp_path, imap_server, oauth_provider, test_mails):
    sess = OAuthSession(oauth_provider.provider("google"), oauth_provider.client_id)
    refreshed = []
    sess.on_refresh = refreshed.append
    token = sess.login_interactive(login_hint=imap_server.user, open_browser=fake_oauth.browser)
    connect = make_connector(imap_server.host, imap_server.port, "ssl", imap_server.user,
                             oauth_session=sess, cafile=str(imap_server.cafile))

    case = Case.create(tmp_path, CaseInfo("OAUTH-1", "Tester"))
    assert log_oauth_login(case, token, imap_server.user) is None
    imap = connect()
    acc = register_account(case, AccountSpec(imap_server.user, "A-01", imap_server.host, imap_server.port,
                                             auth_method="oauth_google"), imap)
    scan(case, acc, imap)
    imap.logout()

    sess.token.expires_at = time.time() - 1  # Token abgelaufen -> beim nächsten Verbinden erneuern
    cfg = Config()
    res = run_attempt(case, acc, connect, cfg=cfg)
    assert res.status == "done" and res.saved == len(test_mails)
    assert len(refreshed) == 1
    hashes = {r[0] for r in case.conn.execute("SELECT sha256 FROM message")}
    assert hashes == {hashlib.sha256(m.raw).hexdigest() for m in test_mails}
    audit = case.conn.execute("SELECT details FROM log WHERE event = 'oauth_login'").fetchone()[0]
    assert sess.token.access_token not in audit and '"account": "test@example.de"' in audit
    case.close()


def test_account_mismatch_warning(tmp_path, oauth_provider):
    sess = OAuthSession(oauth_provider.provider(), oauth_provider.client_id)
    token = sess.login_interactive(open_browser=fake_oauth.browser)
    case = Case.create(tmp_path, CaseInfo("OAUTH-2", "Tester"))
    warning = log_oauth_login(case, token, "anderes@example.de")
    assert warning and "geteiltem Postfach" in warning
    case.close()


def test_google_app_password_normalized():
    from fed.core.auth import normalize_app_password

    assert normalize_app_password("abcd efgh ijkl mnop") == "abcdefghijklmnop"
    assert normalize_app_password("  abcdefghijklmnop ") == "abcdefghijklmnop"
    assert normalize_app_password("Geheim 123 mit Leerzeichen") == "Geheim 123 mit Leerzeichen"
    assert normalize_app_password("abcd efgh ijkl") == "abcd efgh ijkl"


def test_gmail_login_error_explained():
    from fed.core.auth import APP_PASSWORD_URL, explain_login_error, is_gmail

    assert is_gmail("IMAP.gmail.com") and not is_gmail("imap.example.de")
    msg = explain_login_error("imap.gmail.com", "[ALERT] Application-specific password required")
    assert APP_PASSWORD_URL in msg and "2-Schritt" in msg
    assert "App-Passwort" in explain_login_error("imap.gmail.com", "[AUTHENTICATIONFAILED] Invalid credentials")
    assert explain_login_error("imap.example.de", "x") == "x"
