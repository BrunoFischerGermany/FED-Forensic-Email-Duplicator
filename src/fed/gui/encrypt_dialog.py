"""Abfrage beim Abschluss: verschlüsselte Kopie für die Weitergabe erstellen?"""

from __future__ import annotations

from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QLabel,
                               QLineEdit, QPushButton, QRadioButton, QVBoxLayout)

from fed.core.encrypt import MIN_LENGTH, check_password, generate_password


class EncryptDialog(QDialog):
    """Ergebnis: :attr:`password` (``None`` = keine verschlüsselte Kopie)."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.password: str | None = None
        self.setWindowTitle(self.tr("Verschlüsselte Kopie für die Weitergabe"))
        self.setMinimumWidth(520)
        intro = QLabel(self.tr(
            "Zusätzlich zum geprüften Original kann FED eine mit AES-256 verschlüsselte Kopie erstellen "
            "(Original-ZIP, Hash- und Zeitstempeldateien, log.csv, Berichte) – z. B. für USB-Stick oder Versand. "
            "Das Original bleibt unverändert. Das Passwort wird nirgends gespeichert; bitte getrennt übermitteln."))
        intro.setWordWrap(True)
        self.enable = QCheckBox(self.tr("Verschlüsselte Kopie erstellen"))
        self.enable.setChecked(True)
        self.gen = QRadioButton(self.tr("Sicheres Passwort erzeugen"))
        self.own = QRadioButton(self.tr("Eigenes Passwort"))
        self.gen.setChecked(True)
        grp = QButtonGroup(self)
        grp.addButton(self.gen)
        grp.addButton(self.own)
        self.generated = QLineEdit(generate_password())
        self.generated.setReadOnly(True)
        self.generated.setStyleSheet("font-family: monospace;")
        copy = QPushButton(self.tr("Kopieren"))
        copy.clicked.connect(lambda: QGuiApplication.clipboard().setText(self.generated.text()))
        regen = QPushButton(self.tr("Neu"))
        regen.clicked.connect(lambda: self.generated.setText(generate_password()))
        grow = QHBoxLayout()
        grow.addWidget(self.generated, 1)
        grow.addWidget(copy)
        grow.addWidget(regen)
        self.pw1 = QLineEdit()
        self.pw2 = QLineEdit()
        for e in (self.pw1, self.pw2):
            e.setEchoMode(QLineEdit.EchoMode.Password)
            e.textChanged.connect(self._update)
        own_form = QFormLayout()
        own_form.addRow(self.tr("Passwort"), self.pw1)
        own_form.addRow(self.tr("Wiederholen"), self.pw2)
        self.hint = QLabel()
        self.hint.setObjectName("Hint")
        self.hint.setWordWrap(True)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.accepted.connect(self._accept)
        self.buttons.rejected.connect(self.reject)
        for w in (self.enable, self.gen, self.own):
            w.toggled.connect(self._update)
        lay = QVBoxLayout(self)
        lay.addWidget(intro)
        lay.addWidget(self.enable)
        lay.addWidget(self.gen)
        lay.addLayout(grow)
        lay.addWidget(self.own)
        lay.addLayout(own_form)
        lay.addWidget(self.hint)
        lay.addWidget(self.buttons)
        self._update()

    def _current(self) -> str:
        return self.generated.text() if self.gen.isChecked() else self.pw1.text()

    def _update(self, *_) -> None:
        on = self.enable.isChecked()
        for w in (self.gen, self.own, self.generated):
            w.setEnabled(on)
        for w in (self.pw1, self.pw2):
            w.setEnabled(on and self.own.isChecked())
        ok, msg = True, ""
        if on and self.own.isChecked():
            problem = check_password(self.pw1.text())
            if problem:
                ok, msg = False, self.tr("Passwort: mindestens {0} Zeichen, nicht zu einfach.").format(MIN_LENGTH)
            elif self.pw1.text() != self.pw2.text():
                ok, msg = False, self.tr("Die Passwörter stimmen nicht überein.")
        elif on:
            msg = self.tr("Passwort jetzt notieren oder kopieren – es wird nach dem Abschluss noch einmal angezeigt, "
                          "danach nicht mehr.")
        self.hint.setText(msg)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(ok)

    def _accept(self) -> None:
        self.password = self._current() if self.enable.isChecked() else None
        self.accept()
