"""Dialog: Apple-Mail-Ordner (oder Abzug/Image) offline in den geöffneten Fall importieren.

Die ermittelnde Person wählt einen Ordner; FED findet darin die Apple-Mail-Speicher
(``Library/Mail/V#``) und – falls vorhanden – ``Accounts4.sqlite`` für die maßgebliche Adresse.
Je Konto wird ein Asservat angelegt; Adresse und Asservatennummer sind vorbelegt und bestätigbar.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QDir, Qt
from PySide6.QtWidgets import (QAbstractItemView, QDialog, QFileDialog, QHBoxLayout, QHeaderView, QLabel,
                               QMessageBox, QProgressDialog, QPushButton, QTableWidget, QTableWidgetItem,
                               QVBoxLayout, QWidget)

from fed.core import applemail_import as imp
from fed.core.case import Case

_COLS = ("", "Asservat", "E-Mail-Adresse", "Quelle", "Ordner", "Mails", "unvollständig")


class AppleMailImportDialog(QDialog):
    def __init__(self, parent: QWidget | None, case: Case) -> None:
        super().__init__(parent)
        self.case = case
        self.choices: list[imp.AccountChoice] = []
        self.setWindowTitle(self.tr("Apple-Mail importieren"))
        self.resize(820, 440)
        lay = QVBoxLayout(self)

        lay.addWidget(QLabel(self.tr(
            "Ordner mit Apple-Mail-Daten wählen – ein „V#“-Ordner, ein „Mail“-Ordner oder die Wurzel "
            "eines Abzugs/Images. FED findet die Postfächer und (falls vorhanden) die Zuordnung der "
            "Adressen aus „Accounts4.sqlite“.")))
        top = QHBoxLayout()
        self.path_lbl = QLabel(self.tr("Kein Ordner gewählt"))
        top.addWidget(self.path_lbl, 1)
        self.this_mac = QPushButton(self.tr("Apple Mail dieses Macs"))
        self.this_mac.setToolTip(self.tr("Direkt das Postfach des angemeldeten Benutzers (~/Library/Mail) einlesen"))
        self.this_mac.clicked.connect(lambda: self.load(_this_mac_mail()))
        if _this_mac_mail() is None:
            self.this_mac.setVisible(False)
        browse = QPushButton(self.tr("Ordner wählen …"))
        browse.clicked.connect(self._choose)
        top.addWidget(self.this_mac)
        top.addWidget(browse)
        lay.addLayout(top)

        self.table = QTableWidget(0, len(_COLS))
        self.table.setHorizontalHeaderLabels([self.tr(c) for c in _COLS])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.AllEditTriggers)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        head = self.table.horizontalHeader()
        head.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        lay.addWidget(self.table, 1)

        btns = QHBoxLayout()
        self.info = QLabel("")
        cancel = QPushButton(self.tr("Abbrechen"))
        cancel.clicked.connect(self.reject)
        self.ok = QPushButton(self.tr("Importieren"))
        self.ok.setDefault(True)
        self.ok.setEnabled(False)
        self.ok.clicked.connect(self._run)
        btns.addWidget(self.info, 1)
        btns.addWidget(cancel)
        btns.addWidget(self.ok)
        lay.addLayout(btns)

    # ------------------------------------------------------------ Ordner wählen und einlesen
    def _choose(self) -> None:
        # macOS blendet ~/Library aus: Dialog gleich dort öffnen, wo die Mail-Daten liegen, und
        # versteckte Ordner zeigen (nicht-nativer Dialog), damit „Library“ sichtbar ist.
        start = _this_mac_mail() or Path.home()
        opts = QFileDialog.Option.DontUseNativeDialog | QFileDialog.Option.ShowDirsOnly
        dlg = QFileDialog(self, self.tr("Ordner mit Apple-Mail-Daten"), str(start))
        dlg.setFileMode(QFileDialog.FileMode.Directory)
        dlg.setOptions(opts)
        dlg.setFilter(dlg.filter() | QDir.Filter.Hidden)
        if dlg.exec() and dlg.selectedFiles():
            self.load(Path(dlg.selectedFiles()[0]))

    def load(self, root: Path) -> None:
        """Sucht Mail-Speicher und Accounts-DB unter ``root`` und füllt die Tabelle (auch für Tests)."""
        from fed.core import applemail

        self.path_lbl.setText(str(root))
        v10s = applemail.find_v10_dirs(root)
        if not v10s:
            self.table.setRowCount(0)
            self.ok.setEnabled(False)
            self.info.setText(self.tr("Hier wurde kein Apple-Mail-Speicher gefunden."))
            return
        # Accounts4.sqlite im gewählten Ordner – oder neben dem Mail-Speicher (~/Library/Accounts liegt
        # neben ~/Library/Mail, nicht darin).
        db = applemail.find_accounts_db(root)
        if db is None:
            db = next((d for v in v10s if (d := applemail.accounts_db_near(v))), None)
        self.choices = [c for v in v10s for c in imp.scan(v, db)]
        self._fill()
        src = self.tr("Adressen aus Accounts4.sqlite") if db else self.tr(
            "Ohne Accounts4.sqlite – Adressen aus den Kopfzeilen, bitte prüfen")
        self.info.setText(src)
        self.ok.setEnabled(any(c.messages for c in self.choices))

    def _fill(self) -> None:
        self.table.setRowCount(len(self.choices))
        for r, c in enumerate(self.choices):
            check = QTableWidgetItem()
            check.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
            check.setCheckState(Qt.CheckState.Checked if (c.address and c.messages) else Qt.CheckState.Unchecked)
            self.table.setItem(r, 0, check)
            self.table.setItem(r, 1, QTableWidgetItem(f"A-{r + 1:02d}"))
            self.table.setItem(r, 2, QTableWidgetItem(c.address))
            source = {"accounts_db": self.tr("Accounts-DB"), "headers": self.tr("Kopfzeilen (prüfen)")}.get(
                c.address_source, self.tr("unbekannt"))
            for col, text, editable in ((3, source, False), (4, str(c.folders), False),
                                        (5, str(c.messages), False), (6, str(_partials(c)), False)):
                item = QTableWidgetItem(text)
                if not editable:
                    item.setFlags(Qt.ItemFlag.ItemIsEnabled)
                self.table.setItem(r, col, item)

    # ------------------------------------------------------------ Import
    def selected(self) -> list[tuple[imp.AccountChoice, str, str]]:
        """Angehakte Zeilen als (Konto, Asservat, Adresse) – aus der (ggf. bearbeiteten) Tabelle."""
        out = []
        for r, c in enumerate(self.choices):
            if self.table.item(r, 0).checkState() == Qt.CheckState.Checked:
                out.append((c, self.table.item(r, 1).text().strip(), self.table.item(r, 2).text().strip()))
        return out

    def _run(self) -> None:
        rows = self.selected()
        if not rows:
            QMessageBox.information(self, "FED", self.tr("Bitte mindestens ein Konto anhaken."))
            return
        missing = [ev or c.uuid for c, ev, addr in rows if not ev or not addr]
        if missing:
            QMessageBox.warning(self, "FED", self.tr("Asservatennummer und Adresse dürfen nicht leer sein."))
            return
        self.results = self.run_import(rows)
        done = [r for r in self.results if r[2] is None]
        failed = [r for r in self.results if r[2] is not None]
        msg = self.tr("{n} Konten importiert.").format(n=len(done))
        if failed:
            msg += "\n\n" + self.tr("Fehlgeschlagen:") + "\n" + "\n".join(f"{a}: {e}" for a, _, e in failed)
        QMessageBox.information(self, "FED", msg)
        if done:
            self.accept()

    def run_import(self, rows) -> list[tuple[str, imp.ImportResult | None, str | None]]:
        """Führt den Import aus (je Konto) mit Fortschrittsanzeige; gibt (Adresse, Ergebnis, Fehler) je Konto."""
        prog = QProgressDialog(self.tr("Importiere …"), self.tr("Abbrechen"), 0, len(rows), self)
        prog.setWindowModality(Qt.WindowModality.WindowModal)
        out: list[tuple[str, imp.ImportResult | None, str | None]] = []
        for i, (choice, evidence, address) in enumerate(rows):
            if prog.wasCanceled():
                break
            prog.setValue(i)
            prog.setLabelText(self.tr("Importiere {addr} …").format(addr=address))
            try:
                res = imp.import_account(self.case, choice.account, address, evidence, host=choice.host,
                                         port=choice.port, security=choice.security, oauth=choice.oauth,
                                         name=choice.name)
                out.append((address, res, None))
            except Exception as e:  # z. B. Adresse doppelt im Fall (UNIQUE), unlesbare Datei
                out.append((address, None, str(e)))
        prog.setValue(len(rows))
        return out


def _partials(choice: imp.AccountChoice) -> int:
    return sum(1 for f in choice.account.folders for m in f.messages() if m.partial)


def _this_mac_mail() -> Path | None:
    """``~/Library/Mail`` des angemeldeten Benutzers, falls vorhanden (nur auf dem laufenden Mac)."""
    p = Path.home() / "Library" / "Mail"
    return p if p.is_dir() else None
