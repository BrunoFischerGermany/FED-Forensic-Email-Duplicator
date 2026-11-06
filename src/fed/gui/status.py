"""Verbindungsanzeige in der Statusleiste (Internet; IMAP und Antwortzeit folgen)."""

from __future__ import annotations

from PySide6.QtCore import QThreadPool, QTimer, Signal
from PySide6.QtWidgets import QLabel

from fed.core.syscheck import is_online


class ConnectionStatus(QLabel):
    INTERVAL_MS = 10_000
    _result = Signal(bool)  # aus dem Worker-Thread, wird im GUI-Thread zugestellt

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._online: bool | None = None
        self._result.connect(self._set)
        self._render()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._check)
        self._timer.start(self.INTERVAL_MS)
        QTimer.singleShot(0, self._check)

    def _check(self) -> None:
        QThreadPool.globalInstance().start(lambda: self._result.emit(is_online()))

    def _set(self, online: bool) -> None:
        self._online = online
        self._render()

    def _render(self) -> None:
        match self._online:
            case True:
                self.setText("● " + self.tr("Internet: online"))
                self.setStyleSheet("color: #1a7f37;")
            case False:
                self.setText("● " + self.tr("Internet: offline"))
                self.setStyleSheet("color: #cf222e;")
            case None:
                self.setText("● " + self.tr("Internet: prüfe …"))
                self.setStyleSheet("color: gray;")
