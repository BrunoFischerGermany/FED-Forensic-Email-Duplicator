"""Mehrsprachiges Postfach: Ordner über vier Ebenen und Mails in neun Schriften.

Russisch, Serbisch (kyrillisch), Kroatisch/Serbokroatisch, Vietnamesisch, Chinesisch, Japanisch,
Myanmar, Arabisch, Hebräisch – dazu alte Kodierungen (KOI8-R, ISO-2022-JP, GB18030,
windows-1256, windows-1255). Geprüft wird die ganze Kette: Scan, Sicherung, bitgenaue Hashes,
ZIP-Pfade, log.csv, Bericht (TXT und PDF ohne fehlende Zeichen), Betrachter, Inhaltsfilter.
"""

import csv
import hashlib
import io
import logging
import zipfile

import pytest

from fed.core.acquisition import AccountSpec, register_account, run_attempt, scan
from fed.core.case import Case, CaseInfo
from fed.core.config import Config
from fed.core.imap_reader import ReadOnlyImap
from validation import intlmailbox as im


def _connector(server):
    def connect():
        c = ReadOnlyImap(server.host, server.port, cafile=str(server.cafile))
        c.connect()
        c.login_password(im.USER, im.PASSWORD)
        return c

    return connect


@pytest.fixture
def intl_case(tmp_path, imap_server):
    connect = _connector(imap_server)
    case = Case.create(tmp_path, CaseInfo("ИНТЛ-2026/1 – 国际 · دولي", "Max Mustermann"))
    imap = connect()
    acc = register_account(case, AccountSpec(im.USER, "I-01", imap_server.host, imap_server.port), imap)
    rows = scan(case, acc, imap)
    imap.logout()
    yield case, acc, connect, rows
    case.close()


def test_scan_shows_all_folders_four_levels_deep(intl_case):
    case, _acc, _connect, rows = intl_case
    names = {r["name"] for r in rows}
    assert im.folders() <= names, im.folders() - names
    for lang in im.LANGS:  # alle vier Ebenen je Sprache, richtig verschachtelt
        assert all(im.folder(lang, n) in names for n in range(1, 5))
    db = {r[0] for r in case.conn.execute("SELECT display_name FROM folder")}
    assert "日本語/仕事/プロジェクト/報告書" in db and "العربية/العمل/المشاريع/التقارير" in db


def test_acquisition_bit_exact_zip_csv_and_reports(intl_case, caplog):
    from fed.core.finalize import finish_case

    case, acc, connect, _rows = intl_case
    res = run_attempt(case, acc, connect)
    assert res.status == "done" and res.saved == len(im.mails())
    expected = {hashlib.sha256(m.raw).hexdigest(): m for m in im.mails()}
    got = case.conn.execute("SELECT m.sha256, f.display_name, m.subject FROM message m "
                            "JOIN folder f ON f.id = m.folder_id").fetchall()
    assert {h for h, _f, _s in got} == set(expected)  # jede Mail bitgenau, auch die alten Kodierungen
    for h, folder_name, _subject in got:
        assert folder_name == expected[h].folder
    subjects = {s for _h, _f, s in got}
    for lang in im.LANGS:  # Betreff korrekt dekodiert (auch KOI8-R, ISO-2022-JP, GB18030, cp1256, cp1255)
        assert lang.subject in subjects or lang.subject.replace("–", "-") in subjects, lang.code

    cfg = Config()
    cfg.programm.ntp = False
    with caplog.at_level(logging.WARNING, logger="fpdf"):
        fin = finish_case(case, cfg, closing_note="Übergabe – 已移交 – تم التسليم – נמסר")
    assert not [r for r in caplog.records if "missing" in r.getMessage()], caplog.text
    z = fin.zips[0]
    assert z.verified
    with zipfile.ZipFile(z.path) as zf:
        names = zf.namelist()
        for m in im.mails():  # Ordnerstruktur im ZIP, Inhalt bitgenau
            prefix = f"{im.USER}/{m.folder}/"
            member = next(n for n in names if n.startswith(prefix) and n.endswith(".eml")
                          and hashlib.sha256(zf.read(n)).hexdigest() == hashlib.sha256(m.raw).hexdigest())
            assert member
        csv_text = zf.read("log.csv").decode("utf-8-sig")
    rows = list(csv.DictReader(io.StringIO(csv_text), delimiter=";"))
    assert {r["Folder"] for r in rows} >= {im.folder(lang) for lang in im.LANGS}
    assert any(r["Subject"] == "報告書の送付について（ご確認ください）" for r in rows)
    assert any("דוח_פרויקט.txt" in r["Attachment_Names"] for r in rows)  # Anlagenname (RFC 2231)
    assert any("အစီရင်ခံစာ.txt" in r["Attachment_Names"] for r in rows)

    txt = fin.report_txt.read_text(encoding="utf-8")
    for lang in im.LANGS:
        assert im.folder(lang) in txt and lang.subject.split(" ")[0] in txt
    import pypdfium2

    doc = pypdfium2.PdfDocument(fin.report_pdf)
    text = "".join(doc[k].get_textpage().get_text_range() for k in range(len(doc)))
    for word in ("Отчёт", "Извештај", "Račun", "Báo", "报告", "報告書", "Đurđevdan"):
        assert word in text, word


def test_viewer_and_content_filter(intl_case):
    from fed.core.acquisition import set_content_filter
    from fed.core.viewer import open_message

    case, acc, connect, _rows = intl_case
    # Teilsicherung nur mit Treffern im Betreff (Suche mit UTF-8 auf dem Server)
    set_content_filter(case, acc, {"subject": ["報告書", "تقرير"]})
    res = run_attempt(case, acc, connect)
    subjects = sorted(r[0] for r in case.conn.execute("SELECT subject FROM message"))
    assert res.status == "done" and len(subjects) == 4, subjects  # je 2 Mails Japanisch und Arabisch
    assert all("報告書" in s or "تقرير" in s for s in subjects)
    mid = case.conn.execute("SELECT id FROM message WHERE subject LIKE '%تقرير%' "
                            "AND subject LIKE '%–%'").fetchone()[0]
    v = open_message(case, mid)
    assert v.sha256_ok and "مرفق تقرير المشروع" in v.text and v.attachments[0].name == "تقرير_المشروع.txt"
    assert "dir=\"rtl\"" in v.html


MIXED = [  # gemischte Schreibrichtung, wie sie in Betreffs vorkommt
    "Betreff: تقرير المشروع 2026 – Ende",
    "Rechnung فاتورة 2026",
    "דוח 2026 Ende",
    "تقرير 2026",
    "Re: דוח פרויקט 2026 (PDF) – ok",
    "مرحبا Hello World 123 عالم",
    "Hallo שלום 12 עולם Welt",
]


def _visual(text: str, table: bool = False) -> str:
    """Zeichen des gesetzten PDFs von links nach rechts (über ihre Position) – so, wie man es sieht."""
    import pypdfium2
    from fpdf import FPDF

    from fed.core import pdftext

    pdf = FPDF()
    pdf.add_page()
    pdf.add_font("DejaVu", "", "src/fed/resources/fonts/DejaVuSans.ttf")
    pdftext.setup(pdf)
    pdf.set_font("DejaVu", "", 10)
    if table:
        with pdf.table(text_align="LEFT", first_row_as_headings=False) as t:
            t.row([text])
    else:
        pdf.multi_cell(0, 8, text)
    data = bytes(pdf.output())
    tp = pypdfium2.PdfDocument(data)[0].get_textpage()
    chars = sorted((tp.get_charbox(i)[0], tp.get_text_range(i, 1)) for i in range(tp.count_chars()))
    return "".join(c for _x, c in chars)


def _norm(s: str) -> str:
    # Leerraum zusammenfassen; Klammern: HarfBuzz spiegelt sie in RTL-Läufen (Glyphe), die Referenz nicht
    return " ".join(s.replace("‎", "").translate(str.maketrans("()", "||")).split())


@pytest.mark.parametrize("text", MIXED)
def test_pdf_bidi_order_matches_unicode_algorithm(text):
    """fpdf2 2.9 setzte Abschnitte gemischter Richtung in logischer Reihenfolge – korrigiert in
    fed.core.pdftext; Maßstab ist fpdf2s eigene, korrekte Umordnung nach Unicode UAX #9."""
    from fpdf.bidi import BidiParagraph

    expected = _norm(BidiParagraph(text).get_reordered_string())
    assert _norm(_visual(text)) == expected
    assert _norm(_visual(text, table=True)) == expected


def test_report_path_keeps_component_order():
    from fed.core.report import _ltr_path

    v = _norm(_visual(_ltr_path("intl@example.de/עברית/עבודה/20261110T080900Z_UID1.eml")))
    assert v == "intl@example.de/תירבע/הדובע/20261110T080900Z_UID1.eml"  # Teile in echter Reihenfolge


def test_rich_note_word_order_rtl_and_mixed():
    from fpdf import FPDF

    from fed.core import pdftext
    from fed.core.report import _bidi_visual
    from fed.core.richtext import Style

    pdf = FPDF()
    pdf.add_page()
    pdf.add_font("DejaVu", "", "src/fed/resources/fonts/DejaVuSans.ttf")
    pdftext.setup(pdf)
    pdf.set_font("DejaVu", "", 9)

    def order(segs, rtl):
        out = _bidi_visual(pdf, [(0, t, s, 0) for t, s in segs], rtl, 0, 180, lambda st: None)
        return [t for _x, t, _s, _w in out if t.strip()], out

    words, out = order([("Übergabe: ", Style()), ("דוח ", Style()), ("פרויקט", Style(background="#fff176")),
                        (" 2026", Style())], False)
    assert words == ["Übergabe:", "2026", "פרויקט", "דוח"]
    words, out = order([("تم ", Style()), ("التسليم", Style(bold=True)), (" إلى المحقق 2026 – Ende", Style())], True)
    assert words == ["Ende", "–", "2026", "المحقق", "إلى", "التسليم", "تم"]
    assert out[-1][0] + pdf.get_string_width(out[-1][1]) == pytest.approx(180, abs=0.5)  # rechtsbündig
    words, _ = order([("مرحبا Hello World 123 عالم", Style())], True)
    assert words == ["عالم", "Hello", "World", "123", "مرحبا"]


def test_shaping_only_when_needed():
    from fed.core.pdftext import needs_shaping

    assert needs_shaping("تقرير") and needs_shaping("דוח") and needs_shaping("အစီရင်ခံစာ")
    assert not needs_shaping("Отчёт · Račun · Báo cáo · 报告 · 報告書 · 👍 · Grüße")
