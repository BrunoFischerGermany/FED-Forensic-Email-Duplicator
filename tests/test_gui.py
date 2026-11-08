"""Durchlauf der Oberfläche (offscreen) gegen den Test-Dovecot: Case → Konto → Ordner → Sicherung → Abschluss."""

import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")


@pytest.fixture(scope="module")
def app():
    a = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    a.setStyle("Fusion")
    return a


def _pump(app, until, limit=30):
    t = time.time()
    while time.time() - t < limit:
        app.processEvents()
        time.sleep(0.02)
        if until():
            return
    raise TimeoutError


def test_full_gui_walkthrough(app, imap_server, tmp_path, monkeypatch, test_mails):
    from fed.core import auth
    from fed.core import config as cfgmod
    from fed.gui import assistant as ga
    from fed.gui.main_window import MainWindow

    orig = auth.make_connector
    monkeypatch.setattr(ga, "make_connector", lambda *a, **k: orig(*a, cafile=str(imap_server.cafile), **k))
    monkeypatch.setattr(QtWidgets.QMessageBox, "warning", staticmethod(lambda *a, **k: QtWidgets.QMessageBox.StandardButton.Yes))
    monkeypatch.setattr(QtWidgets.QMessageBox, "question", staticmethod(lambda *a, **k: QtWidgets.QMessageBox.StandardButton.Yes))
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
    assert a.st.case is not None

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
    assert a.p_fold.tree.topLevelItemCount() > 0
    a.p_fold._go()
    _pump(app, a.p_acq.finish_btn.isVisible, 60)
    a.p_acq.finish.emit()
    _pump(app, a.p_fin.open_pdf.isEnabled, 60)
    res = a.st.finish
    assert res.zip.verified and res.zip.files == len(test_mails)
    assert not a.st.case.work_dir.exists()  # Frage „Arbeitsordner löschen?“ mit Ja beantwortet
    w.nav.button(1).click()
    w.history.refresh()
    assert w.history.mails.model.rowCount() == len(test_mails)
    w.toggle_theme()
    w.close()


def test_setup_dialog_saves_config(app, tmp_path):
    from fed.core import config as cfgmod
    from fed.gui.setup_dialog import SetupDialog, needs_setup

    cfg = cfgmod.load_default(tmp_path)
    assert needs_setup(cfg)
    d = SetupDialog(cfg, tmp_path / cfgmod.CONFIG_FILENAME)
    assert not d.ok.isEnabled()
    d.name.setText("PP Musterstadt")
    d.examiner.setText("KOK Muster")
    d.lang.setCurrentIndex(d.lang.findData("en"))
    assert d.ok.isEnabled()
    d.save()
    loaded = cfgmod.load(tmp_path / cfgmod.CONFIG_FILENAME)
    assert (loaded.agentur.name, loaded.examiner_name, loaded.programm.sprache) == ("PP Musterstadt", "KOK Muster", "en")
    assert not needs_setup(loaded)
    d.deleteLater()
    app.processEvents()


def test_english_interface(app, tmp_path):
    from fed.core import config as cfgmod
    from fed.gui import i18n
    from fed.gui.main_window import MainWindow

    cfg = cfgmod.Config()
    cfg.programm.sprache = "en"
    cfg.programm.ntp = False
    cfg.programm.zielpfad = str(tmp_path)
    try:
        assert i18n.install("en") == "en"
        win = MainWindow(cfg, tmp_path)
        _pump(app, lambda: win.assistant.st.system is not None)
        texts = {b.text().strip() for b in win.findChildren(type(win.findChild(type(win.settings.save_btn))))}
        assert {"Acquisition", "History", "Settings"} <= texts
        assert win.settings.save_btn.text() == "Save"
        win.close()
        win.deleteLater()
        app.processEvents()
    finally:
        i18n.install("de")


def test_translations_complete():
    import json
    import subprocess
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    r = subprocess.run([sys.executable, str(root / "packaging" / "i18n.py"), "--check"], capture_output=True,
                       text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    assert json.loads((root / "src" / "fed" / "i18n" / "en.json").read_text(encoding="utf-8"))
    assert (root / "src" / "fed" / "i18n" / "fed_en.qm").stat().st_size > 1000


def test_long_texts_do_not_widen_window(app, tmp_path):
    """Lange Pfade, Casenumbers und Agenturnamen dürfen das Fenster nicht verbreitern.

    Verglichen wird die Mindestbreite vor und nach den langen Eingaben – absolute Pixel hängen
    von Schrift und Plattform ab (unter Windows/offscreen sind Texte deutlich breiter).
    """
    from PySide6.QtCore import Qt

    from fed.core import config as cfgmod
    from fed.gui.main_window import MainWindow

    cfg = cfgmod.Config()
    cfg.programm.ntp = False
    cfg.programm.zielpfad = str(tmp_path)
    w = MainWindow(cfg, tmp_path)
    w.setWindowState(Qt.WindowState.WindowNoState)
    w.show()
    _pump(app, lambda: w.assistant.st.system is not None)
    for _ in range(20):
        app.processEvents()
    before = w.minimumSizeHint().width()
    w.resize(before + 100, 720)
    for _ in range(5):
        app.processEvents()
    width = w.width()

    p = w.assistant.p_case
    p.target.setText(str(tmp_path / ("sehr-langer-ordnername-ohne-leerzeichen-" * 6)))
    p.case_no.setText("VG-" + "1234567890" * 8)
    w.agency.setText("Hauptzollamt Berlin – Zollfahndungsamt Berlin-Brandenburg, Sachgebiet IT-Forensik " * 2)
    for _ in range(20):
        app.processEvents()
    assert w.minimumSizeHint().width() <= before + 4
    assert w.width() == width
    assert p.target_hint.full_text().endswith("1234567890")
    w.close()
    w.deleteLater()
    app.processEvents()


def test_google_app_password_method(app, tmp_path):
    from fed.core import config as cfgmod
    from fed.gui.main_window import MainWindow

    cfg = cfgmod.Config()
    cfg.programm.ntp = False
    cfg.programm.zielpfad = str(tmp_path)
    win = MainWindow(cfg, tmp_path)
    _pump(app, lambda: win.assistant.st.system is not None)
    p = win.assistant.p_acc
    p.method.set_value("google_app")
    p._method_changed("google_app")
    assert p.pw_label.text() == "App-Passwort" and "apppasswords" in p.pw_hint.text()
    assert not p.password.isHidden() and p.oauth_hint.isHidden()
    p.method.set_value("google")
    p._method_changed("google")
    assert p.password.isHidden() and not p.oauth_hint.isHidden()
    win.close()
    win.deleteLater()
    app.processEvents()


def test_accounts_overview_and_evidence_suggestion(app, imap_server, tmp_path, monkeypatch):
    from fed.core import config as cfgmod
    from fed.core.acquisition import AccountSpec, register_account, run_attempt, scan
    from fed.core.case import Case, CaseInfo
    from fed.core.imap_reader import ReadOnlyImap
    from fed.gui.main_window import MainWindow
    from validation import testmailbox

    def connect(user=imap_server.user, pw=imap_server.password):
        c = ReadOnlyImap(imap_server.host, imap_server.port, cafile=str(imap_server.cafile))
        c.connect()
        c.login_password(user, pw)
        return c

    case = Case.create(tmp_path, CaseInfo("2026-0007", "Tester"))
    imap = connect()
    a1 = register_account(case, AccountSpec(imap_server.user, "VG-2026-009", imap_server.host, imap_server.port), imap)
    scan(case, a1, imap)
    imap.logout()
    run_attempt(case, a1, connect, cfg=cfgmod.Config())
    imap = connect(testmailbox.UMLAUT_USER, testmailbox.UMLAUT_PASSWORD)
    a2 = register_account(case, AccountSpec(testmailbox.UMLAUT_USER, "VG-2026-010", imap_server.host,
                                            imap_server.port), imap)
    scan(case, a2, imap)  # gescannt, aber noch nicht gesichert
    imap.logout()

    cfg = cfgmod.Config()
    cfg.programm.ntp = False
    cfg.programm.zielpfad = str(tmp_path)
    w = MainWindow(cfg, tmp_path)
    _pump(app, lambda: w.assistant.st.system is not None)
    a = w.assistant
    a.st.case = case
    a._after_case()
    p = a.p_acc
    assert p.accounts_card.isVisibleTo(w) and len(p.accounts.rows) == 2
    assert p.evidence.text() == "VG-2026-011"  # Vorschlag nach der letzten Nummer
    states = {o["email"]: o["state"] for o in p.accounts.rows}
    assert states == {imap_server.user: "done", testmailbox.UMLAUT_USER: "new"}
    assert len(a.rail_accounts.items) == 2 and a.rail_accounts.items[0].full_text().startswith("✓")

    # Doppelklick übernimmt ein Konto aus der Übersicht
    p.accounts.table.cellDoubleClicked.emit(1, 0)
    assert p.email.text() == testmailbox.UMLAUT_USER and p.server.text() == imap_server.host
    assert p.port.value() == imap_server.port
    # bekannte Adresse -> Hinweis und deren Evidence-Nummer
    p.email.setText(testmailbox.UMLAUT_USER)
    p._check_known()
    assert p.evidence.text() == "VG-2026-010" and "fortgesetzt" in p.evidence_hint.text()
    # vergebene Evidence-Nummer für eine andere Adresse wird abgelehnt
    p.email.setText("neu@example.de")
    p.evidence.setText("VG-2026-009")
    p.server.setText(imap_server.host)
    p._test()
    assert "schon für" in p.error.text()

    # Klick auf ein Konto in der linken Leiste übernimmt es (inkl. Notiz)
    from fed.core.acquisition import set_account_notes

    set_account_notes(case, a2, "Praxis-Postfach")
    a.rail_accounts._last = None
    a.refresh_accounts()
    a.rail_accounts.items[1].clicked.emit()
    assert p.email.text() == testmailbox.UMLAUT_USER and p.acc_notes.toPlainText() == "Praxis-Postfach"
    assert a.stack.currentWidget() is p

    # neues Konto: Anmeldeart zurück auf Passwort, Formular leer
    p.method.set_value("google")
    p._method_changed("google")
    a._another()
    assert p.method.value() == "password" and not p.password.isHidden()
    assert p.email.text() == "" and p.acc_notes.toPlainText() == "" and p.evidence.text() == "VG-2026-011"

    # abgelehnte Anmeldung: Hinweis auf IMAP-Freischaltung bzw. Google-App-Passwort
    p.server.setText("imap.gmx.net")
    p._test_failed("[AUTHENTICATIONFAILED] Authentication failed.", "")
    assert "IMAP" in p.test_state.text() and "freigeschaltet" in p.test_state.text()
    p.server.setText("imap.gmail.com")
    p._test_failed("[AUTHENTICATIONFAILED] Invalid credentials (Failure)", "")
    assert "App-Passwort" in p.test_state.text()
    p._test_failed("timed out", "")
    assert "freigeschaltet" not in p.test_state.text()

    # Abschlussseite hat „FED beenden“
    assert a.p_fin.quit_btn.text() == "FED beenden"

    asked = []
    monkeypatch.setattr(QtWidgets.QMessageBox, "question",
                        staticmethod(lambda *a, **k: asked.append(a[2]) or QtWidgets.QMessageBox.StandardButton.No))
    a._finish()
    assert asked and testmailbox.UMLAUT_USER in asked[0] and a.stack.currentWidget() is not a.p_fin
    w.close()
    w.deleteLater()
    app.processEvents()
    case.close()


def test_ask_zip_mode(app, tmp_path, monkeypatch):
    from fed.core import config as cfgmod
    from fed.core.case import Case, CaseInfo
    from fed.gui import assistant

    case = Case.create(tmp_path, CaseInfo("Z-1", "Tester"))
    cfg = cfgmod.Config()

    def click(index):
        def exec_(box):
            box.buttons()[index].click()
            return 0
        return exec_

    # Frage: zweiter Knopf = „Ein ZIP je Konto“
    monkeypatch.setattr(QtWidgets.QMessageBox, "exec", click(1))
    assert assistant.ask_zip_mode(None, case, cfg) == "je_konto" and case.zip_mode() == "je_konto"
    # „Später entscheiden“ lässt die Wahl unverändert
    monkeypatch.setattr(QtWidgets.QMessageBox, "exec", click(2))
    assert assistant.ask_zip_mode(None, case, cfg, force=True) == "je_konto"
    # feste Einstellung: keine Frage
    monkeypatch.setattr(QtWidgets.QMessageBox, "exec", lambda box: pytest.fail("darf nicht fragen"))
    cfg.programm.zip_aufteilung = "gesamt"
    assert assistant.ask_zip_mode(None, case, cfg) == "gesamt"
    src = case.conn.execute("SELECT details FROM log WHERE event = 'zip_mode_set' ORDER BY id DESC").fetchone()[0]
    assert "einstellung" in src
    assert "je Konto" in assistant.zip_mode_text("je_konto") and "offen" in assistant.zip_mode_text(None)
    case.close()
