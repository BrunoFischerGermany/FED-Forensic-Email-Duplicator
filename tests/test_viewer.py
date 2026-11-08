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


def test_inline_cid_and_attachment_check(acquired):
    from fed.core.viewer import check_attachment, inline_resource

    case = acquired
    v = open_message(case, _id(case, "Signatur mit Bild"))
    logo = next(a for a in v.attachments if a.content_id == "logo1")
    assert logo.inline and inline_resource(v, "cid:logo1") == testmailbox.PNG
    assert inline_resource(v, "cid:%3Clogo1%3E") == testmailbox.PNG and inline_resource(v, "cid:fehlt") is None
    csv = next(a for a in v.attachments if a.name == "daten.csv")
    data, fc = check_attachment(case, v, csv.index)
    assert data.replace(b"\r\n", b"\n") == b"a;b\n1;2" and fc.fmt.key == "text" and fc.verdict == "ok"
    row = case.conn.execute("SELECT level, details FROM log WHERE event = 'attachment_previewed'").fetchone()
    assert row[0] == "INFO" and fc.sha256 in row[1] and '"sha256_ok": true' in row[1]


def test_gui_attachment_preview(acquired):
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6 import QtWidgets
    from PySide6.QtGui import QTextDocument
    from PySide6.QtCore import QUrl

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    from fed.core.filecheck import FORMATS
    from fed.gui.viewer import FINDING_TEXT, FORMAT_NAMES, MailViewer

    assert set(FORMAT_NAMES) == {f.name for f in FORMATS.values()}
    import re
    from pathlib import Path

    src = Path("src/fed/core/filecheck.py").read_text(encoding="utf-8")
    assert set(re.findall(r'Finding\("(?:warn|info)", "(\w+)"', src)) <= set(FINDING_TEXT)

    case = acquired
    dlg = MailViewer(case, _id(case, "Signatur mit Bild"))
    names = [dlg.att.item(r, 1).text() for r in range(dlg.att.rowCount())]
    dlg.att.selectRow(names.index("daten.csv"))
    app.processEvents()
    assert dlg.preview.currentWidget() is dlg.pv_text and "a;b" in dlg.pv_text.toPlainText()
    assert dlg.hex.toPlainText().startswith("00000000  61 3b 62")
    assert "✓" in dlg.check_kv.values["Ergebnis"].text() or "unauffällig" in dlg.check_kv.values["Ergebnis"].text()
    # cid-Auflösung im HTML: Testbild ist nur ein PNG-Kopf -> kein darstellbares Bild, aber kein Nachladen
    assert dlg.body.loadResource(QTextDocument.ResourceType.ImageResource, QUrl("http://example.org/x.png")) is None
    assert dlg.body.resolver("cid:logo1") == testmailbox.PNG
    dlg.deleteLater()

    dlg2 = MailViewer(case, _id(case, "Rechnung 2026-1187"))
    app.processEvents()
    assert dlg2.check_kv.values["Erkannt am Inhalt"].text().startswith("PDF")
    assert dlg2.preview.currentWidget() in (dlg2.pv_pdf, dlg2.pv_msg)
    dlg2.deleteLater()
    app.processEvents()
    n = case.conn.execute("SELECT COUNT(*) FROM log WHERE event = 'attachment_previewed'").fetchone()[0]
    assert n >= 3  # Logo (erste Zeile), CSV, PDF


def test_gui_renders_real_image_pdf_and_cid(acquired):
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6 import QtWidgets
    from PySide6.QtCore import QBuffer, QIODevice, QUrl
    from PySide6.QtGui import QColor, QImage, QTextDocument
    from fpdf import FPDF

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    from fed.core.filecheck import check
    from fed.gui.viewer import MailViewer, _SafeBrowser

    img = QImage(40, 30, QImage.Format.Format_RGB32)
    img.fill(QColor("red"))
    buf = QBuffer()
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    img.save(buf, "PNG")
    png = bytes(buf.data())
    doc = FPDF()
    doc.add_page()
    doc.set_font("helvetica", size=12)
    doc.cell(text="Testseite")
    pdf = bytes(doc.output())

    b = _SafeBrowser(lambda ref: png if ref == "cid:bild" else b"MZ-kein-bild")
    res = b.loadResource(QTextDocument.ResourceType.ImageResource, QUrl("cid:bild"))
    assert isinstance(res, QImage) and res.width() == 40
    assert b.loadResource(QTextDocument.ResourceType.ImageResource, QUrl("cid:andere")) is None  # kein Bild
    assert b.loadResource(QTextDocument.ResourceType.ImageResource, QUrl("file:///etc/passwd")) is None

    dlg = MailViewer(acquired, _id(acquired, "Rechnung 2026-1187"))
    dlg._render_preview(png, check("bild.png", png, "image/png"))
    assert dlg.preview.currentWidget() is dlg._img_scroll and dlg.pv_img.pixmap().width() == 40
    dlg._render_preview(pdf, check("seite.pdf", pdf, "application/pdf"))
    if dlg.pv_pdf is not None:
        assert dlg.preview.currentWidget() is dlg.pv_pdf and dlg._pdf[0].pageCount() == 1
    exe = b"MZ" + b"\x00" * 200
    dlg._render_preview(exe, check("x.pdf", exe))
    assert dlg.preview.currentWidget() is dlg.pv_msg
    dlg.deleteLater()
    app.processEvents()
