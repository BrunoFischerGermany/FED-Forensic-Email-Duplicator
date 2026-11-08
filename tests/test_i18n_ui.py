"""Sprachprüfung der Oberfläche: Auf Englisch darf kein deutscher Programmtext zu sehen sein.

Durchläuft Assistent (Case → Konto → Ordner → Sicherung → Abschluss), Historie, Einstellungen,
Ersteinrichtung, Verschlüsselungs-Dialog und Mail-Betrachter und sammelt alle sichtbaren Texte
(Beschriftungen, Knöpfe, Tooltips, Platzhalter, Reiter, Spaltenköpfe, Auswahllisten). Inhalte aus
Mails und Case (Tabellenzellen, Betreffs, Namen) werden nicht geprüft.
"""

import os
import re
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")

# Wörter, die nur in deutschem Programmtext vorkommen (keine Testdaten wie „Rechnung“)
GERMAN = re.compile(
    r"[äöüßÄÖÜ]|\b(und|oder|nicht|keine?|wird|werden|bitte|Konto|Konten|Sicherung|Datei(en)?|Ordner|"
    r"Bericht|Anlagen?|Prüfung|prüfen|Einstellungen|Abbrechen|Weiter|Zurück|Schließen|Speichern|"
    r"Passwort|Kennwort|Zeitraum|Fortsetzen|Neubeginn|Beenden|Hinweis|Ergebnis|Vorschau|Echtheit|"
    r"gesichert|verschlüsselt|Anmeldung|Absender|Empfänger|Betreff|ja|nein|mit|ohne|für|bei|Quelle|MEZ|MESZ|"
    r"Arbeitsordner|Versuch|Eingang|Zeit|Uhr|Seite|von|bis|Größe|Ausgabe|gelöscht|vorhanden|erstellt|geprüft)\b")
ALLOWED = {
    "FED", "Forensic Email Duplicator", "Hex", "SHA-256", "MD5", "SHA-1", "CC", "UID", "IMAP", "TLS",
    "Deutsch",  # Sprachname in der Auswahl steht immer in der eigenen Sprache
}


def _texts(root) -> set[str]:
    from PySide6.QtWidgets import (QAbstractButton, QComboBox, QGroupBox, QHeaderView, QLabel,
                                   QLineEdit, QTabWidget, QTreeWidget)

    out: set[str] = set()
    widgets = [root, *root.findChildren(QtWidgets.QWidget)]
    for w in widgets:
        if w.toolTip():
            out.add(w.toolTip())
        if w.isWindow() and w.windowTitle():
            out.add(w.windowTitle())
        if isinstance(w, QLabel) and not w.objectName().startswith("Data"):
            # Werte in KeyValues sind Daten (rechte Spalte, selektierbar) – nur Beschriftungen prüfen
            if not (w.textInteractionFlags() & w.textInteractionFlags().TextSelectableByMouse):
                out.add(w.text())
        elif isinstance(w, QAbstractButton):
            out.add(w.text())
        elif isinstance(w, QLineEdit):
            out.add(w.placeholderText())
        elif isinstance(w, QGroupBox):
            out.add(w.title())
        elif isinstance(w, QTabWidget):
            out.update(w.tabText(i) for i in range(w.count()))
        elif isinstance(w, QComboBox):
            out.update(w.itemText(i) for i in range(w.count()))
        elif isinstance(w, QHeaderView) and w.model() is not None:
            m = w.model()
            n = m.columnCount() if w.orientation().value == 1 else 0
            out.update(str(m.headerData(i, w.orientation()) or "") for i in range(n))
        elif isinstance(w, QTreeWidget):
            out.update(w.headerItem().text(i) for i in range(w.columnCount()))
    return {re.sub(r"<[^>]+>", " ", t).strip() for t in out if t and t.strip()}


def _german(texts: set[str], data: set[str]) -> list[str]:
    bad = []
    for t in sorted(texts):
        if t in ALLOWED or t in data:
            continue
        s = t
        for d in data:  # eingebettete Testdaten (Pfade, Namen) ausblenden
            if d and len(d) > 3:
                s = s.replace(d, "")
        if GERMAN.search(s):
            bad.append(t)
    return bad


def _pump(app, until, limit=60):
    t = time.time()
    while time.time() - t < limit:
        app.processEvents()
        time.sleep(0.02)
        if until():
            return
    raise TimeoutError


@pytest.fixture
def english_app():
    from fed.gui import i18n

    a = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    i18n.install("en")
    yield a
    i18n.install("de")


def test_english_ui_shows_no_german(english_app, imap_server, tmp_path, monkeypatch, test_mails):
    app = english_app
    if hasattr(time, "tzset"):  # deutsche Zeitzone, damit „MEZ“ auffallen würde
        monkeypatch.setenv("TZ", "Europe/Berlin")
        time.tzset()
    from fed.core import auth
    from fed.core import config as cfgmod
    from fed.gui import assistant as ga
    from fed.gui import encrypt_dialog
    from fed.gui.main_window import MainWindow

    orig = auth.make_connector
    monkeypatch.setattr(ga, "make_connector", lambda *a, **k: orig(*a, cafile=str(imap_server.cafile), **k))
    yes = staticmethod(lambda *a, **k: QtWidgets.QMessageBox.StandardButton.Yes)
    monkeypatch.setattr(QtWidgets.QMessageBox, "warning", yes)
    monkeypatch.setattr(QtWidgets.QMessageBox, "question", yes)
    seen: set[str] = set()
    data = {imap_server.user, imap_server.host, str(tmp_path), "Max Mustermann", "A-01", "Muster Forensik GmbH"}
    data |= {m.folder for m in test_mails} | {p for m in test_mails for p in m.folder.split("/")}

    def enc_exec(dlg):
        seen.update(_texts(dlg))
        dlg._accept()
        return 1

    monkeypatch.setattr(encrypt_dialog.EncryptDialog, "exec", enc_exec)
    cfg = cfgmod.Config()
    cfg.examiner_name = "Max Mustermann"
    cfg.programm.sprache = "en"
    cfg.programm.zielpfad = str(tmp_path)
    cfg.programm.ntp = False
    cfg.source_path = tmp_path / "config.toml"
    w = MainWindow(cfg, tmp_path)
    w.show()
    a = w.assistant
    _pump(app, lambda: a.st.system is not None)
    seen |= _texts(w)
    a.p_case._create()
    data |= {str(v) for v in a.st.case.info().values() if v}
    p = a.p_acc
    seen |= _texts(w)
    p.email.setText(imap_server.user)
    p.evidence.setText("A-01")
    p.server.setText(imap_server.host)
    p.port.setValue(imap_server.port)
    p.password.setText(imap_server.password)
    p._test()
    _pump(app, p.go.isEnabled)
    seen |= _texts(w)
    p._go()
    _pump(app, a.p_fold.go.isEnabled)
    seen |= _texts(w)
    a.p_fold._go()
    _pump(app, a.p_acq.finish_btn.isVisible)
    seen |= _texts(w)
    a.p_acq.finish.emit()
    _pump(app, a.p_fin.open_pdf.isEnabled)
    seen |= _texts(w)
    case = a.st.case
    for i in (1, 2):  # Historie, Einstellungen
        w.nav.button(i).click()
        app.processEvents()
        hist = getattr(w, "history", None)
        if hist is not None and i == 1:
            for t in range(hist.tabs.count()):
                hist.tabs.setCurrentIndex(t)
                hist.refresh()
                app.processEvents()
                seen |= _texts(w)
        seen |= _texts(w)

    from fed.gui.viewer import MailViewer

    for subject in ("Rechnung 2026-1187", "Signatur mit Bild"):
        mid = case.conn.execute("SELECT id FROM message WHERE subject = ?", (subject,)).fetchone()[0]
        dlg = MailViewer(case, mid)
        for r in range(dlg.att.rowCount()):
            dlg.att.selectRow(r)
            app.processEvents()
            for t in range(dlg.side.count()):
                dlg.side.setCurrentIndex(t)
                seen |= _texts(dlg)
        data |= {dlg.view.subject, dlg.view.from_, dlg.view.to, dlg.view.date}
        data |= {x.name for x in dlg.view.attachments}
        dlg.deleteLater()

    from fed.gui.setup_dialog import SetupDialog

    sd = SetupDialog(cfg, tmp_path / "config.toml")
    seen |= _texts(sd)
    sd.deleteLater()
    w.close()
    w.deleteLater()
    app.processEvents()

    bad = _german(seen, data)
    assert not bad, "Deutscher Text in der englischen Oberfläche:\n" + "\n".join(bad)

    # Bericht auf Englisch (folgt der Oberflächensprache)
    from fed.core.report import render_txt

    data |= {r[0] for r in case.conn.execute("SELECT subject FROM message") if r[0]}
    report = render_txt(case, "en", mail_list="full")
    lines = {ln.strip() for ln in report.splitlines() if ln.strip()}
    bad = _german(lines, data | {"Muster Forensik GmbH", "Musterstr. 1"})
    assert not bad, "Deutscher Text im englischen Bericht:\n" + "\n".join(bad)
    assert "CET" in report and "MEZ" not in report
