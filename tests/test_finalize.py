import hashlib
import struct
import zipfile
from datetime import UTC, datetime

import pytest

from fed.core.acquisition import AccountSpec, register_account, run_attempt, scan
from fed.core.case import Case, CaseInfo
from fed.core.config import Config
from fed.core.finalize import finish_case, verify_zip
from fed.core.imap_reader import ReadOnlyImap


@pytest.fixture
def acquired(tmp_path, imap_server):
    def connect():
        c = ReadOnlyImap(imap_server.host, imap_server.port, cafile=str(imap_server.cafile))
        c.connect()
        c.login_password(imap_server.user, imap_server.password)
        return c

    logo = tmp_path / "logo.png"
    logo.write_bytes(bytes.fromhex(
        "89504e470d0a1a0a0000000d49484452000000010000000108020000009077"
        "53de0000000c4944415408d763f8cfc00000030101009c18e5d20000000049454e44ae426082"))
    case = Case.create(tmp_path, CaseInfo("2026-0042", "Max Mustermann", "Test"))
    case.store_config_snapshot('[agentur]\nname = "Muster Forensik GmbH"\nadresse = "Musterstr. 1"\n', logo)
    imap = connect()
    acc = register_account(case, AccountSpec(imap_server.user, "A-01", imap_server.host, imap_server.port), imap)
    scan(case, acc, imap)
    imap.logout()
    run_attempt(case, acc, connect, cfg=Config())
    yield case
    case.close()


def test_finish_creates_verified_zip_and_reports(acquired, test_mails):
    case = acquired
    res = finish_case(case, Config())
    z = res.zip
    assert z.verified and z.files == len(test_mails) and not z.problems
    side = z.path.with_name(z.path.name + ".sha256").read_text()
    assert side.split()[0] == hashlib.sha256(z.path.read_bytes()).hexdigest() == z.hashes["sha256"]
    assert {"md5", "sha512"} <= set(z.hashes)

    by_hash = {hashlib.sha256(m.raw).hexdigest(): m for m in test_mails}
    with zipfile.ZipFile(z.path) as zf:
        names = zf.namelist()
        assert names[0] == "log.csv"
        assert "test@example.de/Leer/" in names  # leerer Ordner bleibt erhalten
        for info in zf.infolist():
            if not info.filename.endswith(".eml"):
                continue
            assert info.filename.startswith("test@example.de/")
            m = by_hash[hashlib.sha256(zf.read(info)).hexdigest()]
            exp = m.received.astimezone(UTC)
            # klassische ZIP-Zeit: 2-Sekunden-Raster; die genaue Zeit steht im Extended Timestamp
            assert info.date_time == (exp.year, exp.month, exp.day, exp.hour, exp.minute, exp.second // 2 * 2)
            tag, size, flags, mtime = struct.unpack("<HHBl", info.extra[:9])
            assert (tag, size, flags) == (0x5455, 5, 1) and datetime.fromtimestamp(mtime, UTC) == exp
        csv_text = zf.read("log.csv").decode("utf-8-sig")
    assert "nur BCC" in csv_text and "Rechnung.pdf" in csv_text and csv_text.count("\n") == len(test_mails) + 1

    assert case.closed
    txt = res.report_txt.read_text(encoding="utf-8")
    assert "Muster Forensik GmbH" in txt and z.hashes["sha256"] in txt and "Entwürfe" in txt
    assert res.report_pdf.read_bytes()[:5] == b"%PDF-"
    outputs = {r[0] for r in case.conn.execute("SELECT file_name FROM output")}
    assert {z.path.name, "log.csv", "bericht.txt", "bericht.pdf"} <= outputs
    with pytest.raises(ValueError):
        run_attempt(case, 1, lambda: None)
    with pytest.raises(ValueError):
        finish_case(case)


def test_verify_detects_tampering(acquired, tmp_path):
    case = acquired
    res = finish_case(case, Config())
    bad = tmp_path / "manipuliert.zip"
    with zipfile.ZipFile(res.zip.path) as src, zipfile.ZipFile(bad, "w") as dst:
        done = False
        for info in src.infolist():
            data = src.read(info)
            if info.filename.endswith(".eml") and not done:
                data, done = data.replace(b"Subject:", b"Subjekt:"), True
            dst.writestr(info, data)
    v = verify_zip(case, bad)
    assert not v.verified and any("Hash abweichend" in p for p in v.problems)


def test_logo_validation(tmp_path):
    case = Case.create(tmp_path, CaseInfo("L-1", "E"))
    fake = tmp_path / "logo.gif"
    fake.write_bytes(b"GIF89a....")
    with pytest.raises(ValueError):
        case.store_config_snapshot("", fake)
    case.store_config_snapshot("", None)
    assert case.logo() is None
    case.close()


def test_report_mail_list_modes(acquired):
    from fed.core.report import include_mail_list, render_txt

    case = acquired
    finish_case(case, Config())
    assert include_mail_list(1000, "auto", 500) is False and include_mail_list(400, "auto", 500) is True
    assert include_mail_list(10**6, "immer") and not include_mail_list(1, "nie")
    full = render_txt(case, "de", "immer")
    short = render_txt(case, "de", "auto", 5)
    assert "SHA-256 " in full and full.count("UID ") > 5
    assert "steht in log.csv" in short and short.count("UID ") == 0
    assert "Unterschrift" not in full and "Unterschrift" not in short
