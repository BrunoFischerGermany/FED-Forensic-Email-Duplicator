"""Exchange/Outlook.com melden oft eine falsche RFC822.SIZE – Sicherung darf daran nicht scheitern."""

import json

from test_imap_reader import _fake_sizes


def test_acquisition_with_wrong_server_sizes(tmp_path, imap_server, monkeypatch):
    from fed.core.acquisition import AccountSpec, register_account, run_attempt, scan
    from fed.core.case import Case, CaseInfo
    from fed.core.config import Config
    from fed.core.imap_reader import ReadOnlyImap

    def connect():
        c = ReadOnlyImap(imap_server.host, imap_server.port, cafile=str(imap_server.cafile))
        c.connect()
        c.login_password(imap_server.user, imap_server.password)
        _fake_sizes(c, monkeypatch, 1000)
        return c

    case = Case.create(tmp_path, CaseInfo("2026-0043", "Max Mustermann", "Test"))
    imap = connect()
    acc = register_account(case, AccountSpec(imap_server.user, "A-01", imap_server.host, imap_server.port), imap)
    scan(case, acc, imap)
    imap.logout()
    res = run_attempt(case, acc, connect, cfg=Config())
    assert res.status == "done" and res.saved > 0 and res.size_differs == res.saved
    rows = case.conn.execute("SELECT size, size_server FROM message").fetchall()
    assert rows and all(s + 1000 == srv for s, srv in rows)
    level, details = case.conn.execute(
        "SELECT level, details FROM log WHERE event = 'size_differs'").fetchone()
    d = json.loads(details)
    assert level == "WARNING" and d["count"] == res.saved and len(d["examples"]) <= 5
    case.close()
