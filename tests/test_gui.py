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
