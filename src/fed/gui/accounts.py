"""Übersicht der Konten im Case: was ist schon gesichert, was fehlt noch."""

from __future__ import annotations

from PySide6.QtCore import QT_TRANSLATE_NOOP, QCoreApplication, Qt, Signal
from PySide6.QtWidgets import QAbstractItemView, QHeaderView, QLabel, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget

from fed.core import timefmt as tf
from fed.core.acquisition import accounts_overview
from fed.core.case import Case
from fed.core.report import _fmt_bytes
from fed.gui.widgets import ElidedLabel, restyle

_STATES = {
    "new": ("○", QT_TRANSLATE_NOOP("AccountsTable", "angelegt, noch nicht gesichert"), "Muted"),
    "running": ("…", QT_TRANSLATE_NOOP("AccountsTable", "Sicherung läuft"), "Muted"),
    "done": ("✓", QT_TRANSLATE_NOOP("AccountsTable", "vollständig gesichert"), "Ok"),
    "partial": ("⚠", QT_TRANSLATE_NOOP("AccountsTable", "fertig, aber weniger als auf dem Server"), "Warn"),
    "cancelled": ("⏸", QT_TRANSLATE_NOOP("AccountsTable", "abgebrochen – fortsetzbar"), "Warn"),
    "failed": ("✗", QT_TRANSLATE_NOOP("AccountsTable", "fehlgeschlagen – fortsetzbar"), "Error"),
}
_COLOR = {"Ok": "ok", "Warn": "warn", "Error": "danger", "Muted": "muted"}


def _tr(text: str) -> str:
    return QCoreApplication.translate("AccountsTable", text)


def state_text(o: dict) -> tuple[str, str, str]:
    """(Symbol, Text, Stil) für ein Konto aus :func:`accounts_overview`."""
    sym, text, style = _STATES.get(o["state"], ("?", o["state"], "Muted"))
    text = _tr(text)
    if o["state"] == "done" and o["date_filter"]:
        text += " " + QCoreApplication.translate("AccountsTable", "(Zeitraum-Filter)")
    return sym, text, style


def _short_time(value: str | None) -> str:
    """Einzeilig: Ortszeit (falls ermittelbar), sonst UTC – volle Angabe im Tooltip."""
    if not value:
        return "–"
    loc = tf.local(value)
    return loc.strftime("%Y-%m-%d %H:%M") if loc else tf.utc_str(value)


def incomplete(case: Case) -> list[dict]:
    """Konten, die noch nicht vollständig gesichert sind."""
    return [o for o in accounts_overview(case) if o["state"] != "done"]


class AccountsTable(QWidget):
    """Tabelle aller Konten im Case (für die Seiten „Konto“ und „Sicherung“)."""

    COLS = (QT_TRANSLATE_NOOP("AccountsTable", "Evidence"), QT_TRANSLATE_NOOP("AccountsTable", "Konto"),
            QT_TRANSLATE_NOOP("AccountsTable", "Status"), QT_TRANSLATE_NOOP("AccountsTable", "Mails gesichert / Server"),
            QT_TRANSLATE_NOOP("AccountsTable", "Größe"), QT_TRANSLATE_NOOP("AccountsTable", "Ordner"),
            QT_TRANSLATE_NOOP("AccountsTable", "Versuche"), QT_TRANSLATE_NOOP("AccountsTable", "Zuletzt"))

    picked = Signal(dict)  # Doppelklick auf ein Konto

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.table = QTableWidget(0, len(self.COLS))
        self.table.cellDoubleClicked.connect(lambda r, _c: self.picked.emit(self.rows[r]) if r < len(self.rows) else None)
        self.table.setHorizontalHeaderLabels([_tr(c) for c in self.COLS])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.table.setAlternatingRowColors(True)
        self.table.setWordWrap(False)
        self.table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        h = self.table.horizontalHeader()
        h.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        h.setStretchLastSection(True)
        self.summary = QLabel()
        self.summary.setObjectName("Muted")
        self.summary.setWordWrap(True)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.table)
        lay.addWidget(self.summary)
        self.rows: list[dict] = []

    def refresh(self, case: Case | None, current_email: str = "") -> list[dict]:
        from fed.gui import theme

        self.rows = accounts_overview(case) if case else []
        t = theme.tokens()
        self.table.setRowCount(len(self.rows))
        for r, o in enumerate(self.rows):
            sym, text, style = state_text(o)
            mails = f"{o['saved']:,} / {o['expected']:,}".replace(",", ".")
            folders = f"{o['folders_selected']} / {o['folders_total']}"
            vals = [o["evidence"], o["email"] + ("  ←" if o["email"] == current_email else ""), f"{sym}  {text}",
                    mails, _fmt_bytes(o["bytes"]), folders, str(o["attempts"]),
                    _short_time(o["last_utc"])]
            for c, v in enumerate(vals):
                it = QTableWidgetItem(v)
                if c == 2:
                    from PySide6.QtGui import QColor

                    it.setForeground(QColor(t[_COLOR[style]]))
                if c in (3, 4, 5, 6):
                    it.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                if c == 7 and o["last_utc"]:
                    it.setToolTip(tf.both(o["last_utc"]))
                self.table.setItem(r, c, it)
        self.table.resizeRowsToContents()
        hdr = self.table.horizontalHeader().height()
        self.table.setFixedHeight(hdr + sum(self.table.rowHeight(i) for i in range(len(self.rows))) + 4)
        done = sum(1 for o in self.rows if o["state"] == "done")
        mails = sum(o["saved"] for o in self.rows)
        self.summary.setText(QCoreApplication.translate("AccountsTable", "{0} Konten · {1} vollständig gesichert · {2} offen · {3} Mails · {4}").format(
            len(self.rows), done, len(self.rows) - done, f"{mails:,}".replace(",", "."),
            _fmt_bytes(sum(o["bytes"] for o in self.rows))))
        return self.rows


class _RailItem(ElidedLabel):
    clicked = Signal()

    def __init__(self, text: str, name: str) -> None:
        super().__init__(text, name, Qt.TextElideMode.ElideRight)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mouseReleaseEvent(self, e) -> None:
        if e.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mouseReleaseEvent(e)


class AccountsRail(QWidget):
    """Kurzliste in der linken Leiste: ein Eintrag je Konto mit Status-Symbol; Klick übernimmt das Konto."""

    picked = Signal(dict)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(0, 0, 0, 0)
        self.lay.setSpacing(2)
        self.title = QLabel(QCoreApplication.translate("AccountsTable", "KONTEN IM CASE"))
        self.title.setObjectName("RailTitle")
        self.lay.addWidget(self.title)
        self.items: list[ElidedLabel] = []
        self._last: list | None = None

    def refresh(self, case: Case | None) -> None:
        rows = accounts_overview(case) if case else []
        key = [(o["email"], o["state"], o["saved"], o["attempts"]) for o in rows]
        if key == self._last:
            return
        self._last = key
        for it in self.items:
            it.deleteLater()
        self.items = []
        self.setVisible(bool(rows))
        for o in rows:
            sym, text, style = state_text(o)
            lab = _RailItem(f"{sym}  {o['evidence']} · {o['email']}", style)
            lab.max_hint = 200
            lab.setToolTip(f"{o['email']}\n{o['evidence']} · {text}\n"
                           + QCoreApplication.translate("AccountsTable", "{0} von {1} Mails · {2} Versuche").format(
                               o["saved"], o["expected"], o["attempts"])
                           + "\n" + QCoreApplication.translate("AccountsTable", "Klicken: Konto übernehmen"))
            lab.clicked.connect(lambda o=o: self.picked.emit(o))
            restyle(lab, style)
            self.lay.addWidget(lab)
            self.items.append(lab)
