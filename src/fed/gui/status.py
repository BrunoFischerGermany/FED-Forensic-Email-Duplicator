"""Verbindungsanzeige in der Statusleiste (Internet; IMAP und Antwortzeit folgen)."""

from __future__ import annotations

from PySide6.QtCore import QThreadPool, QTimer, Signal
from PySide6.QtWidgets import QLabel

from fed.core.syscheck import is_online
from fed.gui import theme


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

    def refresh_style(self) -> None:
        self._render()

    def _render(self) -> None:
        t = theme.tokens()
        match self._online:
            case True:
                self.setText("● " + self.tr("Internet: online"))
                color = t["ok"]
            case False:
                self.setText("● " + self.tr("Internet: offline"))
                color = t["danger"]
            case _:
                self.setText("● " + self.tr("Internet: prüfe …"))
                color = t["muted"]
        self.setStyleSheet(f"color: {color}; font-weight: 600; font-size: 12px;")
