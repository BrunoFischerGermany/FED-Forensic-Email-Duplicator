"""PDF-Export einzelner Mails (PDF/A-3b, eingebettete .eml, Log-Eintrag, Kopie in exports/)."""

import hashlib
import json

from fed.core import mailpdf


def _ids(case, n=None):
    rows = case.conn.execute("SELECT id FROM message WHERE zip_path IS NOT NULL ORDER BY id").fetchall()
    ids = [r[0] for r in rows]
    return ids if n is None else ids[:n]


def test_html_to_text():
    t = mailpdf.html_to_text("<html><head><style>x{}</style></head><body><p>Hallo&nbsp;Welt</p>"
                             "<script>alert(1)</script><ul><li>eins</li><li>zwei</li></ul>a<br>b</body></html>")
    assert "Hallo" in t and "Welt" in t and "alert" not in t and "x{}" not in t
    assert "• eins" in t and "• zwei" in t and "a\nb" in t


def test_single_export_pdfa_embedded_logged(acquired, tmp_path):
    mid = _ids(acquired, 1)[0]
    out = tmp_path / "mail.pdf"
    sha = mailpdf.write(acquired, [mid], out)
    data = out.read_bytes()
    assert data.startswith(b"%PDF") and hashlib.sha256(data).hexdigest() == sha
    assert b"pdfaid:part" in data and b"/OutputIntents" in data and b"/EmbeddedFile" in data
    assert b"/AFRelationship /Source" in data
    copies = list((acquired.root / mailpdf.EXPORT_DIR).glob("*.pdf"))
    assert len(copies) == 1 and copies[0].read_bytes() == data
    row = acquired.conn.execute(
        "SELECT details FROM log WHERE event = 'mail_pdf_exported' ORDER BY id DESC").fetchone()
    d = json.loads(row[0])
    assert d["sha256"] == sha and d["messages"][0]["message_id"] == mid and d["messages"][0]["sha256_ok"]


def test_multi_export_and_unique_copy(acquired, tmp_path):
    ids = _ids(acquired, 3)
    assert len(ids) >= 2
    a = mailpdf.write(acquired, ids, tmp_path / "a.pdf", lang="en")
    mailpdf.write(acquired, ids, tmp_path / "a.pdf", lang="en")
    assert a
    assert len(list((acquired.root / mailpdf.EXPORT_DIR).glob("*.pdf"))) == 2
    assert (tmp_path / "a.pdf").read_bytes().count(b"/AFRelationship /Source") == len(ids)


def test_options_off(acquired, tmp_path):
    mid = _ids(acquired, 1)[0]
    out = tmp_path / "m.pdf"
    mailpdf.write(acquired, [mid], out, full_headers=False, received=False, embed_eml=False, keep_copy=False)
    assert b"/EmbeddedFile" not in out.read_bytes()
    assert not (acquired.root / mailpdf.EXPORT_DIR).exists()


def test_export_name(acquired):
    from fed.core import viewer
    v = viewer.open_message(acquired, _ids(acquired, 1)[0], log=False)
    name = mailpdf.export_name(v)
    assert name.endswith(".pdf") and name.startswith("A-01_") and "uid" in name


def test_closed_case_no_copy(acquired, tmp_path, monkeypatch):
    from fed.core.case import Case

    monkeypatch.setattr(Case, "closed", property(lambda self: True))
    mailpdf.write(acquired, _ids(acquired, 1), tmp_path / "x.pdf")
    assert not (acquired.root / mailpdf.EXPORT_DIR).exists()
    assert mailpdf.language(acquired) in ("de", "en")


def test_pdfa_samples_for_verapdf(acquired, tmp_path):
    """Muster-PDFs für die veraPDF-Prüfung in der CI (FED_PDFA_OUT gesetzt); sonst nur Grundprüfung."""
    import os
    from pathlib import Path

    from fed.core.report import write_pdf

    out = Path(os.environ.get("FED_PDFA_OUT") or tmp_path)
    out.mkdir(parents=True, exist_ok=True)
    for lang in ("de", "en"):
        write_pdf(acquired, out / f"report-{lang}.pdf", lang)
        mailpdf.write(acquired, _ids(acquired), out / f"mails-{lang}.pdf", lang, keep_copy=False)
    for p in out.glob("*.pdf"):
        data = p.read_bytes()
        assert b"pdfaid:part" in data and b"/OutputIntents" in data, p.name
