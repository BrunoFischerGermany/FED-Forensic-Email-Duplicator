"""Tab „Historie“: alles nur lesend aus der case.sqlite."""

from __future__ import annotations

import csv
import json

from PySide6.QtCore import QT_TRANSLATE_NOOP, QCoreApplication, QSortFilterProxyModel, Qt, QTimer
from PySide6.QtGui import QStandardItem, QStandardItemModel
from PySide6.QtWidgets import (QFileDialog, QHBoxLayout, QHeaderView, QLineEdit, QMessageBox, QPushButton,
                               QTableView, QTabWidget, QVBoxLayout, QWidget)

from fed.core import timefmt as tf
from fed.core.case import Case
from fed.core.hashing import hash_file
from fed.gui.widgets import Card, label, restyle, run_async

MAIL_COLS = [
    QT_TRANSLATE_NOOP("HistoryTab", "Ordner"),
    QT_TRANSLATE_NOOP("HistoryTab", "Eingangszeit (UTC)"),
    QT_TRANSLATE_NOOP("HistoryTab", "Eingangszeit (lokal)"),
    QT_TRANSLATE_NOOP("HistoryTab", "Absender"),
    QT_TRANSLATE_NOOP("HistoryTab", "Empfänger"),
    QT_TRANSLATE_NOOP("HistoryTab", "Anlagen"),
    QT_TRANSLATE_NOOP("HistoryTab", "Betreff"),
    QT_TRANSLATE_NOOP("HistoryTab", "Datei"),
    "SHA-256",
    QT_TRANSLATE_NOOP("HistoryTab", "Versuch"),
    QT_TRANSLATE_NOOP("HistoryTab", "Status"),
]
ATTEMPT_COLS = [
    QT_TRANSLATE_NOOP("HistoryTab", "Versuch"),
    QT_TRANSLATE_NOOP("HistoryTab", "Konto"),
    QT_TRANSLATE_NOOP("HistoryTab", "Modus"),
    QT_TRANSLATE_NOOP("HistoryTab", "Start (UTC)"),
    QT_TRANSLATE_NOOP("HistoryTab", "Start (lokal)"),
    QT_TRANSLATE_NOOP("HistoryTab", "Ende (UTC)"),
    QT_TRANSLATE_NOOP("HistoryTab", "Ende (lokal)"),
    QT_TRANSLATE_NOOP("HistoryTab", "Status"),
    QT_TRANSLATE_NOOP("HistoryTab", "Gesichert"),
    QT_TRANSLATE_NOOP("HistoryTab", "Ø MB/s"),
    QT_TRANSLATE_NOOP("HistoryTab", "Grund / Hinweis"),
]
LOG_COLS = [
    QT_TRANSLATE_NOOP("HistoryTab", "Zeit (UTC)"),
    QT_TRANSLATE_NOOP("HistoryTab", "Zeit (lokal)"),
    QT_TRANSLATE_NOOP("HistoryTab", "Versuch"),
    QT_TRANSLATE_NOOP("HistoryTab", "Stufe"),
    QT_TRANSLATE_NOOP("HistoryTab", "Ereignis"),
    QT_TRANSLATE_NOOP("HistoryTab", "Details"),
]
FOLDER_COLS = [
    QT_TRANSLATE_NOOP("HistoryTab", "Konto"),
    QT_TRANSLATE_NOOP("HistoryTab", "Ordner"),
    QT_TRANSLATE_NOOP("HistoryTab", "gewählt"),
    QT_TRANSLATE_NOOP("HistoryTab", "Server"),
    QT_TRANSLATE_NOOP("HistoryTab", "gesichert"),
    QT_TRANSLATE_NOOP("HistoryTab", "Differenz"),
    QT_TRANSLATE_NOOP("HistoryTab", "Vorher/Nachher"),
]


def _tr(text: str) -> str:
    return QCoreApplication.translate("HistoryTab", text)


def _recipient(to, cc, bcc, note, delivered) -> str:
    to, cc, bcc, dl = (json.loads(x or "[]") for x in (to, cc, bcc, delivered))
    if to:
        return to[0] + (f" +{len(to) - 1}" if len(to) > 1 else "") + (f" +{len(cc)} CC" if cc else "")
    if cc:
        return "CC: " + cc[0]
    if bcc:
        return QCoreApplication.translate("HistoryTab", "nur BCC: {0} Empfänger").format(len(bcc))
    if dl:
        return QCoreApplication.translate("HistoryTab", "kein To/CC · Delivered-To: {0}").format(dl[0])
    from fed.core.msg import describe

    return describe(note) or QCoreApplication.translate("HistoryTab", "kein Empfänger")


class Table(QWidget):
    def __init__(self, cols: list[str], search: bool = True) -> None:
        super().__init__()
        self.model = QStandardItemModel(0, len(cols))
        self.model.setHorizontalHeaderLabels([_tr(c) for c in cols])
        self.proxy = QSortFilterProxyModel()
        self.proxy.setSourceModel(self.model)
        self.proxy.setFilterKeyColumn(-1)
        self.proxy.setFilterCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.view = QTableView()
        self.view.setModel(self.proxy)
        self.view.setSortingEnabled(True)
        self.view.setAlternatingRowColors(True)
        self.view.verticalHeader().setVisible(False)
        self.view.setEditTriggers(QTableView.EditTrigger.NoEditTriggers)
        self.view.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self.view.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.view.horizontalHeader().setStretchLastSection(True)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 8, 0, 0)
        if search:
            self.search = QLineEdit()
            self.search.setPlaceholderText(self.tr("Suche: Betreff, Absender, Hash …"))
            self.search.textChanged.connect(self.proxy.setFilterFixedString)
            lay.addWidget(self.search)
        lay.addWidget(self.view)
        self.count = label("", "Hint")
        lay.addWidget(self.count)

    def fill(self, rows: list[list], ids: list | None = None) -> None:
        self.model.setRowCount(0)
        for n, r in enumerate(rows):
            items = []
            for v in r:
                it = QStandardItem("" if v is None else str(v))
                if isinstance(v, int | float):
                    it.setData(v, Qt.ItemDataRole.DisplayRole)
                items.append(it)
            if ids is not None and items:
                items[0].setData(ids[n], Qt.ItemDataRole.UserRole)
            self.model.appendRow(items)
        self.view.resizeColumnsToContents()
        self.count.setText(self.tr("{0} Einträge").format(len(rows)))

    def rows(self) -> list[list[str]]:
        return [[self.proxy.index(r, c).data() or "" for c in range(self.proxy.columnCount())]
                for r in range(self.proxy.rowCount())]


class HistoryTab(QWidget):
    def __init__(self, get_case) -> None:
        super().__init__()
        self.get_case = get_case
        lay = QVBoxLayout(self)
        lay.setContentsMargins(40, 28, 40, 20)
        lay.setSpacing(16)
        head = QHBoxLayout()
        head.addWidget(label(self.tr("Historie"), "H1"))
        head.addStretch(1)
        self.chain = label("", "Ok")
        head.addWidget(self.chain)
        for text, fn in ((self.tr("Hash-Kette prüfen"), self.verify_chain), (self.tr("Dateien nachhashen"), self.rehash),
                         (self.tr("Export CSV"), self.export)):
            b = QPushButton(text)
            b.clicked.connect(fn)
            head.addWidget(b)
        lay.addLayout(head)
        self.sub = label(self.tr("Nur lesend aus case.sqlite · jede Prüfung wird selbst protokolliert"), "Muted")
        lay.addWidget(self.sub)
        att_card = Card()
        self.attempts = Table(ATTEMPT_COLS, search=False)
        self.attempts.setMaximumHeight(180)
        att_card.add(self.attempts)
        lay.addWidget(att_card)
        self.tabs = QTabWidget()
        self.mails = Table(MAIL_COLS)
        self.mails.view.doubleClicked.connect(self.open_mail)
        self.mails.view.setToolTip(self.tr("Doppelklick: Mail ansehen (nur lesend)"))
        self.log = Table(LOG_COLS)
        self.folders = Table(FOLDER_COLS, search=False)
        from fed.gui.accounts import AccountsTable

        self.accounts = AccountsTable()
        acc_page = QWidget()
        al = QVBoxLayout(acc_page)
        al.addWidget(self.accounts)
        al.addStretch(1)
        self.tabs.addTab(acc_page, self.tr("Konten"))
        self.tabs.addTab(self.mails, self.tr("Gesicherte Mails"))
        self.tabs.addTab(self.log, self.tr("Log"))
        self.tabs.addTab(self.folders, self.tr("Ordner Soll/Ist + Vorher/Nachher"))
        lay.addWidget(self.tabs, 1)
        self.empty = label(self.tr("Kein Fall geöffnet – unter „Sicherung“ einen Fall anlegen oder öffnen."), "Muted")
        lay.addWidget(self.empty)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(3000)

    def open_mail(self, index) -> None:
        case = self.get_case()
        if case is None or not index.isValid():
            return
        src = self.mails.proxy.mapToSource(index)
        mid = self.mails.model.item(src.row(), 0).data(Qt.ItemDataRole.UserRole)
        if mid is None:
            return
        from fed.gui.viewer import MailViewer

        try:
            dlg = MailViewer(case, int(mid), self)
        except Exception as e:  # Datei fehlt o. Ä.
            QMessageBox.warning(self, "FED", self.tr("Mail kann nicht geöffnet werden:\n{0}").format(e))
            return
        self.timer.stop()
        try:
            dlg.exec()
        finally:
            self.timer.start(3000)

    def showEvent(self, e) -> None:
        self.refresh()
        super().showEvent(e)

    def refresh(self) -> None:
        if not self.isVisible():
            return
        case: Case | None = self.get_case()
        for w in (self.attempts, self.tabs):
            w.setVisible(case is not None)
        self.empty.setVisible(case is None)
        if case is None:
            return
        c = case.conn
        self.attempts.fill([[r[0], r[1], r[2], tf.utc_str(r[3])[:19], tf.local_str(r[3]),
                             tf.utc_str(r[4])[:19] if r[4] else "", tf.local_str(r[4]), r[5], r[6],
                             round((r[7] or 0) / 1e6, 1), (r[8] or "") + (f" → {r[9]}" if r[9] else "")]
                            for r in c.execute(
            "SELECT t.nr, a.email, t.mode, t.started_utc, t.ended_utc, t.status, "
            "(SELECT COUNT(*) FROM message m WHERE m.attempt_id = t.id), t.avg_bytes_s, t.reason, t.moved_to "
            "FROM attempt t JOIN account a ON a.id = t.account_id ORDER BY t.id")])
        if self.tabs.currentIndex() == 0:
            self.accounts.refresh(case)
        if self.tabs.currentWidget() is self.mails or not self.mails.model.rowCount():
            mrows = c.execute(
                "SELECT f.display_name, m.internaldate, m.msg_from, m.msg_to, m.msg_cc, m.msg_bcc, m.recipient_note, "
                "m.delivered_to, m.attachment_count, m.inline_count, m.subject, m.zip_path, m.sha256, t.nr, m.status, m.id "
                "FROM message m JOIN folder f ON f.id = m.folder_id JOIN attempt t ON t.id = m.attempt_id "
                "ORDER BY m.internaldate DESC").fetchall()
            self.mails.fill([[r[0], tf.utc_str(r[1])[:19], tf.local_str(r[1]), ", ".join(json.loads(r[2] or "[]")),
                              _recipient(*r[3:8]), str(r[8]) + (f" +{r[9]} inline" if r[9] else ""), r[10], r[11].split("/")[-1],
                              r[12], r[13], r[14]] for r in mrows], ids=[r[15] for r in mrows])
        if self.tabs.currentWidget() is self.log:
            self.log.fill([[tf.utc_str(r[0])[:19], tf.local_str(r[0]), r[1] or "", r[2], r[3], r[4][:300]] for r in c.execute(
                "SELECT l.ts_utc, t.nr, l.level, l.event, l.details FROM log l LEFT JOIN attempt t ON t.id = l.attempt_id "
                "ORDER BY l.id DESC")])
        if self.tabs.currentWidget() is self.folders:
            rows = []
            for r in c.execute("SELECT a.email, f.display_name, f.selected, f.count_server, f.count_saved, "
                               "f.status_before, f.status_after FROM folder f JOIN account a ON a.id = f.account_id "
                               "ORDER BY f.account_id, f.id"):
                ba = "–" if not r[5] else (self.tr("unverändert") if json.loads(r[5]) == json.loads(r[6] or "{}") else
                                           self.tr("GEÄNDERT {0} → {1}").format(r[5], r[6]))
                rows.append([r[0], r[1], self.tr("ja") if r[2] else self.tr("nein"), r[3] or 0, r[4] or 0,
                             (r[4] or 0) - (r[3] or 0) if r[2] else "", ba])
            self.folders.fill(rows)

    def verify_chain(self) -> None:
        case = self.get_case()
        if not case:
            return
        ok, bad = case.verify_log()
        n = case.conn.execute("SELECT COUNT(*) FROM log").fetchone()[0]
        case.log("INFO" if ok else "ERROR", "history_chain_check", {"ok": ok, "first_bad": bad})
        self.chain.setText(self.tr("✓ Log-Kette unverändert · {0} Einträge").format(n) if ok
                           else self.tr("✗ Log-Kette gebrochen ab Eintrag {0}").format(bad))
        restyle(self.chain, "Ok" if ok else "Error")

    def rehash(self) -> None:
        case = self.get_case()
        if not case:
            return
        self.chain.setText(self.tr("hashe nach …"))

        def work(cancel, report):
            if not case.work_dir.exists():  # Arbeitsordner gelöscht -> gegen die ZIPs prüfen
                from fed.core.finalize import verify_case_zips

                results = verify_case_zips(case, cancel)
                if not results:
                    raise RuntimeError(self.tr("Weder Arbeitsordner noch ZIP vorhanden"))
                problems = [p for v in results for p in v.problems]
                case.log("INFO" if not problems else "ERROR", "history_rehash_zip",
                         {"zips": [v.path.name for v in results], "problems": problems})
                return sum(v.files for v in results), problems
            bad, n = [], 0
            for zip_path, sha in case.conn.execute("SELECT zip_path, sha256 FROM message WHERE status = 'valid'"):
                cancel.raise_if_cancelled()
                p = case.work_dir / zip_path
                n += 1
                if not p.exists() or hash_file(p)["sha256"] != sha:
                    bad.append(zip_path)
            case.log("INFO" if not bad else "ERROR", "history_rehash", {"checked": n, "invalid": bad})
            return n, bad

        def done(res):
            n, bad = res
            self.chain.setText(self.tr("✓ {0} Dateien identisch").format(n) if not bad
                               else self.tr("✗ {0} von {1} Dateien abweichend oder fehlend").format(len(bad), n))
            restyle(self.chain, "Ok" if not bad else "Error")

        run_async(work, done, lambda e, tb: QMessageBox.critical(self, "FED", e), parent=self)

    def export(self) -> None:
        t = self.tabs.currentWidget()
        path, _ = QFileDialog.getSaveFileName(self, self.tr("Ansicht als CSV speichern"), "historie.csv", "CSV (*.csv)")
        if not path:
            return
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f, delimiter=";")
            w.writerow([t.model.headerData(i, Qt.Orientation.Horizontal) for i in range(t.model.columnCount())])
            w.writerows(t.rows())
        case = self.get_case()
        if case:
            case.log("INFO", "history_export", {"file": path, "rows": t.proxy.rowCount()})
