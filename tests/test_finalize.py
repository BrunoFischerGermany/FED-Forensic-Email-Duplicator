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
    assert {z.path.name, "log.csv", "report.txt", "report.pdf"} <= outputs
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
    assert include_mail_list(10**6, "liste") and not include_mail_list(1, "ordner")
    assert include_mail_list(10**6, "immer") and not include_mail_list(1, "nie")  # alte Werte
    full = render_txt(case, "de", "liste")
    short = render_txt(case, "de", "auto", 5)
    assert "SHA-256 " in full and full.count("UID ") > 5
    assert "steht in log.csv" in short and short.count("UID ") == 0
    assert "Unterschrift" not in full and "Unterschrift" not in short
    # Ordnerstruktur als Baum mit Anzahl, auch ohne Liste
    assert "  └ Rechnungen" in short and "  └ 2025" in short and "Leer" in short


def test_delete_work_dir_after_verified_zip(acquired, test_mails):
    from fed.core.report import render_txt

    case = acquired
    res = finish_case(case, Config(), delete_work=True)
    assert res.zip.verified and not case.work_dir.exists()
    assert (case.root / "discarded").exists()
    det = case.conn.execute("SELECT details FROM log WHERE event = 'work_dir_deleted'").fetchone()[0]
    assert f'"files": {len(test_mails)}' in det
    txt = render_txt(case)
    assert "Arbeitsordner" in txt and "gelöscht nach erfolgreicher ZIP-Verifikation" in txt
    assert verify_zip(case, res.zip.path).verified  # Prüfung jetzt gegen das ZIP


def test_keep_work_dir_by_default(acquired):
    case = acquired
    finish_case(case, Config())
    assert case.work_dir.exists()


def test_finish_with_rfc3161_timestamp(acquired):
    from validation.fake_tsa import FakeTsa, available

    if not available():
        pytest.skip("openssl ts nicht verfügbar")
    case = acquired
    cfg = Config()
    with FakeTsa() as tsa:
        cfg.tsa_url = tsa.url
        res = finish_case(case, cfg, language="en")
    assert res.timestamp and not res.timestamp_error
    assert res.timestamp.hashed_message == res.zip.hashes["sha256"]
    tsr = res.zip.path.with_name(res.zip.path.name + ".tsr")
    assert tsr.exists()
    outputs = {r[0] for r in case.conn.execute("SELECT file_name FROM output")}
    assert tsr.name in outputs
    txt = res.report_txt.read_text(encoding="utf-8")
    assert "Timestamp (RFC 3161)" in txt and res.timestamp.serial in txt and "openssl ts -verify" in txt


def test_finish_continues_when_timestamp_fails(acquired):
    case = acquired
    cfg = Config()
    cfg.tsa_url = "http://127.0.0.1:9/"
    cfg.programm.socket_timeout_s = 2
    res = finish_case(case, cfg)
    assert res.zip.verified and res.timestamp is None and "nicht erreichbar" in res.timestamp_error
    assert case.closed
    assert "fehlgeschlagen" in res.report_txt.read_text(encoding="utf-8")


def test_report_overview_per_account_and_notes(tmp_path, imap_server, test_mails):
    """Mehrere Konten: Gesamtbericht mit Übersicht und Abschnitt je Konto, Einzelbericht je Konto,
    Case-Anmerkungen und Konto-Notizen im Bericht."""
    from fed.core.acquisition import AccountSpec, register_account, run_attempt, scan, set_account_notes
    from fed.core.case import CaseInfo
    from fed.core.imap_reader import ReadOnlyImap
    from validation import testmailbox

    def connector(user, pw):
        def c():
            i = ReadOnlyImap(imap_server.host, imap_server.port, cafile=str(imap_server.cafile))
            i.connect()
            i.login_password(user, pw)
            return i
        return c

    case = Case.create(tmp_path, CaseInfo("2026-0099", "Tester", "Beschluss AG Musterstadt 12 Gs 34/26\nEilt"))
    ids = []
    for user, pw, ev, note in [(imap_server.user, imap_server.password, "A-01", "Zugangsdaten vom Beschuldigten"),
                               (testmailbox.UMLAUT_USER, testmailbox.UMLAUT_PASSWORD, "A-02", "")]:
        c = connector(user, pw)
        imap = c()
        acc = register_account(case, AccountSpec(user, ev, imap_server.host, imap_server.port, notes=note), imap)
        scan(case, acc, imap)
        imap.logout()
        run_attempt(case, acc, c, cfg=Config())
        ids.append(acc)
    assert set_account_notes(case, ids[1], "Postfach der Praxis")
    assert not set_account_notes(case, ids[1], "Postfach der Praxis  ")
    changed = case.conn.execute("SELECT details FROM log WHERE event = 'account_notes_changed'").fetchall()
    assert len(changed) == 1 and "Postfach der Praxis" in changed[0][0]

    res = finish_case(case, Config(), language="de")
    txt = res.report_txt.read_text(encoding="utf-8")
    assert "Anmerkungen zum Case" in txt and "12 Gs 34/26" in txt
    assert "Kontenübersicht" in txt and "2 Konten · 2 vollständig gesichert" in txt
    assert "Zugangsdaten vom Beschuldigten" in txt and "Postfach der Praxis" in txt
    names = sorted(p.name for p in res.account_reports)
    assert names == ["report_A-01.pdf", "report_A-01.txt", "report_A-02.pdf", "report_A-02.txt"]
    one = (case.root / "report_A-02.txt").read_text(encoding="utf-8")
    assert testmailbox.UMLAUT_USER in one and "Postfach der Praxis" in one and "Teil des Case 2026-0099" in one
    assert "Zugangsdaten vom Beschuldigten" not in one and f"({len(test_mails)})" in one
    assert imap_server.user + "/" not in one  # nur Mails dieses Kontos
    for p in res.account_reports:
        if p.suffix == ".pdf":
            assert p.read_bytes().startswith(b"%PDF")
    case.close()


def _two_accounts(tmp_path, imap_server):
    from fed.core.acquisition import AccountSpec, register_account, run_attempt, scan
    from fed.core.case import CaseInfo
    from fed.core.imap_reader import ReadOnlyImap
    from validation import testmailbox

    case = Case.create(tmp_path, CaseInfo("2026-0100", "Tester"))
    for user, pw, ev in [(imap_server.user, imap_server.password, "A-01"),
                         (testmailbox.UMLAUT_USER, testmailbox.UMLAUT_PASSWORD, "A-02")]:
        def c(user=user, pw=pw):
            i = ReadOnlyImap(imap_server.host, imap_server.port, cafile=str(imap_server.cafile))
            i.connect()
            i.login_password(user, pw)
            return i
        imap = c()
        acc = register_account(case, AccountSpec(user, ev, imap_server.host, imap_server.port), imap)
        scan(case, acc, imap)
        imap.logout()
        run_attempt(case, acc, c, cfg=Config())
    return case


def test_one_zip_per_account(tmp_path, imap_server, test_mails):
    from validation import testmailbox
    from validation.fake_tsa import FakeTsa, available

    case = _two_accounts(tmp_path, imap_server)
    assert case.zip_mode() is None
    case.set_zip_mode("je_konto", source="test")
    assert case.zip_mode() == "je_konto"
    cfg = Config()
    if available():
        tsa = FakeTsa().__enter__()
        cfg.tsa_url = tsa.url
    try:
        res = finish_case(case, cfg, language="de", delete_work=True)
    finally:
        if available():
            tsa.__exit__(None, None, None)
    assert res.zip_mode == "je_konto" and len(res.zips) == 2
    names = [z.path.name for z in res.zips]
    assert names[0].startswith("2026-0100_A-01_") and names[1].startswith("2026-0100_A-02_")
    want = {hashlib.sha256(m.raw).hexdigest() for m in test_mails}
    for z, user in zip(res.zips, [imap_server.user, testmailbox.UMLAUT_USER], strict=True):
        assert z.verified and z.files == len(test_mails)
        with zipfile.ZipFile(z.path) as zf:
            emls = [n for n in zf.namelist() if n.endswith(".eml")]
            assert {n.split("/")[0] for n in emls} == {user}
            assert {hashlib.sha256(zf.read(n)).hexdigest() for n in emls} == want
            assert zf.namelist()[0] == "log.csv" and user in zf.read("log.csv").decode("utf-8-sig")
        assert z.path.with_name(z.path.name + ".sha256").exists()
        if available():
            assert z.path.with_name(z.path.name + ".tsr").exists()
    assert (case.root / "log.csv").exists() and (case.root / "log_A-01.csv").exists()
    assert not case.work_dir.exists()  # erst nach Verifikation beider ZIPs gelöscht
    txt = res.report_txt.read_text(encoding="utf-8")
    assert "ein ZIP je Konto" in txt and names[0] in txt and names[1] in txt
    one = (case.root / "report_A-02.txt").read_text(encoding="utf-8")
    assert names[1] in one and names[0] not in one  # Einzelbericht nennt nur das eigene ZIP
    if available():
        assert txt.count("Zeitstempel (RFC 3161) –") == 2 and one.count("Zeitstempel (RFC 3161)") == 1
    case.close()


def test_zip_mode_defaults(tmp_path, imap_server):
    from fed.core.finalize import zip_mode_for

    case = _two_accounts(tmp_path, imap_server)
    cfg = Config()
    assert zip_mode_for(case, cfg) == "gesamt"  # „fragen“ ohne Antwort -> ein ZIP
    cfg.programm.zip_aufteilung = "je_konto"
    assert zip_mode_for(case, cfg) == "je_konto"
    case.set_zip_mode("gesamt")
    assert zip_mode_for(case, cfg) == "gesamt"  # Entscheidung im Case hat Vorrang
    with pytest.raises(ValueError):
        case.set_zip_mode("irgendwas")
    assert case.conn.execute("SELECT COUNT(*) FROM log WHERE event = 'zip_mode_set'").fetchone()[0] == 1
    case.close()
