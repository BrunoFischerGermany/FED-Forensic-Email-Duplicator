"""GUI-Dialog für den Apple-Mail-Import: Ordner einlesen, Konten anhaken, importieren."""

from PySide6 import QtWidgets
from PySide6.QtCore import Qt

from fed.core.case import Case, CaseInfo
from test_applemail import ACCT_A, ACCT_B, _mail, _msg


def _tree(tmp_path):
    v10 = tmp_path / "Users" / "x" / "Library" / "Mail" / "V10"
    _msg(v10 / ACCT_A / "INBOX.mbox", 1, _mail("maik@freenet.example"))
    _msg(v10 / ACCT_A / "INBOX.mbox" / "Sent.mbox", 2, _mail("y@z.example", frm="maik@freenet.example"))
    _msg(v10 / ACCT_B / "INBOX.mbox", 9, _mail("chef@firma.example"))
    return tmp_path


def test_dialog_load_and_import(tmp_path):
    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    from fed.gui.applemail_dialog import AppleMailImportDialog

    root = _tree(tmp_path)
    case = Case.create(tmp_path / "fall", CaseInfo("2026-1", "Prüfer"))
    dlg = AppleMailImportDialog(None, case)
    dlg.load(root)  # findet V10 tief im Abzug, ohne echten Dateidialog
    assert dlg.table.rowCount() == 2 and dlg.ok.isEnabled()

    # Konto B abwählen, Asservat/Adresse von A anpassen
    for r in range(dlg.table.rowCount()):
        addr = dlg.table.item(r, 2).text()
        dlg.table.item(r, 0).setCheckState(Qt.CheckState.Checked if "freenet" in addr else Qt.CheckState.Unchecked)
    rows = dlg.selected()
    assert len(rows) == 1 and rows[0][2] == "maik@freenet.example"

    results = dlg.run_import(rows)
    assert len(results) == 1 and results[0][1].saved == 2 and results[0][2] is None
    assert case.conn.execute("SELECT COUNT(*) FROM account").fetchone()[0] == 1
    assert case.conn.execute("SELECT COUNT(*) FROM message WHERE source='apple_mail'").fetchone()[0] == 2
    dlg.deleteLater()
    case.close()


def test_dialog_reports_no_store(tmp_path):
    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    from fed.gui.applemail_dialog import AppleMailImportDialog

    case = Case.create(tmp_path / "fall", CaseInfo("2026-2", "Prüfer"))
    dlg = AppleMailImportDialog(None, case)
    dlg.load(tmp_path / "leer")
    assert dlg.table.rowCount() == 0 and not dlg.ok.isEnabled()
    dlg.deleteLater()
    case.close()
