import hashlib
from datetime import UTC, datetime

import pytest

from fed.core.imap_reader import ForbiddenCommand, parse_internaldate, parse_list_line


def test_parse_internaldate():
    dt = parse_internaldate(" 7-Jul-1996 02:44:25 -0700")
    assert dt.astimezone(UTC) == datetime(1996, 7, 7, 9, 44, 25, tzinfo=UTC)


def test_parse_list_line():
    assert parse_list_line(b'(\\HasNoChildren) "/" Entw&APw-rfe') == (("\\HasNoChildren",), "/", "Entw&APw-rfe")
    assert parse_list_line(b'(\\Noselect) "." "Mit Leerzeichen"') == (("\\Noselect",), ".", "Mit Leerzeichen")
    assert parse_list_line(b"() NIL INBOX") == ((), None, "INBOX")


def test_tls_and_server_info(imap):
    tls = imap.tls_info()
    assert tls["version"].startswith("TLS") and len(tls["sha256"]) == 95
    info = imap.server_info()
    assert "IMAP4REV1" in info["capabilities"]
    assert "id" in info and "namespace" in info


def test_folders_decoded(imap, test_mails):
    names = {f.name for f in imap.list_folders()}
    expected = {m.folder for m in test_mails} | {"Leer", "Projekte"}
    assert expected <= names
    assert "Entwürfe" in names and "Groß" in names


def test_fetch_is_byte_exact_and_read_only(imap, test_mails):
    folders = {f.name: f for f in imap.list_folders()}
    inbox = folders["INBOX"]
    before = imap.status(inbox)
    imap.examine(inbox)
    uids = imap.search_uids()
    expected = {hashlib.sha256(m.raw).hexdigest() for m in test_mails if m.folder == "INBOX"}
    got = set()
    for uid in uids:
        msg = imap.fetch(uid)
        got.add(hashlib.sha256(msg.raw).hexdigest())
        assert msg.internaldate.tzinfo is not None
    assert got == expected
    after = imap.status(inbox)
    assert before == after  # UNSEEN unverändert: nichts als gelesen markiert


def test_chunked_fetch(imap, test_mails):
    big = next(m for m in test_mails if m.folder == "Groß")
    folder = next(f for f in imap.list_folders() if f.name == "Groß")
    imap.examine(folder)
    (uid,) = imap.search_uids()
    msg = imap.fetch(uid, chunk_bytes=1024 * 1024)
    assert msg.raw == big.raw and msg.size_server == len(big.raw)
    assert msg.internaldate == big.received


def test_date_search(imap):
    inbox = next(f for f in imap.list_folders() if f.name == "INBOX")
    imap.examine(inbox)
    uids = imap.search_uids(since=datetime(2026, 11, 4, tzinfo=UTC))
    assert len(uids) == 2


def test_write_commands_blocked(imap):
    inbox = next(f for f in imap.list_folders() if f.name == "INBOX")
    imap.examine(inbox)
    with pytest.raises(ForbiddenCommand):
        imap.imap.uid("STORE", "1", "+FLAGS", "(\\Seen)")
    with pytest.raises(ForbiddenCommand):
        imap.imap.select('"INBOX"')
    with pytest.raises(ForbiddenCommand):
        imap.imap.expunge()


@pytest.mark.parametrize("cmd", ["UID FETCH 5 (BODY[])", "UID FETCH 5 (RFC822)", "FETCH 1 RFC822",
                                 "UID FETCH 5 (RFC822.TEXT)", "UID FETCH 5 (BODY[TEXT])", "uid fetch 5 (body[])"])
def test_guard_blocks_fetch_without_peek(cmd):
    from fed.core.imap_reader import ForbiddenCommand, guard

    with pytest.raises(ForbiddenCommand):
        guard(cmd)


@pytest.mark.parametrize("cmd", ["UID FETCH 1:* (UID RFC822.SIZE)", "UID FETCH 5 (BODY.PEEK[]<0.1048576>)",
                                 "UID FETCH 1,2 (UID BODY.PEEK[] FLAGS INTERNALDATE)", "FETCH 1 (RFC822.HEADER)"])
def test_guard_allows_peek(cmd):
    from fed.core.imap_reader import guard

    guard(cmd)


def _fake_sizes(imap, monkeypatch, delta):
    """Server meldet eine falsche RFC822.SIZE (wie Exchange/Outlook.com)."""
    import re

    orig = imap.imap.uid

    def fix(b):
        return re.sub(rb"RFC822\.SIZE (\d+)", lambda m: b"RFC822.SIZE %d" % max(0, int(m.group(1)) + delta), b)

    def uid(*args):
        typ, data = orig(*args)
        out = []
        for d in data:
            if isinstance(d, tuple):
                out.append((fix(d[0]), d[1]))
            elif isinstance(d, bytes):
                out.append(fix(d))
            else:
                out.append(d)
        return typ, out

    monkeypatch.setattr(imap.imap, "uid", uid)


@pytest.mark.parametrize("delta", [50000, -500])
def test_wrong_server_size_is_not_fatal(imap, test_mails, monkeypatch, delta):
    big = next(m for m in test_mails if m.folder == "Groß")
    _fake_sizes(imap, monkeypatch, delta)
    folder = next(f for f in imap.list_folders() if f.name == "Groß")
    imap.examine(folder)
    (uid,) = imap.search_uids()
    for chunk in (None, 1024 * 1024, 4096):
        msg = imap.fetch(uid, chunk_bytes=chunk)
        assert msg.raw == big.raw and msg.size_server == len(big.raw) + delta
    inbox = next(f for f in imap.list_folders() if f.name == "INBOX")
    imap.examine(inbox)
    uids = imap.search_uids()
    got = imap.fetch_many(uids)
    expected = {hashlib.sha256(m.raw).hexdigest() for m in test_mails if m.folder == "INBOX"}
    assert {hashlib.sha256(m.raw).hexdigest() for m in got.values()} == expected


def test_truncated_literal_detected():
    from fed.core.imap_reader import ImapError, _check_literal

    _check_literal(1, b"1 (UID 1 BODY[] {5}", b"12345")
    with pytest.raises(ImapError, match="unvollständig"):
        _check_literal(1, b"1 (UID 1 BODY[] {7}", b"12345")
