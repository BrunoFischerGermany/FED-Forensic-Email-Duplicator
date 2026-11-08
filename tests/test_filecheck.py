import io
import zipfile

from fed.core.filecheck import check, decode_text, hexdump, identify
from validation import testmailbox

PNG = testmailbox.PNG
EXE = b"MZ" + b"\x90" * 0x3A + (0x80).to_bytes(4, "little") + b"\x00" * 0x40 + b"PE\x00\x00" + b"\x00" * 64


def _zip(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for n, d in files.items():
            z.writestr(n, d)
    return buf.getvalue()


def codes(fc):
    return [f.code for f in fc.findings]


def test_plain_pdf_is_ok():
    fc = check("Rechnung.pdf", testmailbox.PDF, "application/pdf")
    assert fc.fmt.key == "pdf" and fc.detail == "PDF 1.4" and fc.ext_ok and fc.mime_ok
    assert fc.verdict == "ok" and fc.fmt.preview == "pdf"
    assert len(fc.sha256) == 64 and len(fc.md5) == 32 and len(fc.sha1) == 40


def test_pdf_active_content_and_embedded_file():
    data = b"%PDF-1.7\n1 0 obj<</OpenAction 2 0 R/Names<</JavaScript 3 0 R>>>>endobj\n4 0 obj<</Type/EmbeddedFile>>"
    fc = check("info.pdf", data, "application/pdf")
    assert "pdf_active" in codes(fc) and "pdf_embedded_file" in codes(fc) and fc.verdict == "warn"
    assert "JavaScript" in fc.findings[0].args[0] and "OpenAction" in fc.findings[0].args[0]


def test_exe_disguised_as_pdf():
    fc = check("Rechnung.pdf", EXE, "application/pdf")
    assert fc.fmt.key == "exe" and fc.detail == "PE" and fc.ext_ok is False and fc.mime_ok is False
    assert codes(fc)[0] == "executable" and "ext_mismatch" in codes(fc)


def test_double_extension_and_bidi():
    fc = check("Rechnung.pdf.exe", EXE)
    assert "double_extension" in codes(fc) and fc.ext_ok
    fc2 = check("Rechnung‮fdp.exe", EXE)
    assert "bidi_name" in codes(fc2)
    fc3 = check("Bericht.pdf" + " " * 20 + ".scr", EXE)
    assert "padded_name" in codes(fc3)


def test_ooxml_and_macros():
    docx = _zip({"[Content_Types].xml": b"<Types/>", "word/document.xml": b"<w/>"})
    fc = check("Vertrag.docx", docx, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
    assert fc.fmt.key == "docx" and fc.ext_ok and fc.mime_ok and fc.verdict == "ok"
    docm = _zip({"[Content_Types].xml": b"<Types/>", "word/document.xml": b"<w/>", "word/vbaProject.bin": b"x"})
    fc = check("Vertrag.docx", docm)
    assert fc.fmt.key == "docm" and "macros" in codes(fc) and "ext_mismatch" in codes(fc)
    rels = (b'<Relationships><Relationship Type=".../attachedTemplate" Target="http://x/t.dotm" '
            b'TargetMode="External"/></Relationships>')
    tpl = _zip({"[Content_Types].xml": b"<Types/>", "word/document.xml": b"<w/>",
                "word/_rels/settings.xml.rels": rels})
    assert "remote_template" in codes(check("a.docx", tpl))


def test_zip_with_executable_and_encrypted_flag():
    z = _zip({"liesmich.txt": b"hi", "setup.exe": EXE})
    fc = check("daten.zip", z, "application/zip")
    assert fc.fmt.key == "zip" and "archive_contains_executable" in codes(fc) and "setup.exe" in fc.findings[0].args[0]


def test_images_text_and_scripts():
    assert identify(PNG)[0].key == "png"
    assert check("logo.png", PNG, "image/png").verdict == "ok"
    assert check("logo.jpg", PNG, "image/jpeg").ext_ok is False
    csv = check("daten.csv", b"a;b\n1;2\n", "text/csv")
    assert csv.fmt.key == "text" and csv.ext_ok and csv.mime_ok and csv.verdict == "ok"
    js = check("rechnung.js", b"var s = new ActiveXObject('WScript.Shell');\n", "text/plain")
    assert "script" in codes(js) and js.verdict == "warn"
    assert identify(b"BEGIN:VCALENDAR\r\nVERSION:2.0\r\n")[0].key == "ics"
    assert identify(b"<!DOCTYPE html><html><body>x</body></html>")[0].key == "html"
    assert identify(b"<html><hta:application id=x>")[0].key == "hta"
    assert identify(b"<?xml version='1.0'?><svg xmlns='http://www.w3.org/2000/svg'/>")[0].key == "svg"
    eml = b"From: a@b\r\nTo: c@d\r\nSubject: x\r\nDate: Mon, 1 Jan 2026 00:00:00 +0000\r\n\r\nText"
    assert identify(eml)[0].key == "eml"
    assert identify("Grüße".encode("utf-16"))[0].key == "text"


def test_misc_formats_and_helpers():
    assert check("leer.txt", b"").fmt.key == "empty"
    assert identify(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 100 + "_VBA_PROJECT".encode("utf-16-le"))[2][0].code == "macros"
    assert identify(b"\x7fELF\x02\x01\x01")[0].key == "elf"
    assert identify(b"L\x00\x00\x00\x01\x14\x02\x00" + b"\x00" * 60)[0].executable
    assert identify(b"Rar!\x1a\x07\x01\x00")[0].key == "rar"
    iso = bytearray(0x9000)
    iso[0x8001:0x8006] = b"CD001"
    assert "disk_image" in codes(check("Rechnung.iso", bytes(iso)))
    import hashlib

    rnd = b"".join(hashlib.sha256(bytes([i % 256, i // 256])).digest() for i in range(200))
    fc = check("x.dat", rnd)
    assert fc.fmt.key == "unknown" and "high_entropy" in codes(fc) and fc.ext_ok is None
    dump = hexdump(b"ABC\x00" * 5)
    assert dump.startswith("00000000  41 42 43 00") and "|ABC.ABC.ABC.ABC.|" in dump
    assert decode_text("Grüße".encode("cp1252")) == "Grüße"
    assert decode_text("Grüße".encode()) == "Grüße"
