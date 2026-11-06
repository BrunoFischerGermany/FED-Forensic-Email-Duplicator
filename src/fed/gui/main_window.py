"""Hauptfenster mit Tabs „Sicherung“ (Assistent) und „Historie“ sowie Statusleiste."""

from __future__ import annotations

from PySide6.QtWidgets import QLabel, QMainWindow, QTabWidget

from fed import APP_NAME, __version__
from fed.core.config import Config
from fed.gui.status import ConnectionStatus
from fed.gui.wizard import AcquisitionWizard


class MainWindow(QMainWindow):
    def __init__(self, cfg: Config) -> None:
        super().__init__()
        self.cfg = cfg
        self.setWindowTitle(f"{APP_NAME} {__version__}")
        self.resize(1100, 750)

        tabs = QTabWidget()
        tabs.addTab(AcquisitionWizard(cfg), self.tr("Sicherung"))
        tabs.addTab(QLabel(self.tr("Historie – folgt")), self.tr("Historie"))
        self.setCentralWidget(tabs)

        self.connection = ConnectionStatus(self)
        self.statusBar().addPermanentWidget(self.connection)
