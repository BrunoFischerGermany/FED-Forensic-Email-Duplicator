"""Apple-Mail-Import (``fed.core.applemail``) gegen ein künstliches ``V10``-Muster.

Das Muster bildet das echte Format nach: ``.emlx`` = Längenzeile + genau N Bytes rohe Mail + Plist,
verschachtelte ``.mbox``-Ordner, ``.partial.emlx`` und eine ``Accounts4.sqlite`` mit der Zuordnung
Konto-UUID → Adresse (inkl. NSKeyedArchiver-Eigenschaft ``IdentityEmailAddress``).
"""

import plistlib
import sqlite3

import pytest

from fed.core import applemail as am

ACCT_A = "11111111-1111-4111-8111-111111111111"
ACCT_B = "22222222-2222-4222-8222-222222222222"


def _emlx(path, raw: bytes, flags=1, date=1700000000, partial=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    plist = plistlib.dumps({"flags": flags, "date-received": date, "remote-id": "7"})
    # Erste Zeile: Länge mit Leerzeichen aufgefüllt, dann die rohe Mail, dann der Plist
    path.write_bytes(f"{len(raw)}      \n".encode() + raw + b"\n" + plist)


def _msg(mailbox, number, raw, **kw):
    ext = ".partial.emlx" if kw.pop("partial", False) else ".emlx"
    _emlx(mailbox / "AAAA/Data/Messages" / f"{number}{ext}", raw, **kw)


def _mail(to, frm="Sender <s@ext.example>", subj="Hallo", body="Text\r\n"):
    return (f"From: {frm}\r\nTo: {to}\r\nSubject: {subj}\r\n"
            f"Date: Mon, 01 Sep 2026 10:00:00 +0200\r\n\r\n{body}").encode()


def _archiver(value: str) -> bytes:
    """NSKeyedArchiver-ähnlicher Plist, wie Apple Strings in Eigenschaften ablegt."""
    return plistlib.dumps({"$archiver": "NSKeyedArchiver", "$objects": ["$null", value],
                           "$top": {"root": 1}, "$version": 100000})


@pytest.fixture
def mailroot(tmp_path):
    v10 = tmp_path / "V10"
    # Konto A: INBOX mit Unterordnern Sent und „Ordnerü“ (Umlaut), eine partielle Mail
    a = v10 / ACCT_A / "INBOX.mbox"
    _msg(a, 1, _mail("maik@freenet.example"))
    _msg(a, 2, _mail("maik@freenet.example", subj="Zwei"))
    _msg(a / "Sent.mbox", 3, _mail("x@ext.example", frm="maik@freenet.example", subj="Gesendet"))
    _msg(a / "Ordnerü.mbox", 4, _mail("maik@freenet.example", subj="Umlaut"))
    _msg(a, 5, _mail("maik@freenet.example", subj="Teilweise"), partial=True)
    # Konto B: nur INBOX, Benutzername ist der Server -> Adresse aus IdentityEmailAddress
    b = v10 / ACCT_B / "INBOX.mbox"
    _msg(b, 10, _mail("chef@firma.example"))

    db = tmp_path / "Accounts4.sqlite"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE ZACCOUNT (Z_PK INTEGER PRIMARY KEY, ZIDENTIFIER TEXT, ZUSERNAME TEXT, "
                "ZACCOUNTDESCRIPTION TEXT)")
    con.execute("CREATE TABLE ZACCOUNTPROPERTY (Z_PK INTEGER PRIMARY KEY, ZOWNER INTEGER, ZKEY TEXT, ZVALUE BLOB)")
    con.execute("INSERT INTO ZACCOUNT VALUES (1,?,?,?)", (ACCT_A, "maik@freenet.example", "Freenet"))
    con.execute("INSERT INTO ZACCOUNT VALUES (2,?,?,?)", (ACCT_B, "outlook.office365.com", "Outlook"))
    con.execute("INSERT INTO ZACCOUNTPROPERTY VALUES (1,2,?,?)", ("IdentityEmailAddress", _archiver("chef@firma.example")))
    con.execute("INSERT INTO ZACCOUNTPROPERTY VALUES (2,1,?,?)", ("EmailAliases", _archiver("maik.alt@freenet.example")))
    con.commit()
    con.close()
    return tmp_path, v10, db


def test_read_emlx_byte_exact_and_meta(tmp_path):
    raw = _mail("a@b.example", body="Zeile1\r\nZeile2\r\n")
    f = tmp_path / "7.emlx"
    _emlx(f, raw, flags=am._FLAG_SEEN | am._FLAG_FLAGGED, date=1700000000)
    body, meta = am.read_emlx(f)
    assert body == raw  # byte-genau, nicht ein Byte mehr oder weniger
    assert am.AppleMessage.seen(meta) and am.AppleMessage.flagged(meta)
    assert am.AppleMessage.date_received(meta).year == 2023


def test_read_emlx_rejects_malformed(tmp_path):
    bad = tmp_path / "x.emlx"
    bad.write_bytes(b"999\nzu kurz")
    with pytest.raises(am.EmlxError, match="unvollständig"):
        am.read_emlx(bad)
    nolen = tmp_path / "y.emlx"
    nolen.write_bytes(b"keine zahl\nrumpf")
    with pytest.raises(am.EmlxError):
        am.read_emlx(nolen)


def test_accounts_and_nested_folders(mailroot):
    _, v10, _ = mailroot
    accts = {a.uuid: a for a in am.find_accounts(v10)}
    assert set(accts) == {ACCT_A, ACCT_B}
    paths = sorted(f.path for f in accts[ACCT_A].folders)
    assert paths == ["INBOX", "INBOX/Ordnerü", "INBOX/Sent"]
    assert accts[ACCT_A].message_count() == 5


def test_messages_and_partial_flag(mailroot):
    _, v10, _ = mailroot
    a = next(x for x in am.find_accounts(v10) if x.uuid == ACCT_A)
    inbox = next(f for f in a.folders if f.path == "INBOX")
    msgs = inbox.messages()
    assert [m.number for m in msgs] == [1, 2, 5]
    assert [m.partial for m in msgs] == [False, False, True]
    assert msgs[0].raw == _mail("maik@freenet.example")


def test_address_from_accounts_db_is_authoritative(mailroot):
    _, v10, db = mailroot
    amap = am.accounts_map(db)
    accts = {a.uuid: a for a in am.find_accounts(v10)}
    # Konto A: Adresse aus ZUSERNAME, Alias aus EmailAliases
    addr_a, src_a, _ = am.resolve_address(accts[ACCT_A], amap)
    assert (addr_a, src_a) == ("maik@freenet.example", "accounts_db")
    assert amap[ACCT_A].name == "Freenet" and "maik.alt@freenet.example" in amap[ACCT_A].aliases
    # Konto B: ZUSERNAME ist der Server -> Adresse kommt aus IdentityEmailAddress
    addr_b, src_b, _ = am.resolve_address(accts[ACCT_B], amap)
    assert (addr_b, src_b) == ("chef@firma.example", "accounts_db")


def test_address_falls_back_to_headers(mailroot):
    _, v10, _ = mailroot
    a = next(x for x in am.find_accounts(v10) if x.uuid == ACCT_A)
    addr, src, cands = am.resolve_address(a, None)  # keine Accounts-DB
    assert addr == "maik@freenet.example" and src == "headers"
    assert cands[0][0] == "maik@freenet.example"


def test_find_v10_dirs(mailroot):
    base, v10, _ = mailroot
    assert v10 in am.find_v10_dirs(base)          # über V*-Unterordner
    assert v10 in am.find_v10_dirs(v10.parent)    # direkt angegeben
