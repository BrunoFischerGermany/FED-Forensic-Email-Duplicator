"""Übersicht der Konten im Case: was ist schon gesichert, was fehlt noch."""

from __future__ import annotations

from PySide6.QtCore import QT_TRANSLATE_NOOP, QCoreApplication, Qt, Signal
from PySide6.QtWidgets import (QAbstractItemView, QDialog, QDialogButtonBox, QFrame, QHeaderView, QLabel,
                               QProgressBar, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from fed.core import timefmt as tf
from fed.core.acquisition import accounts_overview, set_account_notes
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


NOTE_COL = 8


class NoteDialog(QDialog):
    """Notiz zu einem Konto bearbeiten (mehrzeilig; Änderung wird mit altem und neuem Text protokolliert)."""

    def __init__(self, evidence: str, email: str, text: str, parent=None, html_value: str | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(QCoreApplication.translate("AccountsTable", "Notiz zum Konto"))
        self.setMinimumSize(520, 300)
        head = QLabel(f"{evidence} · {email}")
        head.setObjectName("H2")
        from fed.gui.richedit import RichNoteEdit

        self.edit = RichNoteEdit()
        self.edit.set_value(text, html_value)
        self.edit.setPlaceholderText(QCoreApplication.translate(
            "AccountsTable", "z. B. Anlass, Einwilligung, Besonderheiten beim Zugriff"))
        hint = QLabel(QCoreApplication.translate(
            "AccountsTable", "Erscheint im Bericht. Jede Änderung wird mit altem und neuem Text protokolliert."))
        hint.setObjectName("Hint")
        hint.setWordWrap(True)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addWidget(head)
        lay.addWidget(self.edit, 1)
        lay.addWidget(hint)
        lay.addWidget(buttons)
        self.edit.setFocus()
        cur = self.edit.edit.textCursor()
        cur.movePosition(cur.MoveOperation.End)
        self.edit.edit.setTextCursor(cur)

    def text(self) -> str:
        return self.edit.toPlainText()

    def html(self) -> str:
        return self.edit.html()


class AccountsTable(QWidget):
    """Tabelle aller Konten im Case (für die Seiten „Konto“ und „Sicherung“ und die Historie).
    Die Notiz ist per Klick bearbeitbar, solange der Case offen ist."""

    COLS = (QT_TRANSLATE_NOOP("AccountsTable", "Evidence"), QT_TRANSLATE_NOOP("AccountsTable", "Konto"),
            QT_TRANSLATE_NOOP("AccountsTable", "Status"), QT_TRANSLATE_NOOP("AccountsTable", "Mails gesichert / Server"),
            QT_TRANSLATE_NOOP("AccountsTable", "Größe"), QT_TRANSLATE_NOOP("AccountsTable", "Ordner"),
            QT_TRANSLATE_NOOP("AccountsTable", "Versuche"), QT_TRANSLATE_NOOP("AccountsTable", "Zuletzt"),
            QT_TRANSLATE_NOOP("AccountsTable", "Notiz"))

    picked = Signal(dict)  # Doppelklick auf ein Konto
    notes_changed = Signal(int, str)  # account_id, neue Notiz

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.case: Case | None = None
        self._current = ""
        self.table = QTableWidget(0, len(self.COLS))
        self.table.cellClicked.connect(self._clicked)
        self.table.cellDoubleClicked.connect(self._double_clicked)
        self.table.setHorizontalHeaderLabels([_tr(c) for c in self.COLS])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.table.setAlternatingRowColors(True)
        self.table.setWordWrap(True)  # nur die Notiz ist mehrzeilig
        self.table.setStyleSheet("QTableWidget::item { padding: 6px 10px; }")
        self.table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        h = self.table.horizontalHeader()
        h.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        h.setSectionResizeMode(NOTE_COL, QHeaderView.ResizeMode.Stretch)
        h.setMinimumSectionSize(60)
        self.table.horizontalHeader().sectionResized.connect(lambda *_: self._fit_height())
        self.summary = QLabel()
        self.summary.setObjectName("Muted")
        self.summary.setWordWrap(True)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.table)
        lay.addWidget(self.summary)
        self.rows: list[dict] = []

    @property
    def editable(self) -> bool:
        return self.case is not None and not self.case.closed

    def _clicked(self, r: int, c: int) -> None:
        if c == NOTE_COL and r < len(self.rows) and self.editable:
            self.edit_note(r)

    def _double_clicked(self, r: int, c: int) -> None:
        if c != NOTE_COL and r < len(self.rows):
            self.picked.emit(self.rows[r])

    def edit_note(self, r: int) -> bool:
        o = self.rows[r]
        dlg = NoteDialog(o["evidence"], o["email"], o["notes"], self, o.get("notes_html"))
        if not dlg.exec():
            return False
        if set_account_notes(self.case, o["id"], dlg.text(), dlg.html() or None):
            self.notes_changed.emit(o["id"], dlg.text().strip())
        self.refresh(self.case, self._current)
        return True

    def _fit_height(self) -> None:
        self.table.resizeRowsToContents()
        hdr = self.table.horizontalHeader().height()
        self.table.setFixedHeight(hdr + sum(self.table.rowHeight(i) for i in range(self.table.rowCount())) + 4)

    def refresh(self, case: Case | None, current_email: str = "") -> list[dict]:
        from PySide6.QtGui import QColor

        from fed.gui import theme

        self.case, self._current = case, current_email
        self.rows = accounts_overview(case) if case else []
        t = theme.tokens()
        self.table.setRowCount(len(self.rows))
        for r, o in enumerate(self.rows):
            sym, text, style = state_text(o)
            mails = f"{o['saved']:,} / {o['expected']:,}".replace(",", ".")
            folders = f"{o['folders_selected']} / {o['folders_total']}"
            vals = [o["evidence"], o["email"] + ("  ←" if o["email"] == current_email else ""), f"{sym}  {text}",
                    mails, _fmt_bytes(o["bytes"]), folders, str(o["attempts"]),
                    _short_time(o["last_utc"]), o["notes"].strip()]
            for c, v in enumerate(vals):
                it = QTableWidgetItem(v)
                if c == 2:
                    it.setForeground(QColor(t[_COLOR[style]]))
                if c == NOTE_COL:
                    it.setTextAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
                    if self.editable:
                        if not v:
                            it.setText(QCoreApplication.translate("AccountsTable", "✎ Notiz hinzufügen"))
                            it.setForeground(QColor(t["muted"]))
                        it.setToolTip(QCoreApplication.translate("AccountsTable", "Klicken: Notiz bearbeiten"))
                if c in (3, 4, 5, 6):
                    it.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                if c == 7 and o["last_utc"]:
                    it.setToolTip(tf.both(o["last_utc"]))
                self.table.setItem(r, c, it)
        self._fit_height()
        done = sum(1 for o in self.rows if o["state"] == "done")
        mails = sum(o["saved"] for o in self.rows)
        self.summary.setText(QCoreApplication.translate("AccountsTable", "{0} Konten · {1} vollständig gesichert · {2} offen · {3} Mails · {4}").format(
            len(self.rows), done, len(self.rows) - done, f"{mails:,}".replace(",", "."),
            _fmt_bytes(sum(o["bytes"] for o in self.rows))))
        return self.rows


class _RailItem(QFrame):
    """Ein Konto in der linken Leiste; bei laufender Sicherung mit Ladebalken und Tempo."""

    clicked = Signal()

    def __init__(self, text: str, name: str) -> None:
        super().__init__()
        self.setObjectName("RailItem")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 3, 4, 3)
        lay.setSpacing(3)
        self.label = ElidedLabel(text, name, Qt.TextElideMode.ElideRight)
        self.label.max_hint = 200
        self.bar = QProgressBar()
        self.bar.setFixedHeight(6)
        self.bar.setTextVisible(False)
        self.info = ElidedLabel("", "Hint", Qt.TextElideMode.ElideRight)
        self.info.max_hint = 200
        lay.addWidget(self.label)
        lay.addWidget(self.bar)
        lay.addWidget(self.info)
        self.show_progress(None)

    def full_text(self) -> str:
        return self.label.full_text()

    def show_progress(self, job) -> None:
        """Ladebalken nur, solange eine Sicherung dieses Kontos läuft."""
        on = job is not None and job.running
        self.bar.setVisible(on)
        self.info.setVisible(on)
        if not on:
            return
        p = job.last
        self.bar.setValue(job.percent)
        if job.cancelling:
            self.info.setText(QCoreApplication.translate("AccountsTable", "wird abgebrochen …"))
        elif p is None:
            self.info.setText(QCoreApplication.translate("AccountsTable", "wird vorbereitet …"))
        elif p.phase == "auth_check":
            self.info.setText(QCoreApplication.translate("AccountsTable", "Echtheitsprüfung …"))
        else:
            bps, _ = p.speed()
            self.info.setText(QCoreApplication.translate("AccountsTable", "{0} % · {1} von {2} Mails · {3} MB/s").format(
                job.percent, f"{p.mails_done:,}".replace(",", "."), f"{p.mails_total:,}".replace(",", "."),
                f"{bps / 1e6:.1f}"))

    def mouseReleaseEvent(self, e) -> None:
        if e.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mouseReleaseEvent(e)


class AccountsRail(QWidget):
    """Kurzliste in der linken Leiste: ein Eintrag je Konto mit Status-Symbol; Klick übernimmt das Konto.
    Laufende Sicherungen zeigen einen Ladebalken – Klick öffnet ihre Live-Ansicht."""

    picked = Signal(dict)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(0, 0, 0, 0)
        self.lay.setSpacing(2)
        self.title = QLabel(QCoreApplication.translate("AccountsTable", "KONTEN IM CASE"))
        self.title.setObjectName("RailTitle")
        self.lay.addWidget(self.title)
        self.items: list[_RailItem] = []
        self.by_id: dict[int, _RailItem] = {}
        self._last: list | None = None
        self.jobs = None  # JobManager

    def refresh(self, case: Case | None) -> None:
        rows = accounts_overview(case) if case else []
        jobs = self.jobs
        live = {j.account_id for j in jobs.running()} if jobs else set()
        key = [(o["email"], o["state"], o["saved"], o["attempts"], o["id"] in live) for o in rows]
        if key == self._last:
            self.update_progress()
            return
        self._last = key
        for it in self.items:
            it.deleteLater()
        self.items, self.by_id = [], {}
        self.setVisible(bool(rows))
        for o in rows:
            running = o["id"] in live
            sym, text, style = state_text(o)
            if running:
                sym, text, style = "⟳", _tr("Sicherung läuft"), "Accent"
            item = _RailItem(f"{sym}  {o['evidence']} · {o['email']}", style)
            hint = (QCoreApplication.translate("AccountsTable", "Klicken: Fortschritt ansehen") if running else
                    QCoreApplication.translate("AccountsTable", "Klicken: Konto übernehmen"))
            item.setToolTip(f"{o['email']}\n{o['evidence']} · {text}\n"
                            + QCoreApplication.translate("AccountsTable", "{0} von {1} Mails · {2} Versuche").format(
                                o["saved"], o["expected"], o["attempts"]) + "\n" + hint)
            item.clicked.connect(lambda o=o: self.picked.emit(o))
            restyle(item.label, style)
            self.lay.addWidget(item)
            self.items.append(item)
            self.by_id[o["id"]] = item
        self.update_progress()

    def update_progress(self, job=None) -> None:
        """Nur die Ladebalken aktualisieren (bei jedem Fortschritts-Signal, ohne Neuaufbau)."""
        if self.jobs is None:
            return
        targets = [job] if job is not None else list(self.jobs.jobs.values())
        for j in targets:
            item = self.by_id.get(j.account_id)
            if item is not None:
                item.show_progress(j)
