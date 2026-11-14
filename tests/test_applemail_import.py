"""Apple-Mail-Import in einen echten Fall: Asservat je Adresse, byte-genaue .eml, Herkunft/partial,
und ein gültiges ZIP über den normalen Finalize-Weg."""

import zipfile

from fed.core import applemail_import as imp
from fed.core.case import Case, CaseInfo
from fed.core.finalize import build_zip
from test_applemail import ACCT_A, ACCT_B, _mail, _msg


def _tree(tmp_path):
    v10 = tmp_path / "V10"
    a = v10 / ACCT_A / "INBOX.mbox"
    _msg(a, 1, _mail("maik@freenet.example"))
    _msg(a / "Sent.mbox", 2, _mail("x@ext.example", frm="maik@freenet.example", subj="Gesendet"))
    _msg(a, 3, _mail("maik@freenet.example", subj="Teilweise"), partial=True)
    _msg(v10 / ACCT_B / "INBOX.mbox", 9, _mail("chef@firma.example"))
    return v10


def test_scan_lists_accounts(tmp_path):
    v10 = _tree(tmp_path)
    choices = {c.uuid: c for c in imp.scan(v10)}
    assert set(choices) == {ACCT_A, ACCT_B}
    a = choices[ACCT_A]
    assert a.address == "maik@freenet.example" and a.address_source == "headers"
    assert a.folders == 2 and a.messages == 3


def test_import_account_creates_asservat_byte_exact(tmp_path):
    v10 = _tree(tmp_path)
    case = Case.create(tmp_path / "fall", CaseInfo("2026-1", "Prüfer"))
    acc = next(a for a in __import__("fed.core.applemail", fromlist=["x"]).find_accounts(v10) if a.uuid == ACCT_A)
    res = imp.import_account(case, acc, "maik@freenet.example", "A-01", name="Freenet")

    assert res.saved == 3 and res.partial == 1 and res.folders == 2
    # Account/Ordner/Message in der Case-Datei
    row = case.conn.execute("SELECT email, evidence_number, autoconfig_source, auth_method FROM account").fetchone()
    assert row == ("maik@freenet.example", "A-01", "apple_mail", "import")
    folders = {r[0] for r in case.conn.execute("SELECT raw_name FROM folder")}
    assert folders == {"INBOX", "INBOX/Sent"}
    srcs = {r[0] for r in case.conn.execute("SELECT DISTINCT source FROM message")}
    assert srcs == {"apple_mail"}
    assert case.conn.execute("SELECT COUNT(*) FROM message WHERE partial=1").fetchone()[0] == 1

    # .eml byte-genau auf der Platte
    zp, size = case.conn.execute("SELECT zip_path, size FROM message WHERE partial=0 ORDER BY uid").fetchone()
    data = (case.work_dir / zp).read_bytes()
    assert data == _mail("maik@freenet.example") and len(data) == size
    assert zp.startswith("maik@freenet.example/INBOX/")

    # Finalize baut ein gültiges ZIP, das die byte-genaue .eml enthält
    zres = build_zip(case)
    with zipfile.ZipFile(zres.path) as zf:
        assert zres.path.exists()
        member = next(n for n in zf.namelist() if n.endswith(".eml") and "/INBOX/" in n and "Sent" not in n)
        assert zf.read(member) == _mail("maik@freenet.example")
    case.close()


def test_import_needs_address(tmp_path):
    v10 = _tree(tmp_path)
    case = Case.create(tmp_path / "fall", CaseInfo("2026-2", "Prüfer"))
    acc = next(a for a in __import__("fed.core.applemail", fromlist=["x"]).find_accounts(v10) if a.uuid == ACCT_B)
    import pytest
    with pytest.raises(ValueError, match="keine Adresse"):
        imp.import_account(case, acc, "   ", "B-01")
    case.close()
