import hashlib
import json
import os
from datetime import UTC, datetime

import pytest

from fed.core.acquisition import (
    AccountSpec,
    Mode,
    register_account,
    run_attempt,
    scan,
    set_selection,
)
from fed.core.cancel import CancelToken
from fed.core.case import Case, CaseInfo
from fed.core.config import Config
from fed.core.imap_reader import ReadOnlyImap


@pytest.fixture
def connect(imap_server):
    def _connect():
        c = ReadOnlyImap(imap_server.host, imap_server.port, cafile=str(imap_server.cafile))
        c.connect()
        c.login_password(imap_server.user, imap_server.password)
        return c

    return _connect


@pytest.fixture
def cfg():
    c = Config()
    c.programm.chunk_mb = 1  # große Testmail wird stückweise geladen
    return c


def _setup(tmp_path, connect, imap_server, **spec_kw):
    case = Case.create(tmp_path, CaseInfo("T-1", "Tester"))
    imap = connect()
    spec = AccountSpec(imap_server.user, "A-01", imap_server.host, imap_server.port, **spec_kw)
    acc = register_account(case, spec, imap)
    scan(case, acc, imap)
    imap.logout()
    return case, acc


def _expected(test_mails):
    return {hashlib.sha256(m.raw).hexdigest(): m for m in test_mails}


def test_full_acquisition(tmp_path, connect, imap_server, test_mails, cfg):
    case, acc = _setup(tmp_path, connect, imap_server)
    res = run_attempt(case, acc, connect, cfg=cfg)
    assert res.status == "done" and res.saved == len(test_mails) and not res.warnings

    exp = _expected(test_mails)
    rows = case.conn.execute("SELECT zip_path, sha256, md5, internaldate, recipient_note, attachment_count, "
                             "inline_count FROM message").fetchall()
    assert {r[1] for r in rows} == set(exp)
    for zip_path, sha, md5, idate, *_ in rows:
        p = case.work_dir / zip_path
        assert datetime.fromisoformat(idate) == exp[sha].received
        assert hashlib.sha256(p.read_bytes()).hexdigest() == sha
        assert hashlib.md5(p.read_bytes()).hexdigest() == md5
        assert int(os.stat(p).st_mtime) == int(exp[sha].received.timestamp())  # Zeitstempel = Eingangszeit
        assert p.name.startswith(exp[sha].received.strftime("%Y%m%dT%H%M%SZ_UID"))
    notes = {r[4] for r in rows}
    assert {"nur BCC", "undisclosed-recipients"} <= notes
    assert max(r[5] for r in rows) == 1 and max(r[6] for r in rows) == 1
    assert (case.work_dir / "test@example.de" / "Entwürfe").is_dir()

    # Vorher/Nachher identisch -> nichts verändert
    for before, after in case.conn.execute("SELECT status_before, status_after FROM folder WHERE selected = 1"):
        assert json.loads(before) == json.loads(after)
    assert case.verify_log() == (True, None)
    case.close()


def test_cancel_and_resume(tmp_path, connect, imap_server, test_mails, cfg):
    case, acc = _setup(tmp_path, connect, imap_server)
    token = CancelToken()

    def stop_after_three(p):
        if p.mails_done == 3:
            token.cancel()

    r1 = run_attempt(case, acc, connect, cancel=token, progress=stop_after_three, cfg=cfg)
    # parallele Verbindungen können ihre gerade geladene Mail noch fertig speichern
    assert r1.status == "cancelled" and 3 <= r1.saved < len(test_mails)
    assert not list(case.work_dir.rglob("*.part"))

    r2 = run_attempt(case, acc, connect, cfg=cfg)
    assert r2.status == "done" and r2.nr == 2
    assert r2.saved == len(test_mails) - r1.saved and r2.skipped_existing == r1.saved
    total = case.conn.execute("SELECT COUNT(*) FROM message WHERE status = 'valid'").fetchone()[0]
    assert total == len(test_mails)
    case.close()


def test_restart_moves_previous(tmp_path, connect, imap_server, test_mails, cfg):
    case, acc = _setup(tmp_path, connect, imap_server)
    run_attempt(case, acc, connect, cfg=cfg)
    with pytest.raises(ValueError):
        run_attempt(case, acc, connect, mode=Mode.RESTART, cfg=cfg)
    r = run_attempt(case, acc, connect, mode=Mode.RESTART, reason="Test Neubeginn", cfg=cfg)
    assert r.status == "done" and r.saved == len(test_mails)
    moved = case.root / "discarded" / "attempt_1" / "test@example.de"
    assert moved.is_dir() and len(list(moved.rglob("*.eml"))) == len(test_mails)
    counts = dict(case.conn.execute("SELECT status, COUNT(*) FROM message GROUP BY status").fetchall())
    assert counts == {"valid": len(test_mails), "discarded": len(test_mails)}
    case.close()


def test_restart_delete_old(tmp_path, connect, imap_server, cfg):
    case, acc = _setup(tmp_path, connect, imap_server)
    run_attempt(case, acc, connect, cfg=cfg)
    run_attempt(case, acc, connect, mode=Mode.RESTART, reason="löschen", delete_old=True, cfg=cfg)
    assert not (case.root / "discarded" / "attempt_1").exists()
    assert case.conn.execute("SELECT deleted FROM attempt WHERE nr = 2").fetchone()[0] == 1
    case.close()


def test_tampered_file_is_reloaded(tmp_path, connect, imap_server, test_mails, cfg):
    case, acc = _setup(tmp_path, connect, imap_server)
    run_attempt(case, acc, connect, cfg=cfg)
    path = case.conn.execute("SELECT zip_path FROM message LIMIT 1").fetchone()[0]
    (case.work_dir / path).write_bytes(b"manipuliert")
    r = run_attempt(case, acc, connect, cfg=cfg)
    assert r.saved == 1 and r.status == "done"
    errors = case.conn.execute("SELECT COUNT(*) FROM log WHERE event = 'existing_file_invalid'").fetchone()[0]
    assert errors == 1
    case.close()


def test_selection_and_date_filter(tmp_path, connect, imap_server, cfg):
    case, acc = _setup(tmp_path, connect, imap_server,
                       date_from=datetime(2026, 11, 3, tzinfo=UTC), date_to=datetime(2026, 11, 4, tzinfo=UTC))
    set_selection(case, acc, {"INBOX", "Gesendet"})
    r = run_attempt(case, acc, connect, cfg=cfg)
    assert r.saved == 2  # INBOX 03.11. und 04.11.; Gesendet (21.10.) liegt außerhalb
    case.close()


@pytest.mark.parametrize("connections", [1, 3, 6])
def test_parallel_connections(tmp_path, connect, imap_server, test_mails, connections):
    cfg = Config()
    cfg.programm.chunk_mb = 1
    cfg.programm.verbindungen = connections
    case, acc = _setup(tmp_path, connect, imap_server)
    r = run_attempt(case, acc, connect, cfg=cfg)
    assert r.status == "done" and r.saved == len(test_mails)
    hashes = {x[0] for x in case.conn.execute("SELECT sha256 FROM message")}
    assert hashes == set(_expected(test_mails))
    used = json.loads(case.conn.execute(
        "SELECT details FROM log WHERE event = 'download_connections'").fetchone()[0])["used"]
    assert used == min(connections, 10)
    assert case.verify_log() == (True, None)  # Log-Kette auch bei parallelen Schreibern intakt
    case.close()


def test_server_refuses_extra_connections(tmp_path, connect, imap_server, test_mails):
    cfg = Config()
    cfg.programm.verbindungen = 4
    case, acc = _setup(tmp_path, connect, imap_server)
    import threading

    lock, active = threading.Lock(), {"n": 0}

    def limited():  # Server erlaubt höchstens 2 gleichzeitige Verbindungen
        with lock:
            if active["n"] >= 2:
                raise OSError("Too many simultaneous connections")
            active["n"] += 1
        c = connect()
        orig = c.logout

        def logout():
            if c._imap is not None:
                with lock:
                    active["n"] -= 1
            orig()

        c.logout = logout
        return c

    r = run_attempt(case, acc, limited, cfg=cfg)
    assert r.status == "done" and r.saved == len(test_mails)
    refused = case.conn.execute("SELECT COUNT(*) FROM log WHERE event = 'connection_refused'").fetchone()[0]
    assert refused >= 1
    case.close()


def test_accounts_overview_and_next_evidence(tmp_path, connect, imap_server, test_mails, cfg):
    from fed.core.acquisition import accounts_overview
    from fed.core.naming import next_evidence

    case, acc = _setup(tmp_path, connect, imap_server)
    ov = accounts_overview(case)
    assert [(o["email"], o["evidence"], o["state"], o["attempts"]) for o in ov] == [
        (imap_server.user, "A-01", "new", 0)]
    assert ov[0]["expected"] == len(test_mails) and ov[0]["saved"] == 0
    token = CancelToken()
    token.cancel()
    run_attempt(case, acc, connect, cancel=token, cfg=cfg)
    assert accounts_overview(case)[0]["state"] == "cancelled"
    run_attempt(case, acc, connect, cfg=cfg)
    ov = accounts_overview(case)[0]
    assert (ov["state"], ov["saved"], ov["attempts"]) == ("done", len(test_mails), 2)
    assert ov["bytes"] > 4_000_000 and ov["last_utc"]
    assert next_evidence([o["evidence"] for o in accounts_overview(case)]) == "A-02"
    case.close()


@pytest.mark.parametrize(("existing", "expected"), [
    (["A-01"], "A-02"), (["VG-2026-009"], "VG-2026-010"), (["Asservat 3a"], "Asservat 4a"), (["A-99"], "A-100"),
    (["A-01", "A-03", "A-02"], "A-04"), (["ohne Ziffer"], ""), ([], ""), (["A-01", "a-02", "A-01"], "A-03"),
])
def test_next_evidence(existing, expected):
    from fed.core.naming import next_evidence

    assert next_evidence(existing) == expected


def test_unexpected_error_marks_attempt_failed(tmp_path, connect, imap_server, cfg, monkeypatch):
    from fed.core import acquisition

    case, acc = _setup(tmp_path, connect, imap_server)

    def boom(*a, **k):
        raise RuntimeError("Datenträger voll")

    monkeypatch.setattr(acquisition, "_store_message", boom)
    with pytest.raises(RuntimeError, match="voll"):
        run_attempt(case, acc, connect, cfg=cfg)
    status = case.conn.execute("SELECT status FROM attempt WHERE account_id = ?", (acc,)).fetchone()[0]
    assert status == "failed"
    assert case.conn.execute("SELECT COUNT(*) FROM log WHERE event = 'attempt_failed'").fetchone()[0] == 1
    case.close()
