"""Logo: PNG/JPG/SVG erkennen, SVG für den PDF-Bericht in PNG umwandeln."""

import hashlib
import json

import pytest

from fed.core import logo
from fed.core.case import Case, CaseInfo

SVG = (b'<?xml version="1.0" encoding="UTF-8"?>\n<!-- Logo -->\n'
       b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 100" width="200" height="100">'
       b'<rect x="0" y="0" width="200" height="100" fill="#1f6feb"/>'
       b'<circle cx="50" cy="50" r="30" fill="#ffffff"/></svg>')


@pytest.mark.parametrize("data,mime", [
    (b"\x89PNG\r\n\x1a\n...", "image/png"), (b"\xff\xd8\xff\xe0", "image/jpeg"), (SVG, "image/svg+xml"),
    (b'<svg xmlns="http://www.w3.org/2000/svg"/>', "image/svg+xml"),
    (b"\xef\xbb\xbf<svg>", "image/svg+xml"),
    (b'<!DOCTYPE svg PUBLIC "-//W3C//DTD SVG 1.1//EN" "x.dtd">\n<svg>', "image/svg+xml"),
    (b"<html><svg></svg></html>", None), (b"GIF89a", None), (b"", None)])
def test_detect(data, mime):
    assert logo.detect(data) == mime


def test_svg_to_png_keeps_aspect_and_colours():
    from PySide6.QtGui import QImage

    png = logo.svg_to_png(SVG)
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    img = QImage.fromData(png)
    assert (img.width(), img.height()) == (logo.SVG_PIXELS, logo.SVG_PIXELS // 2)
    assert img.pixelColor(5, 5).name() == "#1f6feb"  # Rechteck
    assert img.pixelColor(img.width() // 4, img.height() // 2).name() == "#ffffff"  # Kreis


def test_svg_invalid():
    with pytest.raises(ValueError):
        logo.svg_to_png(b"<svg><rect")


def test_case_stores_converted_svg_and_logs_original_hash(tmp_path):
    f = tmp_path / "agentur.svg"
    f.write_bytes(SVG)
    case = Case.create(tmp_path / "cases", CaseInfo("AZ 1/26", "Tester"))
    case.store_config_snapshot("", f)
    blob, mime, sha = case.logo()
    assert mime == "image/png" and blob[:8] == b"\x89PNG\r\n\x1a\n" and sha == hashlib.sha256(blob).hexdigest()
    name = case.conn.execute("SELECT logo_file FROM config_snapshot").fetchone()[0]
    assert name == "agentur.png"
    row = case.conn.execute("SELECT details FROM log WHERE event = 'logo_converted'").fetchone()
    d = json.loads(row[0])
    assert d["svg"] == "agentur.svg" and d["svg_sha256"] == hashlib.sha256(SVG).hexdigest()
    assert d["png_sha256"] == sha
    assert case.verify_log()[0]
    case.close()


def test_case_rejects_other_formats(tmp_path):
    f = tmp_path / "logo.gif"
    f.write_bytes(b"GIF89a....")
    case = Case.create(tmp_path / "cases", CaseInfo("AZ 2/26", "Tester"))
    with pytest.raises(ValueError, match="SVG"):
        case.store_config_snapshot("", f)
    case.close()


def test_report_pdf_with_svg_logo(tmp_path):
    from fed.core.report import write_pdf

    f = tmp_path / "agentur.svg"
    f.write_bytes(SVG)
    case = Case.create(tmp_path / "cases", CaseInfo("AZ 3/26", "Tester"))
    case.store_config_snapshot('[agentur]\nname = "Muster"\n', f)
    out = tmp_path / "r.pdf"
    write_pdf(case, out)
    assert out.read_bytes()[:4] == b"%PDF" and b"/Subtype /Image" in out.read_bytes()
    case.close()
