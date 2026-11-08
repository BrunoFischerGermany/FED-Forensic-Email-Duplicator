import hashlib

from fed.core.config import Config
from fed.core.finalize import finish_case
from fed.core.viewer import auth_summary, export_attachment, open_message, parse_received
from validation import testmailbox


def _id(case, subject):
    return case.conn.execute("SELECT id FROM message WHERE subject = ?", (subject,)).fetchone()[0]


def test_open_from_work_and_zip(acquired, tmp_path):
    case = acquired
    mid = _id(case, "Rechnung 2026-1187")
    v = open_message(case, mid)
    assert v.source == "work" and v.sha256_ok and v.text.strip() == "Anbei die Rechnung."
    assert v.subject == "Rechnung 2026-1187" and "lieferant" in v.from_
    pdf = next(a for a in v.attachments if a.name == "Rechnung.pdf")
    assert pdf.mime == "application/pdf" and pdf.sha256 == hashlib.sha256(testmailbox.PDF).hexdigest()
    target = tmp_path / "export.pdf"
    assert export_attachment(case, v, pdf.index, target) == pdf.sha256 and target.read_bytes() == testmailbox.PDF
    assert case.conn.execute("SELECT COUNT(*) FROM log WHERE event = 'attachment_exported'").fetchone()[0] == 1

    finish_case(case, Config(), delete_work=True)  # Arbeitsordner weg -> aus dem ZIP lesen
    v2 = open_message(case, mid)
    assert v2.source.endswith(".zip") and v2.sha256_ok and v2.raw == v.raw
    assert case.conn.execute("SELECT COUNT(*) FROM log WHERE event = 'message_viewed'").fetchone()[0] == 2


def test_tampered_file_is_flagged(acquired):
    case = acquired
    mid = _id(case, "Projekt 2026")
    row = case.conn.execute("SELECT zip_path FROM message WHERE id = ?", (mid,)).fetchone()[0]
    (case.work_dir / row).write_bytes(b"Subject: anders\r\n\r\nmanipuliert\r\n")
    v = open_message(case, mid)
    assert not v.sha256_ok
    assert case.conn.execute("SELECT level FROM log WHERE event = 'message_viewed'").fetchone()[0] == "ERROR"


def test_html_inline_and_dkim(acquired):
    case = acquired
    v = open_message(case, _id(case, "Signatur mit Bild"))
    assert "<img" in v.html and v.text == ""
    assert {(a.name, a.inline) for a in v.attachments} >= {("daten.csv", False)}
    assert any(a.inline for a in v.attachments)
    s = auth_summary(open_message(case, _id(case, "Signierte Mitteilung")))
    assert s["dkim"] == "pass" and s["provider"]["dmarc"] == "pass"


def test_parse_received_chain():
    hops = parse_received([
        "from mx.example.de (mx.example.de [192.0.2.10]) by mail.example.de with ESMTPS id 1; "
        "Thu, 05 Nov 2026 08:12:45 +0100",
        "from client (dsl.example [198.51.100.7]) by smtp.lieferant.example with ESMTPSA id 2; "
        "Thu, 05 Nov 2026 08:12:41 +0100",
    ])
    assert [h.nr for h in hops] == [1, 2]
    assert hops[0].by == "smtp.lieferant.example" and hops[0].with_ == "ESMTPSA"
    assert hops[1].from_.startswith("mx.example.de") and hops[1].delay_s == 4.0


def test_gui_viewer_and_history_hook(acquired, monkeypatch):
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6 import QtWidgets

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    from fed.gui.history import HistoryTab
    from fed.gui.viewer import MailViewer

    case = acquired
    mid = _id(case, "Rechnung 2026-1187")
    dlg = MailViewer(case, mid)
    assert "Rechnung 2026-1187" in dlg.windowTitle()
    assert dlg.att.rowCount() >= 1 and "Anbei die Rechnung." in dlg.body.toPlainText()
    dlg.deleteLater()

    page = HistoryTab(lambda: case)
    page.show()
    page.tabs.setCurrentWidget(page.mails)
    page.refresh()
    opened = []
    monkeypatch.setattr(MailViewer, "exec", lambda self: opened.append(self.view.message_id) or 0)
    model = page.mails.model
    subjects = [model.item(r, 6).text() for r in range(model.rowCount())]
    assert "Rechnung 2026-1187" in subjects, subjects
    row = subjects.index("Rechnung 2026-1187")
    page.open_mail(page.mails.proxy.mapFromSource(model.index(row, 0)))
    assert opened == [mid]
    page.timer.stop()
    page.hide()
    page.deleteLater()
    app.processEvents()
