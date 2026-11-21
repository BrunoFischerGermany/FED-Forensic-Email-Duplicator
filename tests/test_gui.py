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
    from fed.gui import encrypt_dialog

    def enc_exec(dlg):  # Abschluss: verschlüsselte Kopie mit erzeugtem Passwort (Checkbox ist standardmäßig aus)
        dlg.enable.setChecked(True)
        dlg._accept()
        return 1

    monkeypatch.setattr(encrypt_dialog.EncryptDialog, "exec", enc_exec)
    cfg = cfgmod.Config()
    cfg.examiner_name = "Max Mustermann"
    cfg.programm.zielpfad = str(tmp_path)
    cfg.programm.ntp = False
    cfg.programm.verschluesselung = "fragen"  # dieser Walkthrough prüft die verschlüsselte Kopie (Standard ist „aus“)
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
    _pump(app, lambda: p._ok)
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
    assert res.encrypted and res.encrypted["verified"]  # verschlüsselte Kopie mit erzeugtem Passwort
    shown = a.p_fin.check_kv.values["Passwort (nur jetzt sichtbar)"].text()
    assert len(shown) == 29 and a.p_fin._password is None
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


def test_gui_warns_on_password_for_microsoft(app, tmp_path):
    from fed.core import config as cfgmod
    from fed.gui.main_window import MainWindow

    cfg = cfgmod.Config()
    cfg.programm.ntp = False
    cfg.programm.zielpfad = str(tmp_path)
    win = MainWindow(cfg, tmp_path)
    _pump(app, lambda: win.assistant.st.system is not None)
    p = win.assistant.p_acc
    p.method.set_value("password")
    p._method_changed("password")
    p.server.setText("imap.gmx.net")
    assert "Arbeitsspeicher" in p.pw_hint.text() and p.pw_hint.objectName() == "Hint"
    p.server.setText("outlook.office365.com")
    assert "OAuth" in p.pw_hint.text() and p.pw_hint.objectName() == "Warn"
    assert "Weiterleitung und IMAP" in p._login_hint("AUTHENTICATE failed.")
    p.method.set_value("microsoft")
    p._method_changed("microsoft")
    assert "Administratorzustimmung" in p._login_hint("AADSTS65001: consent")
    # Anleitung: Link im Hinweis öffnet das Hilfe-Fenster am Microsoft-Abschnitt
    assert 'href="microsoft"' in p.oauth_hint.text()
    p.oauth_hint.linkActivated.emit("microsoft")
    dlg = win._fed_help
    assert dlg.isVisible() and "Microsoft" in dlg.browser.toPlainText()
    assert dlg.browser.verticalScrollBar().value() > 0
    ms_pos = dlg.browser.verticalScrollBar().value()
    win.settings.help_google.click()
    assert win._fed_help is dlg and dlg.browser.verticalScrollBar().value() != ms_pos
    dlg.close()
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


def test_settings_language_restart_hint_and_svg_logo(app, tmp_path, monkeypatch):
    from PySide6 import QtWidgets

    from fed.core import config as cfgmod
    from fed.gui.main_window import MainWindow
    from tests.test_logo import SVG

    # Beim Schließen mit ungespeicherten Einstellungen fragt FED nach – im Test „Nein“ (verwerfen).
    monkeypatch.setattr(QtWidgets.QMessageBox, "question",
                        staticmethod(lambda *a, **k: QtWidgets.QMessageBox.StandardButton.No))
    cfg = cfgmod.Config()
    cfg.programm.ntp = False
    cfg.programm.zielpfad = str(tmp_path)
    win = MainWindow(cfg, tmp_path)
    s = win.settings
    assert s.lang_hint.isHidden()
    s.lang.setCurrentIndex(s.lang.findData("en"))
    assert not s.lang_hint.isHidden() and "Neustart" in s.lang_hint.text() and "Restart" in s.lang_hint.text()
    s.lang.setCurrentIndex(s.lang.findData("de"))
    assert s.lang_hint.isHidden()

    (s.path.parent / "logo.svg").write_bytes(SVG)
    s._set_logo("logo.svg")
    assert s.logo_preview.pixmap() is not None and not s.logo_preview.pixmap().isNull()
    assert "PNG umgewandelt" in s.logo_hint.text()
    win.close()
    win.deleteLater()
    app.processEvents()



def test_case_page_continue_scan_without_test_and_add_account(app, tmp_path, monkeypatch, imap_server):
    """Fall schon offen: „Weiter“ statt neu anlegen; „Postfach scannen“ testet selbst;
    „+ Weiteres Konto“ in Leiste und Kontoseite; gleicher Fall erneut anlegen -> öffnen."""
    from PySide6 import QtWidgets

    from fed.core import auth
    from fed.core import config as cfgmod
    from fed.gui import assistant as ga
    from fed.gui.main_window import MainWindow

    orig = auth.make_connector
    monkeypatch.setattr(ga, "make_connector", lambda *a, **k: orig(*a, cafile=str(imap_server.cafile), **k))
    monkeypatch.setattr(QtWidgets.QMessageBox, "warning",
                        staticmethod(lambda *a, **k: QtWidgets.QMessageBox.StandardButton.Yes))
    asked = []
    monkeypatch.setattr(QtWidgets.QMessageBox, "question",
                        staticmethod(lambda *a, **k: asked.append(a[2]) or QtWidgets.QMessageBox.StandardButton.Yes))
    cfg = cfgmod.Config()
    cfg.examiner_name = "Max Mustermann"
    cfg.programm.zielpfad = str(tmp_path)
    cfg.programm.ntp = False
    cfg.source_path = tmp_path / "config.toml"
    w = MainWindow(cfg, tmp_path)
    w.show()
    a = w.assistant
    _pump(app, lambda: a.st.system is not None)
    a.p_case.case_no.setText("VG-1234/12, EV-1")
    a.p_case._go_clicked()
    case = a.st.case
    assert case is not None and a.stack.currentIndex() == 1

    # Konto ohne vorherigen Test: „Postfach scannen“ testet und geht weiter
    p = a.p_acc
    p.email.setText(imap_server.user)
    p.evidence.setText("DF001")
    p.server.setText(imap_server.host)
    p.port.setValue(imap_server.port)
    p.password.setText(imap_server.password)
    assert p.go.isEnabled() and not p._ok
    p._go_clicked()
    _pump(app, lambda: a.stack.currentIndex() == 2)
    _pump(app, a.p_fold.go.isEnabled)

    # zurück zu Schritt 1: Fall ist offen -> „Weiter“, Felder gesperrt, kein „Fall existiert bereits“
    a._show(0, 0)
    assert a.p_case.go.text() == "Weiter" and a.p_case.case_no.isReadOnly()
    assert not a.p_case.open_btn.isVisible()
    a.p_case._go_clicked()
    assert a.st.case is case and a.stack.currentIndex() == 1 and a.p_case.error.text() == ""

    # „+ Weiteres Konto“: Formular leer, Leiste hat den Knopf
    a.refresh_accounts()
    assert a.rail_accounts.add_btn.isVisibleTo(w)
    a.rail_accounts.add_btn.click()
    assert a.stack.currentIndex() == 1 and p.email.text() == ""

    # Programm neu gestartet: gleicher Fall noch einmal „anlegen“ -> Frage, dann öffnen statt Fehler
    case_dir = case.root
    a._new_case()
    a.p_case.case_no.setText("VG-1234/12, EV-1")
    a.p_case._go_clicked()
    assert any("schon einen Fall" in q for q in asked)
    assert a.st.case is not None and a.st.case.root == case_dir
    w.close()
    w.deleteLater()
    app.processEvents()


@pytest.mark.parametrize("button,expected", [("Yes", "own"), ("No", "subject"), ("Cancel", None),
                                            ("Quit", "quit")])
def test_ask_device_owner(app, tmp_path, monkeypatch, button, expected):
    """Ja = eigenes Gerät, Nein = Gerät der betroffenen Person, Abbrechen = kein Fall, FED beenden."""
    from PySide6 import QtWidgets

    from fed.core import config as cfgmod
    from fed.gui import assistant as ga
    from fed.gui.main_window import MainWindow

    seen = {}

    def fake_exec(box):
        seen["buttons"] = [b.text() for b in box.buttons()]
        seen["text"] = box.text()
        if button == "Quit":
            next(b for b in box.buttons() if b.text() == "FED beenden").click()
            return 0
        return getattr(QtWidgets.QMessageBox.StandardButton, button).value

    monkeypatch.setattr(QtWidgets.QMessageBox, "exec", fake_exec)
    monkeypatch.setattr(QtWidgets.QMessageBox, "warning",
                        staticmethod(lambda *a, **k: QtWidgets.QMessageBox.StandardButton.Yes))
    assert ga.ask_device_owner(None) == expected
    assert len(seen["buttons"]) == 4 and "eigenen Gerät" in seen["text"]

    cfg = cfgmod.Config()
    cfg.examiner_name = "Max Mustermann"
    cfg.programm.zielpfad = str(tmp_path)
    cfg.programm.ntp = False
    w = MainWindow(cfg, tmp_path)
    a = w.assistant
    _pump(app, lambda: a.st.system is not None)
    a.p_case.case_no.setText(f"AZ-{button}")
    w.show()
    a.p_case._go_clicked()
    if expected == "quit":
        app.processEvents()
        assert a.st.case is None and not w.isVisible()
    elif expected is None:
        assert a.st.case is None and not any(tmp_path.glob("AZ-Cancel*")) and w.isVisible()
    else:
        assert a.st.case.info()["device_owner"] == expected
        assert a.st.case.device_owner_history()[0]["hostname"] == a.st.system.hostname
    w.close()
    w.deleteLater()
    app.processEvents()


@pytest.mark.parametrize("button,expected", [("Yes", "own"), ("No", "subject"), ("Quit", "quit"), ("Esc", "quit")])
def test_ask_device_owner_at_start(app, monkeypatch, button, expected):
    """Beim Programmstart: Ja / Nein / FED beenden – Enter und Esc beenden."""
    from PySide6 import QtWidgets

    from fed.gui import assistant as ga

    seen = {}

    def fake_exec(box):
        seen["buttons"] = [b.text() for b in box.buttons()]
        seen["escape"] = box.escapeButton().text()
        if button in ("Quit", "Esc"):
            box.escapeButton().click() if button == "Esc" else next(
                b for b in box.buttons() if b.text() == "FED beenden").click()
            return 0
        return getattr(QtWidgets.QMessageBox.StandardButton, button).value

    monkeypatch.setattr(QtWidgets.QMessageBox, "exec", fake_exec)
    assert ga.ask_device_owner(None, at_start=True) == expected
    assert len(seen["buttons"]) == 3 and seen["escape"] == "FED beenden"


def test_ask_device_owner_session_answer_used_for_case(app, tmp_path, monkeypatch):
    """Antwort vom Start gilt für jeden Fall der Sitzung (auch nach „Neuer Fall“) – keine zweite Frage."""
    from PySide6 import QtWidgets

    from fed.core import config as cfgmod
    from fed.gui import assistant as ga
    from fed.gui.main_window import MainWindow

    monkeypatch.setattr(ga, "ask_device_owner", lambda *a, **k: (_ for _ in ()).throw(AssertionError("gefragt")))
    monkeypatch.setattr(QtWidgets.QMessageBox, "warning",
                        staticmethod(lambda *a, **k: QtWidgets.QMessageBox.StandardButton.Yes))
    cfg = cfgmod.Config()
    cfg.examiner_name = "Max Mustermann"
    cfg.programm.zielpfad = str(tmp_path)
    cfg.programm.ntp = False
    w = MainWindow(cfg, tmp_path)
    a = w.assistant
    a.st.device_owner, a.st.device_asked_utc = "subject", "2026-11-11T08:00:00+00:00"
    _pump(app, lambda: a.st.system is not None)
    a.p_case.case_no.setText("AZ-1")
    a.p_case._go_clicked()
    h = a.st.case.device_owner_history()
    assert h[0]["device_owner"] == "subject"
    row = a.st.case.conn.execute("SELECT details FROM log WHERE event = 'device_owner_confirmed'").fetchone()[0]
    assert "2026-11-11T08:00:00" in row
    a._new_case()
    assert a.st.device_owner == "subject"
    a.p_case.case_no.setText("AZ-2")
    a.p_case._go_clicked()
    assert a.st.case.info()["device_owner"] == "subject"
    w.close()
    w.deleteLater()
    app.processEvents()


def test_encrypt_dialog_defaults_to_no_copy(app):
    """Der Abschluss-Dialog erscheint, ist aber standardmäßig OHNE verschlüsselte Kopie (kein Passwort)."""
    from fed.gui.encrypt_dialog import EncryptDialog

    dlg = EncryptDialog()
    assert not dlg.enable.isChecked()  # Checkbox standardmäßig aus
    assert not dlg.options.isVisible()  # Passwort-Optionen ausgeblendet
    dlg._accept()
    assert dlg.password is None  # OK ohne Haken -> keine verschlüsselte Kopie
    dlg.deleteLater()


def test_settings_dirty_detection(app, tmp_path):
    """Einstellungen erkennen ungespeicherte Änderungen; Speichern setzt den Stand zurück."""
    from fed.core import config as cfgmod
    from fed.gui.settings import SettingsTab

    cfg = cfgmod.Config()
    cfg.source_path = tmp_path / "config.toml"
    st = SettingsTab(cfg, tmp_path / "config.toml")
    assert not st.is_dirty()  # frisch geladen -> sauber
    old = st.conns.value()
    st.conns.setValue(old - 1 if old > 1 else old + 1)
    assert st.is_dirty()  # geänderter Wert -> ungespeichert
    assert st.save() is True
    assert not st.is_dirty()  # nach Speichern wieder sauber
    assert cfg.programm.verbindungen == st.conns.value()  # Wert ist im cfg angekommen
    st.deleteLater()


def test_settings_report_content_roundtrip(app, tmp_path):
    """Einstellungen laden/speichern die neuen Bericht-Felder (DKIM-Liste, DNS-Modus)."""
    from fed.core import config as cfgmod
    from fed.gui.settings import SettingsTab

    cfg = cfgmod.Config()
    cfg.programm.bericht_dkimliste = "ordner"
    cfg.programm.bericht_dkimliste_max = 250
    cfg.programm.bericht_dns = "tabelle"
    cfg.source_path = tmp_path / "config.toml"
    st = SettingsTab(cfg, tmp_path / "config.toml")
    assert st.dkimlist.currentData() == "ordner"       # geladen
    assert st.dns.currentData() == "tabelle"
    assert not st.dkimlist_max.isEnabled()             # Grenze nur bei "auto"

    st.dkimlist.setCurrentIndex(st.dkimlist.findData("auto"))
    st.dkimlist_max.setValue(123)
    st.dns.setCurrentIndex(st.dns.findData("csv"))
    assert st.save() is True
    assert cfg.programm.bericht_dkimliste == "auto"     # gespeichert
    assert cfg.programm.bericht_dkimliste_max == 123
    assert cfg.programm.bericht_dns == "csv"
    st.deleteLater()


def test_acquire_page_add_account_shortcut(app):
    """Strg+K/Cmd+K öffnet „Weiteres Konto", wenn der Knopf sichtbar und aktiv ist."""
    from PySide6.QtGui import QShortcut

    from fed.core.config import Config
    from fed.gui.assistant import AcquirePage, State

    p = AcquirePage(State(Config()))
    scs = {s.key().toString(): s for s in p.findChildren(QShortcut)}
    assert "Ctrl+K" in scs
    p.show()  # offscreen: nötig, damit isVisible() der Knöpfe greift
    clicked = []
    p.another_btn.animateClick = lambda *a: clicked.append(1)  # animateClick ist asynchron
    p.another_btn.setVisible(True)
    p.another_btn.setEnabled(True)
    scs["Ctrl+K"].activated.emit()
    assert clicked == [1]
    clicked.clear()
    p.another_btn.setVisible(False)             # nicht angeboten -> kein Auslösen
    scs["Ctrl+K"].activated.emit()
    assert clicked == []
    p.hide()
    p.deleteLater()


def test_worker_expected_error_no_traceback():
    """Erwartete Fehler (Login/Netz) markiert der Worker nicht als Programmierfehler."""
    import imaplib

    from fed.core.imap_reader import ImapError
    from fed.gui.widgets import _is_expected_error

    assert _is_expected_error(imaplib.IMAP4.error("Invalid login"))
    assert _is_expected_error(ImapError("x"))
    assert _is_expected_error(ConnectionError("refused"))
    assert not _is_expected_error(TypeError("bug"))
    assert not _is_expected_error(KeyError("k"))


def test_account_page_shortcuts(app):
    """Strg+T löst „Verbindung testen“ aus, Strg+P „Postfach scannen“ – nur wenn der Knopf aktiv ist."""
    from PySide6.QtGui import QShortcut

    from fed.core.config import Config
    from fed.gui.assistant import AccountPage, State

    p = AccountPage(State(Config()))
    scs = {s.key().toString(): s for s in p.findChildren(QShortcut)}
    assert "Ctrl+T" in scs and "Ctrl+P" in scs
    clicked = []
    p.test_btn.setEnabled(True)
    p.test_btn.animateClick = lambda *a: clicked.append("test")
    scs["Ctrl+T"].activated.emit()
    assert clicked == ["test"]
    # deaktivierter Knopf -> kein Auslösen
    p.go.setEnabled(False)
    p.go.animateClick = lambda *a: clicked.append("scan")
    scs["Ctrl+P"].activated.emit()
    assert clicked == ["test"]
    p.deleteLater()


def test_window_title_shows_version_and_os(app, tmp_path):
    """Der Fenstertitel nennt Version und Betriebssystem (Windows/macOS/Linux)."""
    from fed.core.config import Config
    from fed.gui.main_window import MainWindow, _os_name

    cfg = Config()
    cfg.programm.ntp = False
    cfg.programm.zielpfad = str(tmp_path)
    w = MainWindow(cfg, tmp_path)
    title = w.windowTitle()
    assert "Forensic Email Duplicator" in title
    assert _os_name() in title
    assert _os_name() in ("Windows", "macOS", "Linux")


def test_finalize_activity_spinner_animates(app):
    """Die Abschluss-Seite zeigt eine laufende Aktivität (animierte Punkte), auch ohne neue Meldung."""
    from fed.core.config import Config
    from fed.gui.assistant import FinishPage, State

    p = FinishPage(State(Config()))
    p.busy = True
    p._show_step("Berichte werden erzeugt")
    v = p.check_kv.values["Aktueller Schritt"]
    first = v.text()
    p._tick_spin()
    p._tick_spin()
    assert v.text() != first                 # Punkte animieren
    assert "Berichte werden erzeugt" in v.text()
    p.busy = False                           # gestoppt -> keine Punkte
    p._render_step()
    assert v.text().strip() == "Berichte werden erzeugt"
    p.deleteLater()


def test_finalize_total_eta_casewide(app, monkeypatch):
    """Gesamt-Restzeit zählt über ALLE Konten gepackte + geprüfte Bytes und rechnet über den
    gemittelten Gesamtdurchsatz hoch – sie fällt nicht nach jedem Konto auf 0."""
    import time

    from fed.core.config import Config
    from fed.gui.assistant import FinishPage, State

    clock = [0.0]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])

    class FakeProg:
        def __init__(self, phase, bytes_done, account="DF001", case_bytes_total=1000):
            self.phase, self.bytes_done = phase, bytes_done
            self.account, self.case_bytes_total = account, case_bytes_total

    p = FinishPage(State(Config()))
    p._reset_finalize_eta()

    # Vor der ersten belastbaren Rate: keine Schätzung
    assert p._finalize_eta(FakeProg("zip", 0)) is None
    clock[0] = 1.0
    assert p._finalize_eta(FakeProg("zip", 100)) is None          # t0 gesetzt, elapsed 0

    # 1 s später 200 B gepackt -> 200 B/s, geplant 2000 (zip+verify), Rest 1800 -> 9 s
    clock[0] = 2.0
    assert abs(p._finalize_eta(FakeProg("zip", 200)) - 9.0) < 0.6

    # ZIP fertig (1000), Wechsel in verify (neue Byte-Phase, kein Rücksprung auf 0)
    clock[0] = 5.0
    p._finalize_eta(FakeProg("zip", 1000))                        # done = 1000
    eta = p._finalize_eta(FakeProg("verify", 300))                # done = 1300, noch > 0
    assert isinstance(eta, float) and eta > 0

    # Kurz vor Schluss (>= 99 % erledigt) -> "final" statt 00:00:00
    assert p._finalize_eta(FakeProg("verify", 995)) == "final"    # done = 1995 von 2000

    # Schlussphasen ohne Byte-Zähler -> "final"
    assert p._finalize_eta(FakeProg("reports_pdf", 0)) == "final"
    assert p._finalize_eta(FakeProg("encrypt", 0)) == "final"
    # Ohne Fall-Gesamtbytes keine Schätzung
    assert p._finalize_eta(FakeProg("zip", 10, case_bytes_total=0)) is None
    p.deleteLater()


def test_check_icon_is_packaged_resource_not_temp(app, tmp_path, monkeypatch):
    """Forensik: Beim Erzeugen des Stylesheets darf nichts ins System-Temp geschrieben
    werden – das Häkchen-SVG kommt als mitgelieferte Ressource aus dem Programm."""
    import tempfile
    from pathlib import Path

    from fed.gui import theme

    # Temp auf einen leeren, kontrollierten Ordner lenken und danach prüfen, dass er leer bleibt.
    fake_tmp = tmp_path / "systemp"
    fake_tmp.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(fake_tmp))

    icon = theme._check_icon()
    assert icon.endswith("fed/resources/fed-check.svg") or icon.endswith("fed-check.svg")
    assert Path(icon).exists()                      # echter Pfad in der Paketressource
    assert "fed-check.svg" not in [p.name for p in fake_tmp.iterdir()]  # nichts ins Temp geschrieben

    css = theme.stylesheet(theme.DARK)
    assert 'image: url("' in css and "fed-check.svg" in css
    assert list(fake_tmp.iterdir()) == []           # Stylesheet-Bau schreibt nichts ins Temp
