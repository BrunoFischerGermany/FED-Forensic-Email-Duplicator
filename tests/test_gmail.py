"""Gmail-Verhalten über dem Test-Dovecot simuliert: X-GM-EXT-1, SPECIAL-USE-Kennungen, X-GM-Felder."""

import dataclasses
import re

import pytest

from fed.core.acquisition import AccountSpec, gmail_recommended, register_account, run_attempt, scan
from fed.core.case import Case, CaseInfo
from fed.core.config import Config
from fed.core.finalize import finish_case, message_rows
from fed.core.imap_reader import ReadOnlyImap, parse_gmail

SPECIAL = {"Archiv": "\\All", "Gesendet": "\\Junk", "Leer": "\\Trash"}
LABELS = b'("\\\\Important" "Projekt Ost" Entw&APw-rfe)'


class GmailSim(ReadOnlyImap):
    def capabilities(self):
        return (*super().capabilities(), "X-GM-EXT-1")

    def list_folders(self):
        return [dataclasses.replace(f, flags=(*f.flags, SPECIAL[f.name])) if f.name in SPECIAL else f
                for f in super().list_folders()]

    def examine(self, folder):
        self._folder_no = sorted(SPECIAL).index(folder.name) + 1 if folder.name in SPECIAL else 0
        return super().examine(folder)

    def connect(self):
        super().connect()
        orig = self._imap.uid

        def uid(cmd, *args):
            if cmd.upper() == "FETCH" and args and "X-GM" in str(args[-1]):
                typ, data = orig(cmd, *args[:-1], re.sub(r" X-GM-[A-Z]+", "", args[-1]))
                return typ, [_inject(d, getattr(self, "_folder_no", 0)) for d in data]
            return orig(cmd, *args)

        self._imap.uid = uid


def _inject(d, folder_no: int):
    def add(head: bytes) -> bytes:
        u = re.search(rb"UID (\d+)", head)
        if not u:
            return head
        n = str(folder_no).encode() + u.group(1)
        return head.replace(b"(", b"(X-GM-MSGID 9000" + n + b" X-GM-THRID 7000" + n + b" X-GM-LABELS " + LABELS + b" ", 1)
    if isinstance(d, tuple):
        return (add(d[0]), d[1])
    return add(d) if isinstance(d, bytes) else d


@pytest.fixture
def gmail_case(tmp_path, imap_server):
    def connect():
        c = GmailSim(imap_server.host, imap_server.port, cafile=str(imap_server.cafile))
        c.connect()
        c.login_password(imap_server.user, imap_server.password)
        return c

    case = Case.create(tmp_path, CaseInfo("G-1", "Tester"))
    imap = connect()
    acc = register_account(case, AccountSpec(imap_server.user, "G-01", imap_server.host, imap_server.port), imap)
    scan(case, acc, imap)
    imap.logout()
    yield case, acc, connect
    case.close()


def test_parse_gmail_labels():
    mid, thr, labels = parse_gmail(b"1 (UID 5 X-GM-MSGID 12 X-GM-THRID 34 X-GM-LABELS " + LABELS + b" FLAGS ())")
    assert (mid, thr, labels) == ("12", "34", ("\\Important", "Projekt Ost", "Entwürfe"))
    assert parse_gmail(b"1 (UID 5 X-GM-MSGID 12 X-GM-LABELS ())") == ("12", None, ())


def test_gmail_recommended_selection(gmail_case):
    case, acc, _ = gmail_case
    assert gmail_recommended(case, acc) == ["Archiv", "Gesendet", "Leer"]
    selected = sorted(r[0] for r in case.conn.execute(
        "SELECT display_name FROM folder WHERE account_id = ? AND selected = 1", (acc,)))
    assert selected == ["Archiv", "Gesendet", "Leer"]
    assert case.conn.execute("SELECT COUNT(*) FROM log WHERE event = 'gmail_selection'").fetchone()[0] == 1


def test_gmail_fields_saved_and_reported(gmail_case):
    case, acc, connect = gmail_case
    res = run_attempt(case, acc, connect, cfg=Config())
    assert res.status == "done" and res.saved == 2
    rows = message_rows(case)
    assert {r["Gmail_Labels"] for r in rows} == {"\\Important, Projekt Ost, Entwürfe"}
    assert all(r["Gmail_MsgID"].startswith("9000") and r["Gmail_ThrID"].startswith("7000") for r in rows)
    assert not any(r["Gmail_Duplikat"] for r in rows)
    # zwei Kopien derselben Mail (gleiche X-GM-MSGID) werden als Duplikat gekennzeichnet
    with case.conn:
        case.conn.execute("UPDATE message SET gm_msgid = '42'")
    assert {r["Gmail_Duplikat"] for r in message_rows(case)} == {"ja"}
    fin = finish_case(case, Config(), language="de")
    txt = fin.report_txt.read_text(encoding="utf-8")
    assert "Gmail-Kennung" in txt and "1 Mehrfachkopien von 1 Mails" in txt and "Labels: \\Important" in txt


def test_non_gmail_keeps_all_folders(tmp_path, imap_server):
    case = Case.create(tmp_path, CaseInfo("N-1", "Tester"))
    imap = ReadOnlyImap(imap_server.host, imap_server.port, cafile=str(imap_server.cafile))
    imap.connect()
    imap.login_password(imap_server.user, imap_server.password)
    acc = register_account(case, AccountSpec(imap_server.user, "N-01", imap_server.host, imap_server.port), imap)
    scan(case, acc, imap)
    imap.logout()
    assert gmail_recommended(case, acc) == []
    n = case.conn.execute("SELECT COUNT(*) FROM folder WHERE account_id = ? AND selected = 1", (acc,)).fetchone()[0]
    assert n > 3
    case.close()
