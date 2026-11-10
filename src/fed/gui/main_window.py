"""Hauptfenster: Kopfzeile mit Logo, Bereichen und Hell/Dunkel; Statusleiste mit Verbindungsanzeige."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSettings, QSize, Qt
from PySide6.QtWidgets import (QApplication, QButtonGroup, QHBoxLayout, QLabel, QMainWindow, QMessageBox,
                               QPushButton, QStackedWidget, QVBoxLayout, QWidget)

from fed import __version__
from fed.core.config import CONFIG_FILENAME, Config
from fed.gui import theme
from fed.gui.assistant import Assistant
from fed.gui.history import HistoryTab
from fed.gui.settings import SettingsTab
from fed.gui.status import ConnectionStatus
from fed.gui.widgets import LOGO, MOON, SUN, ElidedLabel, label, svg_icon


class MainWindow(QMainWindow):
    def __init__(self, cfg: Config, program_dir: Path | None = None) -> None:
        super().__init__()
        self.cfg = cfg
        self.setWindowTitle(f"FED – Forensic Email Duplicator {__version__}")
        self._place_window()
        self.dark = theme.is_dark(cfg.programm.design)

        root = QWidget()
        rl = QVBoxLayout(root)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(0)
        header = QWidget()
        header.setObjectName("Header")
        header.setFixedHeight(56)
        hl = QHBoxLayout(header)
        hl.setContentsMargins(20, 0, 20, 0)
        logo = QLabel()
        logo.setPixmap(svg_icon(LOGO, 28).pixmap(QSize(28, 28)))
        hl.addWidget(logo)
        hl.addWidget(label("FED", None))
        hl.itemAt(1).widget().setStyleSheet("font-weight:700;font-size:15px;")
        self.case_lbl = ElidedLabel("Forensic Email Duplicator", "HeaderSub", Qt.TextElideMode.ElideRight)
        self.case_lbl.setMinimumWidth(160)
        hl.addWidget(self.case_lbl)
        hl.addStretch(1)
        self.nav = QButtonGroup(self)
        for i, t in enumerate((self.tr("Sicherung"), self.tr("Historie"), self.tr("Einstellungen"))):
            b = QPushButton(t)
            b.setObjectName("NavButton")
            b.setCheckable(True)
            self.nav.addButton(b, i)
            hl.addWidget(b)
        hl.addStretch(1)
        self.agency = ElidedLabel(cfg.agentur.name, "HeaderSub", Qt.TextElideMode.ElideRight)
        self.agency.setMinimumWidth(120)
        self.agency.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        hl.addWidget(self.agency)
        self.theme_btn = QPushButton()
        self.theme_btn.setObjectName("ThemeButton")
        self.theme_btn.setToolTip(self.tr("Hell / Dunkel umschalten"))
        self.theme_btn.setFixedSize(40, 34)
        self.theme_btn.clicked.connect(self.toggle_theme)
        hl.addWidget(self.theme_btn)
        rl.addWidget(header)

        self.stack = QStackedWidget()
        self.assistant = Assistant(cfg)
        self.history = HistoryTab(lambda: self.assistant.st.case)
        cfg_path = cfg.source_path or ((program_dir or Path.cwd()) / CONFIG_FILENAME)
        self.settings = SettingsTab(cfg, cfg_path)
        for w in (self.assistant, self.history, self.settings):
            self.stack.addWidget(w)
        rl.addWidget(self.stack, 1)
        self.setCentralWidget(root)
        self.nav.idClicked.connect(self.stack.setCurrentIndex)
        self.nav.button(0).setChecked(True)

        self.case_info = ElidedLabel(self.tr("Kein Fall geöffnet"))
        self.case_info.max_hint = 2000
        self.statusBar().addWidget(self.case_info, 1)
        self.connection = ConnectionStatus(self)
        self.statusBar().addPermanentWidget(self.connection)
        self.assistant.case_changed.connect(self._case_changed)
        self.settings.saved.connect(self._settings_saved)
        self.settings.theme_preview.connect(lambda m: self.apply_theme(theme.is_dark(m)))
        self.apply_theme(self.dark)

    def apply_theme(self, dark: bool) -> None:
        self.dark = dark
        theme.apply(QApplication.instance(), "dunkel" if dark else "hell")
        self.theme_btn.setIcon(svg_icon(SUN if dark else MOON, 18))
        self.connection.refresh_style()

    def toggle_theme(self) -> None:
        self.apply_theme(not self.dark)

    def _case_changed(self) -> None:
        case = self.assistant.st.case
        if case:
            i = case.info()
            self.case_lbl.setText(self.tr("Fall {0}").format(i["case_number"]))
            self.case_info.setText(f"Fall {i['case_number']} · {case.root}"
                                   + (self.tr(" · abgeschlossen") if i["closed_utc"] else ""))
        else:
            self.case_lbl.setText("Forensic Email Duplicator")
            self.case_info.setText(self.tr("Kein Fall geöffnet"))

    def _settings_saved(self, cfg: Config) -> None:
        self.agency.setText(cfg.agentur.name)
        self.apply_theme(theme.is_dark(cfg.programm.design))
        self.statusBar().showMessage(self.tr("Einstellungen gespeichert"), 4000)

    def _place_window(self) -> None:
        """Letzte Fenstergröße wiederherstellen, sonst passend zum Bildschirm (max. 1440 × 940) mittig."""
        geo = QSettings().value("window/geometry")
        screen = QApplication.primaryScreen()
        if geo is not None and self.restoreGeometry(geo):
            if screen is None or any(s.availableGeometry().intersects(self.frameGeometry())
                                     for s in QApplication.screens()):
                return
        if screen is None:
            self.resize(1280, 820)
            return
        avail = screen.availableGeometry()
        w = min(1440, int(avail.width() * 0.9))
        h = min(940, int(avail.height() * 0.9))
        self.resize(w, h)
        self.move(avail.x() + (avail.width() - w) // 2, avail.y() + (avail.height() - h) // 2)
        if avail.width() < 1100 or avail.height() < 720:  # kleiner Bildschirm: ganz nutzen
            self.setWindowState(self.windowState() | Qt.WindowState.WindowMaximized)

    def closeEvent(self, e) -> None:
        running = self.assistant.jobs.running()
        if running:
            r = QMessageBox.question(self, "FED", self.tr("Es laufen noch {0} Sicherungen:\n\n{1}\n\nAbbrechen und "
                                                          "beenden? Abgebrochene Konten lassen sich später "
                                                          "fortsetzen.").format(
                len(running), "\n".join(f"{j.evidence} · {j.email} – {j.percent} %" for j in running)))
            if r != QMessageBox.StandardButton.Yes:
                e.ignore()
                return
            self.assistant.jobs.cancel_all(15000)
        self.assistant.vault.clear()  # Anmeldedaten der Sitzung verwerfen
        from fed.core import keepawake

        while keepawake.shared().active:
            keepawake.shared().release()
        if self.assistant.st.case:
            self.assistant.st.case.close()
        QSettings().setValue("window/geometry", self.saveGeometry())
        e.accept()
