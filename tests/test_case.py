import sqlite3

import pytest

from fed.core.case import Case, CaseInfo


def test_create_and_log_chain(tmp_path):
    case = Case.create(tmp_path, CaseInfo("2026-0001", "Max Mustermann", "Test"))
    assert (case.root / "case.sqlite").exists()
    assert case.work_dir.is_dir() and case.discarded_dir.is_dir()
    case.log("INFO", "test", {"a": 1})
    case.log("WARNING", "test2", "frei")
    assert case.verify_log() == (True, None)
    case.close()

    reopened = Case.open(tmp_path / "2026-0001")
    assert reopened.verify_log()[0]
    reopened.close()


def test_log_is_append_only(tmp_path):
    case = Case.create(tmp_path, CaseInfo("C1", "E"))
    with pytest.raises(sqlite3.DatabaseError):
        case.conn.execute("UPDATE log SET details = 'x'")
    with pytest.raises(sqlite3.DatabaseError):
        case.conn.execute("DELETE FROM log")
    case.close()


def test_tampering_detected(tmp_path):
    case = Case.create(tmp_path, CaseInfo("C2", "E"))
    case.log("INFO", "a")
    case.log("INFO", "b")
    case.conn.execute("DROP TRIGGER log_no_update")
    case.conn.execute("UPDATE log SET details = 'manipuliert' WHERE event = 'a'")
    ok, bad = case.verify_log()
    assert not ok and bad is not None
    case.close()


def test_required_fields(tmp_path):
    with pytest.raises(ValueError):
        Case.create(tmp_path, CaseInfo("", "E"))
    with pytest.raises(ValueError):
        Case.create(tmp_path, CaseInfo("C3", " "))


def test_old_case_gets_notes_column(tmp_path):
    """Case-Dateien älterer Versionen (ohne account.notes) werden beim Öffnen ergänzt."""
    case = Case.create(tmp_path, CaseInfo("ALT-1", "Tester"))
    root = case.root
    case.conn.execute("ALTER TABLE account DROP COLUMN notes")
    case.conn.commit()
    case.close()
    case = Case.open(root)
    cols = {r[1] for r in case.conn.execute("PRAGMA table_info(account)")}
    assert "notes" in cols
    assert case.conn.execute("SELECT COUNT(*) FROM log WHERE event = 'schema_migrated'").fetchone()[0] == 1
    assert case.verify_log()[0]
    case.close()


def test_device_owner_logged_and_in_report(tmp_path):
    from fed.core import report
    from fed.core.case import Case, CaseInfo

    case = Case.create(tmp_path, CaseInfo("AZ 9/26", "Tester"))
    assert case.device_owner_history() == []
    case.set_device_owner("subject", "PC-BETROFFEN")
    assert case.info()["device_owner"] == "subject"
    h = case.device_owner_history()
    assert h[0]["device_owner"] == "subject" and h[0]["hostname"] == "PC-BETROFFEN"
    assert case.verify_log()[0]
    txt = tmp_path / "r.txt"
    report.write_txt(case, txt, "de")
    assert "Gerät der betroffenen Person" in txt.read_text(encoding="utf-8")
    # Fortsetzung auf einem anderen Rechner: beide Angaben mit Rechner
    case.set_device_owner("own", "DIENST-PC")
    report.write_txt(case, txt, "de")
    t = txt.read_text(encoding="utf-8")
    assert "PC-BETROFFEN" in t and "DIENST-PC" in t and "eigenes Gerät" in t
    import pytest

    with pytest.raises(ValueError):
        case.set_device_owner("fremd")
    case.close()


def test_host_per_step_logged_and_differences_in_report(tmp_path, monkeypatch):
    """Vor Ort gesichert, im Amt auf anderem Rechner fortgesetzt/abgeschlossen: Rechner und Benutzer
    stehen je Arbeitsschritt im Log (Hash-Kette) und im Bericht – mit Hinweis auf den Unterschied."""
    from fed.core import case as casemod
    from fed.core import report

    onsite = {"user": "ermittler", "hostname": "LAPTOP-VOR-ORT", "os": "macOS 15", "tz": "CET"}
    office = {"user": "AMT\\pruefer", "hostname": "PC-AMT-07", "os": "Windows 11", "tz": "CET"}
    monkeypatch.setattr(casemod, "host_snapshot", lambda: dict(onsite))
    case = Case.create(tmp_path, CaseInfo("AZ 10/26", "Tester"))
    root = case.root
    case.close()
    again = Case.open(root)
    t = report.render_txt(again, "de")
    again.close()
    assert "LAPTOP-VOR-ORT" in t and "Hinweis" not in t.split("Bearbeitende Rechner")[1].split("\n\n")[0]
    monkeypatch.setattr(casemod, "host_snapshot", lambda: dict(office))
    case = Case.open(root)
    h = case.host_history()
    assert [x["step"] for x in h] == ["create", "open", "open"]
    assert h[0]["hostname"] == "LAPTOP-VOR-ORT" and h[-1]["hostname"] == "PC-AMT-07"
    assert h[-1]["user"] == "AMT\\pruefer" and case.verify_log()[0]
    t = report.render_txt(case, "de")
    assert "ermittler @ LAPTOP-VOR-ORT" in t and "AMT\\pruefer @ PC-AMT-07" in t
    assert "an 2 verschiedenen Rechnern" in t
    case.close()


def test_host_items_flags_finalize_on_other_computer():
    from fed.core.report import _host_items, labels

    L = labels("de")
    a = {"user": "u", "hostname": "VOR-ORT", "os": "macOS", "tz": "CET", "nr": 1, "email": "x@example.org"}
    b = {"user": "u2", "hostname": "AMT", "os": "Windows", "tz": "CET", "nr": None, "email": None}
    hist = [{**a, "step": "create", "ts_utc": "2026-11-24T08:00:00+00:00"},
            {**a, "step": "attempt", "ts_utc": "2026-11-24T08:05:00+00:00"},
            {**b, "step": "open", "ts_utc": "2026-11-24T14:00:00+00:00"},
            {**b, "step": "finalize", "ts_utc": "2026-11-24T14:01:00+00:00"}]
    checks = [{"user": "u2", "hostname": "AMT", "ntp_offset_s": 0.25, "ts_utc": "2026-11-24T14:00:00+00:00"}]
    items = dict(_host_items(hist, checks, L))
    assert "anderen Rechner als die Sicherung" in items["Hinweis"]
    assert "x@example.org #1" in items["u @ VOR-ORT"] and "Fallanlage" in items["u @ VOR-ORT"]
    assert "+0.250 s" in items["u2 @ AMT"] and "Abschluss" in items["u2 @ AMT"]
    assert len(_host_items(hist[:2], [], L)) == 1  # ein Rechner: kein Hinweis
