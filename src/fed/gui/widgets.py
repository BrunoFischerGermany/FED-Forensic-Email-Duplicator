"""Wiederverwendbare Bausteine: Karte, Überschriften, Schlüssel/Wert-Liste, Passwortfeld, Worker."""

from __future__ import annotations

import traceback
from collections.abc import Callable

from PySide6.QtCore import QObject, QSize, Qt, QThread, QTimer, Signal, Slot
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (QApplication, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QSizePolicy,
                               QVBoxLayout, QWidget)

from fed.core.cancel import CancelToken

EYE = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{c}" stroke-width="2">'
       '<path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/>{x}</svg>')
EYE_OFF = '<path d="M3 3l18 18"/>'
SUN = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="#E6E8EC" stroke-width="2">'
       '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2'
       'M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></svg>')
MOON = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="#E6E8EC" stroke-width="2">'
        '<path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/></svg>')
LOGO = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">'
        '<ellipse cx="50" cy="52" rx="30" ry="35" fill="none" stroke="#E6E8EC" stroke-width="7" '
        'stroke-linecap="round" stroke-dasharray="110 14 60 14"/>'
        '<circle cx="50" cy="52" r="8" fill="none" stroke="#8AB0FF" stroke-width="7"/>'
        '<path d="M58 44 V55 a5.5 5.5 0 0 0 11 0 V52 A19 19 0 1 0 64.6 64.2" fill="none" stroke="#8AB0FF" '
        'stroke-width="7" stroke-linecap="round" stroke-linejoin="round"/></svg>')


def svg_icon(svg: str, size: int = 20) -> QIcon:
    r = QSvgRenderer(svg.encode())
    pm = QPixmap(QSize(size * 2, size * 2))
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    r.render(p)
    p.end()
    return QIcon(pm)


def label(text: str = "", name: str | None = None, wrap: bool = False, selectable: bool = False) -> QLabel:
    lab = QLabel(text)
    if name:
        lab.setObjectName(name)
    lab.setWordWrap(wrap)
    if selectable:
        lab.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return lab


class ElidedLabel(QLabel):
    """Einzeilige Anzeige für lange Pfade/Namen: kürzt in der Mitte mit „…“ statt das Fenster zu
    verbreitern. Der volle Text steht im Tooltip und wird mit „Kopieren“ (Rechtsklick) übernommen."""

    def __init__(self, text: str = "", name: str | None = None, mode=Qt.TextElideMode.ElideMiddle,
                 parent=None) -> None:
        super().__init__(parent)
        if name:
            self.setObjectName(name)
        self._full = ""
        self._mode = mode
        self.max_hint = 420  # bevorzugte Breite höchstens so groß – darüber wird gekürzt
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.ActionsContextMenu)
        from PySide6.QtGui import QAction

        copy = QAction(self.tr("Kopieren"), self)
        copy.triggered.connect(lambda: QApplication.clipboard().setText(self._full))
        self.addAction(copy)
        self.setText(text)

    def setText(self, text: str) -> None:
        self._full = text
        self.setToolTip(text)
        self.updateGeometry()
        self._elide()

    def text(self) -> str:
        return self._full

    def full_text(self) -> str:
        return self._full

    def sizeHint(self) -> QSize:
        h = super().sizeHint()
        w = self.fontMetrics().horizontalAdvance(self._full) + 4
        return QSize(min(w, self.max_hint), h.height())

    def minimumSizeHint(self) -> QSize:
        return QSize(min(self.minimumWidth() or 40, self.sizeHint().width()), super().minimumSizeHint().height())

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        self._elide()

    def _elide(self) -> None:
        shown = self.fontMetrics().elidedText(self._full, self._mode, max(self.width(), 40))
        super().setText(shown)


def restyle(w: QWidget, name: str) -> None:
    w.setObjectName(name)
    w.style().unpolish(w)
    w.style().polish(w)


class Card(QFrame):
    def __init__(self, title: str | None = None, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("Card")
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(22, 20, 22, 20)
        self.lay.setSpacing(12)
        self.title = None
        if title:
            self.title = label(title, "H2")
            self.lay.addWidget(self.title)

    def add(self, w) -> QWidget:
        if isinstance(w, QWidget):
            self.lay.addWidget(w)
        else:
            self.lay.addLayout(w)
        return w


class KeyValues(QWidget):
    """Zweispaltige Liste wie „Systemprüfung“ im Mockup."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.grid = QGridLayout(self)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setHorizontalSpacing(16)
        self.grid.setVerticalSpacing(10)
        self.grid.setColumnStretch(1, 1)
        self.values: dict[str, QLabel] = {}

    def set(self, key: str, value: str, state: str | None = None, mono: bool = False) -> None:
        if key not in self.values:
            row = self.grid.rowCount()
            self.grid.addWidget(label(key, "Muted"), row, 0, Qt.AlignmentFlag.AlignTop)
            v = label("", wrap=True, selectable=True)
            v.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop)
            v.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            v.setMinimumWidth(120)
            self.grid.addWidget(v, row, 1)
            self.values[key] = v
        v = self.values[key]
        v.setText(value)
        restyle(v, {"ok": "Ok", "warn": "Warn", "error": "Error"}.get(state or "", "Mono" if mono else ""))

    def clear(self) -> None:
        while self.grid.count():
            w = self.grid.takeAt(0).widget()
            if w:
                w.deleteLater()
        self.values.clear()


class PasswordField(QLineEdit):
    """Passwortfeld mit Augen-Symbol; wird beim Verlassen wieder verborgen (``hide_text``)."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setEchoMode(QLineEdit.EchoMode.Password)
        self._action = self.addAction(svg_icon(EYE.format(c="#5B6270", x="")), QLineEdit.ActionPosition.TrailingPosition)
        self._action.setCheckable(True)
        self._action.setToolTip(self.tr("Passwort ein-/ausblenden"))
        self._action.toggled.connect(self._toggle)

    def _toggle(self, visible: bool) -> None:
        self.setEchoMode(QLineEdit.EchoMode.Normal if visible else QLineEdit.EchoMode.Password)
        self._action.setIcon(svg_icon(EYE.format(c="#5B6270", x=EYE_OFF if visible else "")))

    def hide_text(self) -> None:
        self._action.setChecked(False)


class Segmented(QWidget):
    changed = Signal(str)

    def __init__(self, options: list[tuple[str, str]], parent=None) -> None:
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.buttons: dict[str, QPushButton] = {}
        for key, text in options:
            b = QPushButton(text)
            b.setObjectName("Seg")
            b.setCheckable(True)
            b.setAutoExclusive(True)
            b.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            b.clicked.connect(lambda _=False, k=key: self.changed.emit(k))
            lay.addWidget(b)
            self.buttons[key] = b
        next(iter(self.buttons.values())).setChecked(True)

    def value(self) -> str:
        return next(k for k, b in self.buttons.items() if b.isChecked())

    def set_value(self, key: str) -> None:
        self.buttons[key].setChecked(True)


def row(*widgets, stretch_first: bool = False, spacing: int = 12) -> QHBoxLayout:
    lay = QHBoxLayout()
    lay.setSpacing(spacing)
    if not stretch_first:
        lay.addStretch(0)
    for w in widgets:
        if w == "stretch":
            lay.addStretch(1)
        elif isinstance(w, QWidget):
            lay.addWidget(w)
        else:
            lay.addLayout(w)
    return lay


# ---------------------------------------------------------------- Hintergrundarbeit
class Worker(QObject):
    """Führt ``fn(cancel, report)`` in einem eigenen Thread aus; GUI bleibt bedienbar."""

    progress = Signal(object)
    done = Signal(object)
    failed = Signal(str, str)
    finished = Signal()

    def __init__(self, fn: Callable, parent=None) -> None:
        super().__init__(None)
        self.fn = fn
        self.cancel = CancelToken()
        self.thread = QThread()
        self.moveToThread(self.thread)
        self.thread.started.connect(self._run)
        self.finished.connect(self.thread.quit)

    def _run(self) -> None:
        try:
            self.done.emit(self.fn(self.cancel, self.progress.emit))
        except Exception as e:
            self.failed.emit(str(e) or type(e).__name__, traceback.format_exc())
        finally:
            self.finished.emit()

    def start(self) -> Worker:
        self.thread.start()
        return self


_running: set[Worker] = set()


class _Relay(QObject):
    """Lebt im GUI-Thread; leitet Worker-Signale an beliebige Python-Funktionen weiter.

    Ohne diesen Umweg würden Lambdas/Funktionen, die an Signale eines Objekts im
    Worker-Thread hängen, im Worker-Thread laufen – und dort Widgets anfassen.
    """

    def __init__(self, on_done, on_failed, on_progress) -> None:
        super().__init__(QApplication.instance())
        self._done, self._failed, self._progress = on_done, on_failed, on_progress

    @Slot(object)
    def done(self, value) -> None:
        if self._done:
            self._done(value)

    @Slot(str, str)
    def failed(self, err, tb) -> None:
        if self._failed:
            self._failed(err, tb)

    @Slot(object)
    def progress(self, value) -> None:
        if self._progress:
            self._progress(value)


def run_async(fn: Callable, on_done=None, on_failed=None, on_progress=None, parent=None) -> Worker:
    w = Worker(fn, parent)
    _running.add(w)
    relay = _Relay(on_done, on_failed, on_progress)
    w.relay = relay
    queued = Qt.ConnectionType.QueuedConnection
    w.done.connect(relay.done, queued)
    w.failed.connect(relay.failed, queued)
    w.progress.connect(relay.progress, queued)
    w.thread.finished.connect(relay.deleteLater)
    # erst freigeben, wenn alle Signale im GUI-Thread zugestellt sind
    w.thread.finished.connect(lambda: QTimer.singleShot(500, lambda: _running.discard(w)))
    return w.start()


def vbox(*items, spacing: int = 16, margins=(0, 0, 0, 0)) -> QVBoxLayout:
    lay = QVBoxLayout()
    lay.setSpacing(spacing)
    lay.setContentsMargins(*margins)
    for it in items:
        if it == "stretch":
            lay.addStretch(1)
        elif isinstance(it, QWidget):
            lay.addWidget(it)
        else:
            lay.addLayout(it)
    return lay
