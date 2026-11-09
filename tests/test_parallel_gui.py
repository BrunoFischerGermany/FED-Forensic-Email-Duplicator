"""Mehrere Konten: Sicherung läuft im Hintergrund, während das nächste Konto vorbereitet wird."""

import os
import threading
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")


def _pump(app, until, limit=60):
    t = time.time()
    while time.time() - t < limit:
        app.processEvents()
        time.sleep(0.02)
        if until():
            return
    raise TimeoutError


def test_background_acquisition_and_second_account(imap_server, tmp_path, monkeypatch, test_mails):
    from validation import testmailbox

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    from fed.core import acquisition as acq
    from fed.core import auth
    from fed.core import config as cfgmod
    from fed.gui import assistant as ga
    from fed.gui.main_window import MainWindow

    orig = auth.make_connector
    monkeypatch.setattr(ga, "make_connector", lambda *a, **k: orig(*a, cafile=str(imap_server.cafile), **k))
    yes = staticmethod(lambda *a, **k: QtWidgets.QMessageBox.StandardButton.Yes)
    infos = []
    monkeypatch.setattr(QtWidgets.QMessageBox, "warning", yes)
    monkeypatch.setattr(QtWidgets.QMessageBox, "question", yes)
    monkeypatch.setattr(QtWidgets.QMessageBox, "information", staticmethod(lambda *a, **k: infos.append(a[2])))
    monkeypatch.setattr(ga, "ask_zip_mode", lambda parent, case, cfg, force=False: case.set_zip_mode("je_konto")
                        or "je_konto")

    # Sicherungen anhalten, bis der Test sie freigibt (sonst sind 14 Testmails zu schnell fertig)
    gates: dict[int, threading.Event] = {}
    real = acq.run_attempt

    def gated(case, account_id, connect, mode=acq.Mode.RESUME, cancel=None, progress=None, *a, **k):
        gate = gates.setdefault(account_id, threading.Event())
        while not gate.wait(0.05):
            if cancel is not None and cancel.is_cancelled:
                break
        return real(case, account_id, connect, mode, cancel, progress, *a, **k)

    monkeypatch.setattr(acq, "run_attempt", gated)

    from fed.gui import encrypt_dialog

    monkeypatch.setattr(encrypt_dialog.EncryptDialog, "exec", lambda dlg: 0 if dlg.reject() else 1)
    cfg = cfgmod.Config()
    cfg.examiner_name = "Max Mustermann"
    cfg.programm.zielpfad = str(tmp_path)
    cfg.programm.ntp = False
    cfg.programm.verschluesselung = "aus"
    cfg.source_path = tmp_path / "config.toml"
    w = MainWindow(cfg, tmp_path)
    w.show()
    a = w.assistant
    _pump(app, lambda: a.st.system is not None)
    a.p_case._create()

    def account(user, password, evidence):
        p = a.p_acc
        p.email.setText(user)
        p.evidence.setText(evidence)
        p.server.setText(imap_server.host)
        p.port.setValue(imap_server.port)
        p.password.setText(password)
        p._test()
        _pump(app, p.go.isEnabled)
        p._go()
        _pump(app, a.p_fold.go.isEnabled)
        a.p_fold._go()
        app.processEvents()

    # Konto 1 starten – es läuft (angehalten) im Hintergrund
    account(imap_server.user, imap_server.password, "A-01")
    job1 = a.p_acq.job
    assert job1.running and a.p_acq.cancel_btn.isVisible() and a.p_acq.another_btn.isVisible()
    assert not a.p_acq.finish_btn.isVisible()
    _pump(app, lambda: job1.account_id in a.rail_accounts.by_id)
    assert a.rail_accounts.by_id[job1.account_id].bar.isVisibleTo(a.rail_accounts)

    # während Konto 1 läuft: weiteres Konto (Umlaut-Adresse) vorbereiten und starten
    a.p_acq.another_btn.click()
    assert a.stack.currentWidget() is a.p_acc and job1.running
    account(testmailbox.UMLAUT_USER, testmailbox.UMLAUT_PASSWORD, "A-02")
    job2 = a.p_acq.job
    assert job2 is not job1 and job2.running and job1.running and len(a.jobs.running()) == 2
    assert job2.connect is not job1.connect

    # Klick links auf das laufende Konto 1 zeigt dessen Live-Ansicht
    a._rail_pick({"id": job1.account_id, "email": job1.email})
    assert a.p_acq.job is job1 and a.stack.currentWidget() is a.p_acq
    # dasselbe Konto ein zweites Mal starten geht nicht – es wird der Fortschritt gezeigt
    a.st.account_id = job1.account_id
    a._after_account()
    assert a.p_acq.job is job1 and infos

    # Abschluss ist gesperrt, solange Sicherungen laufen
    infos.clear()
    a._finish()
    assert infos and "2" in infos[0] and a.stack.currentWidget() is a.p_acq

    # Konto 2 zuerst fertig: Konto 1 läuft weiter, Abschluss-Knopf noch gesperrt
    gates[job2.account_id].set()
    _pump(app, lambda: job2.result is not None)
    assert job2.result.status == "done" and job1.running
    a._rail_pick({"id": job2.account_id, "email": job2.email})  # fertiges Konto: normale Kontoseite
    assert a.stack.currentWidget() is a.p_acc
    a.show_job(job2)
    assert a.p_acq.finish_btn.isVisible() and not a.p_acq.finish_btn.isEnabled()

    # Konto 1 freigeben: beide fertig, Abschluss mit je einem ZIP
    gates[job1.account_id].set()
    _pump(app, lambda: job1.result is not None)
    a._rail_pick({"id": job1.account_id, "email": job1.email})
    a.show_job(job1)
    assert job1.result.status == "done" and a.p_acq.finish_btn.isEnabled() and not a.jobs.running()
    rail = a.rail_accounts
    rail.refresh(a.st.case)
    assert not any(it.bar.isVisibleTo(rail) for it in rail.by_id.values())
    a.p_acq.finish.emit()
    _pump(app, a.p_fin.open_pdf.isEnabled, 60)
    res = a.st.finish
    assert len(res.zips) == 2 and all(z.verified for z in res.zips)
    assert sum(z.files for z in res.zips) == len(test_mails) * 2
    w.close()
    w.deleteLater()
    app.processEvents()


def test_close_window_cancels_running_jobs(imap_server, tmp_path, monkeypatch):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    from fed.core import acquisition as acq
    from fed.core import auth
    from fed.core import config as cfgmod
    from fed.gui import assistant as ga
    from fed.gui.main_window import MainWindow

    orig = auth.make_connector
    monkeypatch.setattr(ga, "make_connector", lambda *a, **k: orig(*a, cafile=str(imap_server.cafile), **k))
    asked = []
    monkeypatch.setattr(QtWidgets.QMessageBox, "warning",
                        staticmethod(lambda *a, **k: QtWidgets.QMessageBox.StandardButton.Yes))
    monkeypatch.setattr(QtWidgets.QMessageBox, "question",
                        staticmethod(lambda *a, **k: asked.append(a[2]) or QtWidgets.QMessageBox.StandardButton.Yes))
    real = acq.run_attempt

    def hang(case, account_id, connect, mode=acq.Mode.RESUME, cancel=None, progress=None, *a, **k):
        while not cancel.is_cancelled:
            time.sleep(0.02)
        return real(case, account_id, connect, mode, cancel, progress, *a, **k)

    monkeypatch.setattr(acq, "run_attempt", hang)
    cfg = cfgmod.Config()
    cfg.examiner_name = "Max Mustermann"
    cfg.programm.zielpfad = str(tmp_path)
    cfg.programm.ntp = False
    cfg.source_path = tmp_path / "config.toml"
    w = MainWindow(cfg, tmp_path)
    w.show()
    a = w.assistant
    _pump(app, lambda: a.st.system is not None)
    a.p_case._create()
    p = a.p_acc
    p.email.setText(imap_server.user)
    p.evidence.setText("A-01")
    p.server.setText(imap_server.host)
    p.port.setValue(imap_server.port)
    p.password.setText(imap_server.password)
    p._test()
    _pump(app, p.go.isEnabled)
    p._go()
    _pump(app, a.p_fold.go.isEnabled)
    a.p_fold._go()
    job = a.p_acq.job
    assert job.running
    case = a.st.case
    root = case.root
    w.close()
    assert asked and "1" in asked[0] and not job.worker.thread.isRunning()
    app.processEvents()
    from fed.core.case import Case

    c2 = Case.open(root)
    assert c2.conn.execute("SELECT status FROM attempt").fetchone()[0] == "cancelled"
    c2.close()
    w.deleteLater()
    app.processEvents()


def test_empty_password_is_marked_not_tested():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    from fed.core.config import Config
    from fed.gui.assistant import AccountPage, State

    p = AccountPage(State(Config()))
    p.email.setText("info@example.de")
    p.evidence.setText("DF001")
    p.server.setText("imap.example.de")
    p._test()
    assert p.test_btn.isEnabled()  # kein Test gestartet
    assert p.password.property("invalid") and not p.server.property("invalid")
    assert "Passwort" in p.error.text()
    p.password.setText("x")  # Tippen hebt die Markierung auf
    assert not p.password.property("invalid")
    p.server.setText("")
    p.password.setText("")
    p._test()
    assert p.server.property("invalid") and p.password.property("invalid")
    p.method.set_value("google")  # OAuth: kein Passwort nötig
    p._method_changed("google")
    assert p.password not in p._missing()
    p.deleteLater()
    app.processEvents()


def test_case_folder_name_replaces_slash(tmp_path):
    from fed.core.case import Case, CaseInfo
    from fed.core.naming import sanitize_component

    from fed.core.naming import file_name

    assert sanitize_component("VG-1234/26, EV-1") == "VG-1234_26, EV-1"
    assert sanitize_component(" a\\b:c ") == "a_b_c"
    assert file_name("VG-1234/26, EV-1") == "VG-1234_26,_EV-1"
    assert file_name("VG-1234/26,  EV-1", spaces_to_underscore=False) == "VG-1234_26,  EV-1"
    # Standard: Leerzeichen -> _, gilt für alle Dateien des Case
    case = Case.create(tmp_path, CaseInfo("VG-1234/26, EV-1", "ZAI+Z Fischer"))
    assert case.root == tmp_path / "VG-1234_26,_EV-1" and case.root.parent == tmp_path
    assert case.info()["case_number"] == "VG-1234/26, EV-1"  # Original bleibt erhalten
    assert case.replace_spaces and case.file_name("EV 2") == "EV_2"
    case.close()
    case = Case.open(tmp_path / "VG-1234_26,_EV-1")
    assert case.replace_spaces  # Regel bleibt beim erneuten Öffnen erhalten
    case.close()
    # abgeschaltet: Leerzeichen bleiben
    keep = Case.create(tmp_path, CaseInfo("AZ 7/26", "X"), replace_spaces=False)
    assert keep.root.name == "AZ 7_26" and keep.file_name("EV 2") == "EV 2"
    keep.close()


def test_finish_reachable_after_restart_and_retry_after_failure(acquired):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    from fed.core.config import Config
    from fed.gui.assistant import AccountPage, FinishPage, State

    st = State(Config(), case=acquired)
    p = AccountPage(st)
    p.prepare()
    assert p.finish_btn.isVisibleTo(p)  # Case mit Konto: direkt abschließen möglich
    fired = []
    p.finish.connect(lambda: fired.append(1))
    p.finish_btn.click()
    assert fired
    f = FinishPage(st)
    assert not f.retry_btn.isVisibleTo(f)
    f._failed("[Errno 2] No such file or directory", "")
    assert f.retry_btn.isVisibleTo(f) and f.back_btn.isVisibleTo(f) and "noch offen" in f.sub.text()
    for w in (p, f):
        w.deleteLater()
    app.processEvents()


def test_account_note_editable_in_overview(acquired, monkeypatch):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    from fed.gui.accounts import NOTE_COL, AccountsTable, NoteDialog

    case = acquired
    t = AccountsTable()
    t.resize(900, 200)
    t.show()
    t.refresh(case)
    assert t.table.item(0, NOTE_COL).text() == "✎ Notiz hinzufügen"
    monkeypatch.setattr(NoteDialog, "exec", lambda dlg: dlg.edit.setPlainText("RUDI STILLER\nEinwilligung liegt vor") or 1)
    changed = []
    t.notes_changed.connect(lambda i, s: changed.append((i, s)))
    t._clicked(0, NOTE_COL)
    assert case.conn.execute("SELECT notes FROM account").fetchone()[0] == "RUDI STILLER\nEinwilligung liegt vor"
    assert changed and changed[0][1].endswith("liegt vor")
    item = t.table.item(0, NOTE_COL)
    assert item.text() == "RUDI STILLER\nEinwilligung liegt vor"  # Zeilenumbruch bleibt erhalten
    assert t.table.rowHeight(0) > t.table.verticalHeader().defaultSectionSize() * 0.9
    row = case.conn.execute("SELECT details FROM log WHERE event = 'account_notes_changed'").fetchone()
    assert row and "Einwilligung" in row[0]
    # Klick auf andere Spalten öffnet keinen Dialog; Doppelklick übernimmt weiterhin das Konto
    picked = []
    t.picked.connect(picked.append)
    t._double_clicked(0, 1)
    t._double_clicked(0, NOTE_COL)
    assert len(picked) == 1
    # abgeschlossener Case: nur lesen
    monkeypatch.setattr(type(case), "closed", property(lambda self: True))
    t.refresh(case)
    opened = []
    monkeypatch.setattr(NoteDialog, "exec", lambda dlg: opened.append(1) or 0)
    t._clicked(0, NOTE_COL)
    assert not opened and not t.table.item(0, NOTE_COL).toolTip()
    t.deleteLater()
    app.processEvents()
