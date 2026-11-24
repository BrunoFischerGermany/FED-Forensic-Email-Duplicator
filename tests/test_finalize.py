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


def test_finish_creates_verified_zip_and_reports(acquired, test_mails):
    case = acquired
    res = finish_case(case, Config())
    z = res.zip
    assert z.verified and z.files == len(test_mails) and not z.problems
    steps = [h["step"] for h in case.host_history()]
    assert steps[0] == "create" and "attempt" in steps and steps[-1] == "finalize"
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
    assert "bcc_only" in csv_text and "Rechnung.pdf" in csv_text and csv_text.count("\n") == len(test_mails) + 1

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
    assert not v.verified and any(p.startswith("hash_mismatch: ") for p in v.problems)


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
    assert "steht in log.csv" in short and short.count("\n       SHA-256 ") == 0  # keine Mail-Liste
    assert full.count("\n       SHA-256 ") > 5
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
    mts, merr = res.manifest_timestamp  # zweiter Zeitstempel über das Manifest (Anker für die Log-Kette)
    assert mts is not None and not merr and mts.hashed_message == res.manifest_hashes["sha256"]
    assert res.manifest.with_name("manifest.json.tsr").exists()
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
    assert res.manifest.exists() and res.manifest_timestamp[0] is None  # Dienst nicht erneut abwarten
    assert "übersprungen" in res.manifest_timestamp[1]
    assert "fehlgeschlagen" in res.report_txt.read_text(encoding="utf-8")


def test_finish_does_not_hang_on_stalling_tsa(acquired, monkeypatch):
    """Ein TSA, der die Verbindung annimmt aber nicht antwortet, darf den Abschluss nicht blockieren:
    harte Gesamtfrist (socket_timeout_s + 2 s), danach geht es ohne Zeitstempel weiter."""
    import time

    from fed.core import timestamp as tsmod

    def _stall(*a, **k):
        time.sleep(30)  # länger als die Frist – würde ohne harte Grenze minutenlang hängen

    monkeypatch.setattr(tsmod, "request_timestamp", _stall)
    case = acquired
    cfg = Config()
    cfg.tsa_url = "http://example.invalid/tsa"
    cfg.programm.socket_timeout_s = 1          # Frist also ~3 s
    t0 = time.monotonic()
    res = finish_case(case, cfg)
    elapsed = time.monotonic() - t0
    assert elapsed < 15                         # nicht minutenlang hängen
    assert res.zip.verified and res.timestamp is None
    assert "nicht innerhalb" in res.timestamp_error
    assert case.closed


def test_report_overview_per_account_and_notes(tmp_path, imap_server, test_mails):
    """Mehrere Konten: Gesamtbericht mit Übersicht und Abschnitt je Konto, Einzelbericht je Konto,
    Case-Anmerkungen und Konto-Notizen im Bericht."""
    from fed.core.acquisition import run_attempt, set_account_notes
    from fed.core.case import CaseInfo
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
    assert "Anmerkungen zum Fall" in txt and "12 Gs 34/26" in txt
    assert "Kontenübersicht" in txt and "2 Konten · 2 vollständig gesichert" in txt
    assert "Zugangsdaten vom Beschuldigten" in txt and "Postfach der Praxis" in txt
    names = sorted(p.name for p in res.account_reports)
    assert names == ["report_A-01.pdf", "report_A-01.txt", "report_A-02.pdf", "report_A-02.txt"]
    one = (case.root / "report_A-02.txt").read_text(encoding="utf-8")
    assert testmailbox.UMLAUT_USER in one and "Postfach der Praxis" in one and "Teil des Falls 2026-0099" in one
    assert "Zugangsdaten vom Beschuldigten" not in one and f"({len(test_mails)})" in one
    assert imap_server.user + "/" not in one  # nur Mails dieses Kontos
    for p in res.account_reports:
        if p.suffix == ".pdf":
            assert p.read_bytes().startswith(b"%PDF")
    case.close()


def _two_accounts(tmp_path, imap_server):
    from fed.core.acquisition import run_attempt
    from fed.core.case import CaseInfo
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


def test_account_reports_reuse_collected_data(tmp_path, imap_server, test_mails, monkeypatch):
    """Einzelberichte je Konto leiten ihre Daten aus der Gesamtsammlung ab: collect() läuft nur EINMAL,
    nicht erneut (und früher sogar doppelt) je Konto."""
    from fed.core import report as rep

    case = _two_accounts(tmp_path, imap_server)
    real_collect = rep.collect
    calls: list = []

    def counting(c, account_id=None):
        calls.append(account_id)
        return real_collect(c, account_id)

    monkeypatch.setattr(rep, "collect", counting)
    rep.write_reports(case, "de")

    assert calls == [None]                                   # genau einmal gesammelt, kein Re-collect je Konto
    names = sorted(p.name for p in rep.account_reports(case))
    assert names == ["report_A-01.pdf", "report_A-01.txt", "report_A-02.pdf", "report_A-02.txt"]
    # Inhalt stimmt weiterhin: Einzelbericht enthält nur Mails seines Kontos
    one = (case.root / "report_A-01.txt").read_text(encoding="utf-8")
    assert f"({len(test_mails)})" in one
    for p in rep.account_reports(case):
        if p.suffix == ".pdf":
            assert p.read_bytes().startswith(b"%PDF")
    case.close()


def test_reports_dns_csv_by_default_both_reports(tmp_path, imap_server, test_mails):
    """Vorgabe dns=csv: Gesamt- UND Einzelbericht verweisen auf die CSV (Einzel wie Gesamt),
    keine DNS-Tabelle. dns_snapshot.csv wird mit Inhalt erzeugt."""
    from fed.core import report as rep
    from fed.core.finalize import write_dns_snapshot_csv

    case = _two_accounts(tmp_path, imap_server)
    acc_id, ev = case.conn.execute("SELECT id, evidence_number FROM account ORDER BY id LIMIT 1").fetchone()
    case.conn.execute("DELETE FROM domain_dns")  # deterministisch
    case.conn.execute(
        "INSERT INTO domain_dns (account_id, domain, rtype, name, value, sha256, status, fetched_utc) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (acc_id, "ex.example", "MX", "ex.example", "10 mail.ex.example.", "abc123def", "ok", "2026-01-01T00:00:00Z"))
    case.conn.commit()

    assert write_dns_snapshot_csv(case, case.root / "dns_snapshot.csv") == 1
    assert "10 mail.ex.example." in (case.root / "dns_snapshot.csv").read_text(encoding="utf-8")

    rep.write_reports(case, "de")                          # Vorgabe dns_mode="csv"
    overall = (case.root / "report.txt").read_text(encoding="utf-8")
    single = (case.root / f"report_{ev}.txt").read_text(encoding="utf-8")
    for txt in (overall, single):                          # Einzel wie Gesamt
        assert "dns_snapshot.csv" in txt
        assert "10 mail.ex.example." not in txt            # kein Tabellenwert im Bericht
    case.close()


def test_stray_file_warning_attributed_to_account(tmp_path, imap_server, test_mails):
    """Eine verwaiste Datei im Arbeitsordner eines Kontos wird als Warnung MIT Konto geloggt,
    sodass sie auch im Einzelbericht des Kontos erscheint – nicht nur im Gesamtbericht."""
    from fed.core.finalize import build_zip
    from fed.core.naming import sanitize_component
    from fed.core.report import account_subset, collect

    case = _two_accounts(tmp_path, imap_server)
    acc_id, email = case.conn.execute("SELECT id, email FROM account ORDER BY id LIMIT 1").fetchone()
    folder = case.work_dir / sanitize_component(email) / "Sent"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "orphan_UID00000099.eml").write_bytes(b"From: x\r\n\r\nhi")  # nicht in den gültigen Mails

    build_zip(case, account_id=acc_id)

    details = [r[0] for r in case.conn.execute("SELECT details FROM log WHERE event = 'zip_file_not_in_case'")]
    assert details and any('"account_id"' in dt and str(acc_id) in dt for dt in details)

    sub = account_subset(collect(case, None), acc_id, case)   # Konto-Bericht sieht die Warnung
    assert any(x["event"] == "zip_file_not_in_case" for x in sub["issues"])
    case.close()


def test_report_timestamp_qualified_label(acquired):
    """ts_qualified steuert die Zeitstempel-Überschrift: „Qualifiziert" nur bei eIDAS-Dienst."""
    from fed.core.report import render_txt

    case = acquired
    case.log("WARNING", "timestamp_failed",
             {"file": "x.zip.tsr", "tsa": "https://tsr.open-tsa.eu", "error": "kaputt"})
    qualified = render_txt(case, ts_qualified=True)
    neutral = render_txt(case, ts_qualified=False)
    assert "Qualifizierter Zeitstempel (RFC 3161)" in qualified
    assert "Qualifizierter Zeitstempel" not in neutral
    assert "Zeitstempel (RFC 3161)" in neutral


def test_finish_page_lists_each_zip_and_account_reports(tmp_path, imap_server, test_mails):
    """Abschlussseite bei „ein ZIP je Konto": jede ZIP-/Prüf-/Hash-Zeile je Konto (nichts überschrieben),
    voller Hash im Tooltip, „Gesamtbericht öffnen" plus je Konto „Bericht <E-Mail> öffnen"."""
    from PySide6.QtWidgets import QApplication, QPushButton

    from fed.gui.assistant import FinishPage, State

    qapp = QApplication.instance() or QApplication([])
    case = _two_accounts(tmp_path, imap_server)
    cfg = Config()
    cfg.programm.zip_aufteilung = "je_konto"
    res = finish_case(case, cfg)
    st = State(cfg)
    st.case = case
    p = FinishPage(st)
    p._done(res)

    keys = set(p.check_kv.values)
    assert {"ZIP gepackt · A-01", "ZIP gepackt · A-02", "ZIP verifiziert · A-01", "ZIP verifiziert · A-02"} <= keys
    full = res.zips[0].hashes["sha256"]
    lbl = p.hash_kv.values["SHA-256 · A-01"]
    assert lbl.text().replace("\n", "") == full and full in lbl.toolTip()   # komplett, nichts abgeschnitten
    assert p.open_pdf.text() == "Gesamtbericht öffnen"
    emails = [e for (e,) in case.conn.execute("SELECT email FROM account ORDER BY id")]
    assert [b.text() for b in p.acc_reports.findChildren(QPushButton)] == [f"Bericht {e} öffnen" for e in emails]

    p._no_report()                                                     # z. B. erneuter Lauf/Fehler
    qapp.processEvents()
    assert p.open_pdf.text() == "Bericht öffnen"
    p.deleteLater()                                                    # vor dem Ende sauber abbauen
    qapp.processEvents()
    case.close()


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
    import json as _json

    manifest = _json.loads(res.manifest.read_text(encoding="utf-8"))
    by_name = {f["file"]: f for f in manifest["files"]}
    assert {"report_A-01.pdf", "report_A-02.txt", names[0], names[1]} <= set(by_name)
    assert by_name["report_A-02.txt"]["account"] == testmailbox.UMLAUT_USER
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


def test_encrypted_copy(acquired, tmp_path):
    import pyzipper

    from fed.core.encrypt import check_password, generate_password

    pw = generate_password()
    assert not check_password(pw) and len(pw) == 29 and check_password("kurz")
    case = acquired
    with pytest.raises(ValueError, match="ungeeignet"):
        finish_case(case, Config(), encrypt_password="zu-kurz")
    assert not case.closed
    res = finish_case(case, Config(), language="de", encrypt_password=pw)
    enc = res.encrypted
    assert enc["verified"] and enc["method"].startswith("AES-256")
    path = case.root / enc["file"]
    assert path.name.endswith("_encrypted.zip") and path.with_name(path.name + ".sha256").exists()
    with pyzipper.AESZipFile(path) as zf:
        assert all(i.flag_bits & 0x1 for i in zf.infolist())  # verschlüsselt
        with pytest.raises(RuntimeError):
            zf.read(res.zip.path.name)  # ohne Passwort nicht lesbar
        zf.setpassword(pw.encode())
        assert hashlib.sha256(zf.read(res.zip.path.name)).hexdigest() == res.zip.hashes["sha256"]
        assert {"report.pdf", "report.txt", "log.csv"} <= set(zf.namelist())
    logged = case.conn.execute("SELECT details FROM log WHERE event = 'encrypted_copy_created'").fetchone()[0]
    assert enc["sha256"] in logged and pw not in logged
    all_log = " ".join(r[0] for r in case.conn.execute("SELECT details FROM log"))
    assert pw not in all_log  # Passwort nirgends im Log
    assert "Verschlüsselte Weitergabe-Kopie" in res.report_txt.read_text(encoding="utf-8")
    assert path.name in {r[0] for r in case.conn.execute("SELECT file_name FROM output")}


def test_finish_with_slash_and_spaces_in_case_number(tmp_path, imap_server, test_mails):
    """Casenumber „VG-122234/23, EV-1“: Abschluss schrieb das ZIP in einen nicht vorhandenen Unterordner."""
    from fed.core.acquisition import AccountSpec, register_account, run_attempt, scan
    from fed.core.case import Case, CaseInfo
    from fed.core.config import Config
    from fed.core.finalize import finish_case
    from fed.core.imap_reader import ReadOnlyImap

    from validation import testmailbox

    def connector(user, password):
        def connect():
            c = ReadOnlyImap(imap_server.host, imap_server.port, cafile=str(imap_server.cafile))
            c.connect()
            c.login_password(user, password)
            return c
        return connect

    case = Case.create(tmp_path, CaseInfo("VG-122234/23, EV-1", "Testamt Ort"))
    for user, pw, ev in ((imap_server.user, imap_server.password, "DF 001"),
                         (testmailbox.UMLAUT_USER, testmailbox.UMLAUT_PASSWORD, "DF 002")):
        connect = connector(user, pw)
        imap = connect()
        acc = register_account(case, AccountSpec(user, ev, imap_server.host, imap_server.port), imap)
        scan(case, acc, imap)
        imap.logout()
        assert run_attempt(case, acc, connect).status == "done"
    case.set_zip_mode("je_konto")
    cfg = Config()
    cfg.programm.ntp = False
    res = finish_case(case, cfg, encrypt_password="Kennwort-fuer-Test-123")
    assert case.root.parent == tmp_path and case.root.name == "VG-122234_23,_EV-1"
    names = sorted(p.name for p in case.root.iterdir() if p.is_file())
    assert all(z.path.parent == case.root and z.verified for z in res.zips)
    assert res.zips[0].path.name.startswith("VG-122234_23,_EV-1_DF_001_")
    assert res.zips[1].path.name.startswith("VG-122234_23,_EV-1_DF_002_")
    assert "VG-122234_23,_EV-1_encrypted.zip" in names and {"log_DF_001.csv", "log_DF_002.csv"} <= set(names)
    assert {"report_DF_001.pdf", "report_DF_002.pdf"} <= set(names)
    assert not any("/" in n or " " in n for n in names)
    case.close()


def test_zip_dedups_duplicate_folder_entries(acquired):
    """Gleichnamige Ordner (z. B. aus einem Apple-Mail-Import) dürfen keine „Duplicate name“-Warnung
    und keine doppelten Verzeichniseinträge im ZIP erzeugen."""
    import warnings

    case = acquired
    acc_id, name, delim = case.conn.execute(
        "SELECT account_id, display_name, delimiter FROM folder WHERE selected = 1 LIMIT 1").fetchone()
    # zweite Ordner-Zeile mit gleichem Anzeigenamen/Delimiter, aber anderem raw_name -> gleicher Archiv-Pfad
    with case.conn:
        case.conn.execute(
            "INSERT INTO folder (account_id, raw_name, display_name, delimiter, namespace, count_server, selected) "
            "VALUES (?,?,?,?,?,?,1)", (acc_id, (name or "X") + "#dup", name, delim, "personal", 0))

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        res = finish_case(case, Config())
    assert not any("Duplicate name" in str(w.message) for w in caught)
    assert res.zip.verified

    with zipfile.ZipFile(res.zip.path) as zf:
        dirs = [n for n in zf.namelist() if n.endswith("/")]
    assert len(dirs) == len(set(dirs))  # keine doppelten Verzeichniseinträge


def test_report_caps_many_dkim_issues(acquired):
    """Viele DKIM-Auffälligkeiten werden im Bericht nicht einzeln gelistet (Volldaten in log.csv)."""
    from fed.core.report import labels, render_txt

    case = acquired
    with case.conn:
        case.conn.execute("UPDATE message SET dkim_result = 'fail', dkim_domain = 'x.de' WHERE status = 'valid'")
    n = case.conn.execute("SELECT COUNT(*) FROM message WHERE status = 'valid'").fetchone()[0]
    assert n >= 1
    full = render_txt(case, dkim_list="immer")          # eigene DKIM-Liste: immer -> alle Auffälligkeiten gelistet
    capped = render_txt(case, dkim_list="auto", dkim_list_max=0)  # DKIM-Liste auto + Grenze 0 -> weggelassen
    assert len(capped) < len(full)
    assert labels("de")["dkim_issues_omitted"].format(n=n) in capped
    assert "log.csv" in capped


def test_describe_event_readable_and_fallback():
    """Log-Ereignisse werden lesbar; unbekannte sauber aufbereitet (kein roher JSON)."""
    from fed.core.report import describe_event, labels

    de = labels("de")
    assert "INBOX" in describe_event("no_date_header", '{"folder": "INBOX", "uid": 7}', de)
    assert "UID 7" in describe_event("no_date_header", '{"folder": "INBOX", "uid": 7}', de)
    # String-Details (kein dict)
    assert "nicht möglich" in describe_event("resume_not_possible", "UIDVALIDITY", de)
    # unbekanntes Ereignis -> entgrätet, kein "{"
    out = describe_event("keep_awake_on", '{"for": "acquisition"}', de)
    assert "{" not in out and "keep awake on" in out
    # Englisch
    assert "Date header" in describe_event("no_date_header", '{"folder": "X", "uid": 1}', labels("en"))


def test_report_shows_delivery_verdict_for_permerror(acquired):
    """Bei permerror (Schlüssel heute weg) zeigt der Bericht das Zustell-Urteil des Empfangsservers."""
    import json as _json

    from fed.core.report import render_txt

    case = acquired
    mid = case.conn.execute("SELECT id FROM message WHERE status = 'valid' LIMIT 1").fetchone()[0]
    with case.conn:
        case.conn.execute(
            "UPDATE message SET dkim_result = 'permerror', dkim_domain = 'alt.example', "
            "dkim_detail = 'key_not_in_dns', auth_results = ? WHERE id = ?",
            (_json.dumps({"authserv": "mx.provider.de", "dkim": "pass", "spf": "pass"}), mid))
    txt = render_txt(case, mail_list="immer")
    assert "nicht prüfbar" in txt                  # permerror-Label (nicht „ungültig")
    assert "bei Zustellung: dkim=pass" in txt       # zeitgenössisches Urteil daneben


def test_build_zip_reports_prepare_phases(acquired):
    """build_zip meldet die Vorbereitungsschritte (collect/csv/scan) vor dem Packen, damit nichts leer wirkt."""
    from fed.core.finalize import build_zip

    phases = []
    build_zip(acquired, Config(), progress=lambda p: phases.append(p.phase))
    for ph in ("collect", "csv", "scan", "zip"):
        assert ph in phases, f"Phase {ph} fehlt: {phases[:6]}"
    # Vorbereitung kommt vor dem ersten „zip“-Fortschritt
    assert phases.index("collect") < phases.index("zip")


def test_write_reports_emits_txt_and_pdf_phases(acquired):
    """write_reports meldet reports_txt vor reports_pdf (PDF ist der langsame Teil)."""
    from fed.core.report import write_reports

    phases = []
    write_reports(acquired, progress=lambda p: phases.append(p.phase))
    assert "reports_txt" in phases and "reports_pdf" in phases
    assert phases.index("reports_txt") < phases.index("reports_pdf")


def test_dns_keys_list_csv_or_table_and_exported(acquired, tmp_path):
    """DNS-Abfragen: Vorgabe csv -> Bericht verweist auf dns_keys.csv; tabelle -> volle Liste im Bericht.
    Vollständige Liste geht in dns_keys.csv."""
    from fed.core.finalize import write_dns_keys_csv
    from fed.core.report import labels, render_txt

    case = acquired
    with case.conn:
        for i in range(5):
            case.conn.execute(
                "INSERT INTO dns_record (name, record, sha256, status, fetched_utc) VALUES (?,?,?,?,?)",
                (f"sel{i}._domainkey.d{i}.example", "v=DKIM1; p=AAA", "a" * 64, "ok", "2026-11-19T10:00:00Z"))
    n = case.conn.execute("SELECT COUNT(*) FROM dns_record").fetchone()[0]
    assert n >= 5

    csv_ref = render_txt(case, dns_mode="csv")                     # Vorgabe: nur Verweis auf die CSV
    assert labels("de")["dns_keys_csv"].format(n=n) in csv_ref
    assert "dns_keys.csv" in csv_ref
    table = render_txt(case, dns_mode="tabelle")                   # tabelle: Schlüssel im Bericht gelistet
    assert "sel0._domainkey.d0.example" in table

    out = tmp_path / "dns_keys.csv"
    assert write_dns_keys_csv(case, out) == n                      # Export enthält alle Schlüssel
    assert out.read_text(encoding="utf-8").count("_domainkey") >= 5


def test_manifest_anchors_outputs_and_log(acquired):
    """manifest.json: alle Ausgabedateien mit Hash und der Stand der Log-Kette; selbst gehasht und
    protokolliert, sodass die Kette danach an das Manifest anschließt."""
    import json as _json

    from fed.core.hashing import hash_file

    case = acquired
    res = finish_case(case, Config())
    m = res.manifest
    assert m.name == "manifest.json" and res.manifest_timestamp == (None, "")  # kein TSA eingerichtet
    data = _json.loads(m.read_text(encoding="utf-8"))
    assert data["format"] == "FED-Manifest/1" and data["case_number"] == case.info()["case_number"]
    files = {f["file"]: f for f in data["files"]}
    assert {res.zip.path.name, "report.pdf", "report.txt"} <= set(files)
    for name, f in files.items():
        assert hash_file(case.root / name)["sha256"] == f["sha256"], name
    sha = hash_file(m)["sha256"]
    assert res.manifest_hashes["sha256"] == sha
    assert m.with_name("manifest.json.sha256").read_text().split()[0] == sha
    # Log-Stand im Manifest = Eintrag direkt vor „manifest_created“
    lg = data["log"]
    assert lg["chain_ok"] and lg["entries"] > 0
    row = case.conn.execute("SELECT entry_hash FROM log WHERE id = ?", (lg["last_id"],)).fetchone()
    assert row[0] == lg["last_entry_hash"]
    nxt = case.conn.execute("SELECT event, details FROM log WHERE id = ?", (lg["last_id"] + 1,)).fetchone()
    assert nxt[0] == "manifest_created" and sha in nxt[1]
    assert sha in {r[0] for r in case.conn.execute("SELECT sha256 FROM output WHERE file_name = 'manifest.json'")}
    assert "manifest.json" in res.report_txt.read_text(encoding="utf-8")  # Prüfanker im Bericht genannt
