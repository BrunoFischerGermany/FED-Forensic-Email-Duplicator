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
