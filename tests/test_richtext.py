import os

import pytest

from fed.core import richtext as rt


def test_sanitize_keeps_only_allowed_formats():
    dirty = ('<div onclick="x()"><p style="margin:9px">Hallo <b>fett</b> <script>alert(1)</script>'
             '<a href="http://evil">Link</a> <img src="http://x/y.png"> <i><u>beides</u></i> '
             '<span style="color: #C62828; font-size:40px">rot</span> <mark>gelb</mark> '
             '<span style="color: red">kein Hex</span></p><ul><li>eins</li><li>zwei</li></ul></div>')
    clean = rt.sanitize(dirty)
    assert "script" not in clean and "href" not in clean and "img" not in clean and "onclick" not in clean
    assert "<b>fett</b>" in clean and "<i><u>beides</u></i>" in clean
    assert '<span style="color:#c62828">rot</span>' in clean
    assert f'<span style="background-color:{rt.HIGHLIGHT}">gelb</span>' in clean
    assert "kein Hex" in clean and "color: red" not in clean
    assert clean.endswith("<ul><li>eins</li><li>zwei</li></ul>")
    assert rt.sanitize(clean) == clean  # stabil


def test_plain_and_normalize():
    html_value = "<p>Durchsuchungsort <b>Musterstraße</b></p><p></p><ul><li>A &amp; B</li><li>C</li></ul>"
    assert rt.to_plain(html_value) == "Durchsuchungsort Musterstraße\n\n• A & B\n• C"
    plain, h = rt.normalize(html_value)
    assert plain.startswith("Durchsuchungsort") and h and "<b>" in h
    # unformatiert -> nur Text, kein HTML gespeichert
    assert rt.normalize("<p>nur Text</p>") == ("nur Text", None)
    assert rt.normalize(None, "Zeile 1\nZeile 2") == ("Zeile 1\nZeile 2", None)
    assert rt.parse("Zeile <1>") != [] and rt.to_plain("a < b") == "a < b"


def test_account_notes_html_stored_and_logged(acquired):
    from fed.core.acquisition import accounts_overview, set_account_notes

    case = acquired
    assert set_account_notes(case, 1, "", "<p><b>Wichtig:</b> Einwilligung liegt vor</p>")
    row = case.conn.execute("SELECT notes, notes_html FROM account WHERE id = 1").fetchone()
    assert row[0] == "Wichtig: Einwilligung liegt vor" and row[1] == "<p><b>Wichtig:</b> Einwilligung liegt vor</p>"
    log = case.conn.execute("SELECT details FROM log WHERE event = 'account_notes_changed'").fetchone()[0]
    assert "new_html" in log
    assert accounts_overview(case)[0]["notes_html"].startswith("<p><b>")
    assert not set_account_notes(case, 1, "", "<p><b>Wichtig:</b> Einwilligung liegt vor</p>")  # unverändert


def test_report_title_multi_examiner_and_signature(acquired, tmp_path):
    from fed.core.report import collect, labels, render_txt, write_pdf

    case = acquired
    case.conn.execute("UPDATE case_info SET notes_html = ?", (
        '<p>Ort <b>fett</b> <span style="color:#000000; background-color:#fff176">markiert</span></p>'
        '<ul><li>' + "lang " * 60 + '</li></ul>',))
    case.conn.commit()
    txt = render_txt(case, "de")
    assert "Sicherungsbericht E-Mail-Postfach" in txt[:400]
    assert "Bestätigung" in txt and "Unterschrift" not in txt and "Max Mustermann" in txt[txt.index("Bestätigung"):]
    write_pdf(case, tmp_path / "r.pdf", "en")
    import pypdfium2

    doc = pypdfium2.PdfDocument(tmp_path / "r.pdf")
    first = doc[0].get_textpage().get_text_range()
    assert "Examiner Max Mustermann" in first and "markiert" in first and "fett" in first
    assert any("Confirmation" in doc[i].get_textpage().get_text_range() for i in range(len(doc)))
    # zwei Konten -> Titel mit Anzahl
    from fed.core.report import _title

    d = collect(case)
    d["all_accounts"] = 2
    assert _title(d, labels("de")) == "Sicherungsbericht von 2 E-Mail-Postfächern"
    assert _title(d, labels("en")) == "Acquisition Report for 2 Email Mailboxes"


@pytest.fixture
def qapp():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    QtWidgets = pytest.importorskip("PySide6.QtWidgets")
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def test_editor_roundtrip_and_buttons(qapp):
    from PySide6.QtGui import QTextCursor

    from fed.gui.richedit import RichNoteEdit

    e = RichNoteEdit()
    e.set_html('<p>Hallo <b>fett</b> <span style="color:#c62828">rot</span></p><ul><li>eins</li></ul>')
    assert rt.sanitize(e.html()) == e.html()
    assert "<b>fett</b>" in e.html() and 'color:#c62828' in e.html() and "<li>eins</li>" in e.html()
    assert e.toPlainText() == "Hallo fett rot\n• eins"
    # Text auswählen und per Knopf formatieren
    e.setPlainText("Beschluss liegt vor")
    c = e.edit.textCursor()
    c.setPosition(0)
    c.setPosition(9, QTextCursor.MoveMode.KeepAnchor)
    e.edit.setTextCursor(c)
    e.b_bold.click()
    e.b_mark.click()
    h = e.html()
    assert h.startswith("<p><b><span style=\"color:#000000; background-color:#fff176\">Beschluss</span></b>"), h
    e.b_mark.click()  # Marker wieder aus
    assert "background" not in e.html() and "<b>Beschluss</b>" in e.html()
    c.setPosition(0)
    c.setPosition(9, QTextCursor.MoveMode.KeepAnchor)
    e.edit.setTextCursor(c)
    e._color(rt.COLORS["blue"])
    assert 'color:#1565c0' in e.html()
    e._clear()
    assert e.html() == "<p>Beschluss liegt vor</p>"
    e.b_list.click()
    assert e.html() == "<ul><li>Beschluss liegt vor</li></ul>"
    e.b_list.click()
    assert e.html() == "<p>Beschluss liegt vor</p>"
    # Einfügen von fremdem HTML kommt als reiner Text
    from PySide6.QtCore import QMimeData

    m = QMimeData()
    m.setHtml("<b style='font-size:40px'>Fremd</b>")
    m.setText("Fremd")
    e.clear()
    e.edit.insertFromMimeData(m)
    assert e.html() == "<p>Fremd</p>"
    e.deleteLater()


def test_closing_note_and_duration_in_report(acquired, tmp_path):
    from fed.core.config import Config
    from fed.core.finalize import finish_case
    from fed.core.report import _duration_items, fmt_duration, labels

    assert fmt_duration(12) == "12 s" and fmt_duration(242) == "4 min 02 s" and fmt_duration(7509) == "2 h 05 min 09 s"
    L = labels("de")
    att = [{"account_id": 1, "started_utc": "2026-11-09T10:00:00.000Z", "ended_utc": "2026-11-09T10:04:00.000Z"},
           {"account_id": 1, "started_utc": "2026-11-09T10:30:00.000Z", "ended_utc": "2026-11-09T10:31:30.000Z"}]
    items = dict(_duration_items(att, L))
    assert items["Dauer der Sicherung"] == "31 min 30 s (Summe der 2 Versuche: 5 min 30 s)"
    assert items["Beginn der Sicherung"].startswith("2026-11-09 10:00:00 UTC")
    running = dict(_duration_items([{**att[0], "ended_utc": None}], L))
    assert running["Dauer der Sicherung"] == "läuft noch" and running["Ende der Sicherung"] == "…"

    case = acquired
    cfg = Config()
    cfg.programm.ntp = False
    res = finish_case(case, cfg, closing_note="", closing_note_html=(
        "<p>Übergabe an <b>KHK Muster</b></p><ul><li>Datenträger versiegelt</li></ul>"))
    info = case.info()
    assert info["closing_note"] == "Übergabe an KHK Muster\n• Datenträger versiegelt" and "<b>" in info["closing_note_html"]
    assert case.conn.execute("SELECT COUNT(*) FROM log WHERE event = 'closing_note_set'").fetchone()[0] == 1
    txt = res.report_txt.read_text(encoding="utf-8")
    i, j = txt.index("Abschlussvermerk"), txt.index("Bestätigung")
    assert i < j and "Datenträger versiegelt" in txt[i:j]
    assert "Beginn der Sicherung" in txt and "Dauer der Sicherung" in txt
    import pypdfium2

    doc = pypdfium2.PdfDocument(res.report_pdf)
    text = "".join(doc[k].get_textpage().get_text_range() for k in range(len(doc)))
    assert "Abschlussvermerk" in text and "KHK Muster" in text and "Dauer der Sicherung" in text
    import pytest as _pytest

    with _pytest.raises(RuntimeError):
        case.set_closing_note("später")  # nach Abschluss nicht mehr änderbar
