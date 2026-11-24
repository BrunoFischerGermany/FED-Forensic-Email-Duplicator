"""Verschlüsselte Kopie für die Weitergabe – Auswahl auf der Prüfseite vor dem Abschluss."""

from __future__ import annotations

from PySide6.QtCore import QTimer, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
                               QRadioButton, QVBoxLayout, QWidget)

from fed.core.encrypt import MIN_LENGTH, check_password, generate_password


class EncryptOptions(QWidget):
    """Checkbox „Verschlüsselte Kopie erstellen“ mit Passwort (erzeugt oder eigenes).

    :meth:`password` liefert ``None`` (keine Kopie) oder das Passwort; :meth:`valid` sagt, ob die Eingabe
    vollständig ist. Das Passwort wird nirgends gespeichert.
    """

    changed = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        intro = QLabel(self.tr(
            "Zusätzlich zum geprüften Original kann FED eine mit AES-256 verschlüsselte Kopie erstellen "
            "(Original-ZIP, Hash- und Zeitstempeldateien, log.csv, Berichte) – z. B. für USB-Stick oder Versand. "
            "Das Original bleibt unverändert. Das Passwort wird nirgends gespeichert; bitte getrennt übermitteln."))
        intro.setWordWrap(True)
        intro.setObjectName("Hint")
        self.enable = QCheckBox(self.tr("Verschlüsselte Kopie erstellen"))
        self.enable.setChecked(False)  # Standard: ohne Kopie; der Prüfer hakt bei Bedarf an
        self.gen = QRadioButton(self.tr("Sicheres Passwort erzeugen"))
        self.own = QRadioButton(self.tr("Eigenes Passwort"))
        self.gen.setChecked(True)
        grp = QButtonGroup(self)
        grp.addButton(self.gen)
        grp.addButton(self.own)
        self.generated = QLineEdit(generate_password())
        self.generated.setReadOnly(True)
        self.generated.setObjectName("Mono")  # Festbreitenschrift des Systems (Design)
        copy = QPushButton(self.tr("Kopieren"))
        copy.clicked.connect(lambda: QGuiApplication.clipboard().setText(self.generated.text()))
        regen = QPushButton(self.tr("Neu"))
        regen.clicked.connect(lambda: self.generated.setText(generate_password()))
        grow = QHBoxLayout()
        grow.setContentsMargins(24, 0, 0, 0)
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
        own_form.setContentsMargins(24, 0, 0, 0)
        self.own_box = QWidget()
        self.own_box.setLayout(own_form)
        self.hint = QLabel()
        self.hint.setObjectName("Hint")
        self.hint.setWordWrap(True)
        for w in (self.enable, self.gen, self.own):
            w.toggled.connect(self._update)
        self.options = QWidget()  # nur bei „Verschlüsselte Kopie erstellen“
        ol = QVBoxLayout(self.options)
        ol.setContentsMargins(0, 0, 0, 0)
        ol.addWidget(self.gen)
        ol.addLayout(grow)
        ol.addWidget(self.own)
        ol.addWidget(self.own_box)
        ol.addWidget(self.hint)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.enable)
        lay.addWidget(intro)
        lay.addWidget(self.options)
        self._ok = True
        self._update()

    def _current(self) -> str:
        return self.generated.text() if self.gen.isChecked() else self.pw1.text()

    def _update(self, *_) -> None:
        on = self.enable.isChecked()
        self.options.setVisible(on)
        self.own_box.setVisible(on and self.own.isChecked())
        self.generated.setEnabled(self.gen.isChecked())
        if on and self.own.isChecked() and not self.pw1.text():
            QTimer.singleShot(0, self.pw1.setFocus)
        ok, msg = True, ""
        if on and self.own.isChecked():
            if check_password(self.pw1.text()):
                ok, msg = False, self.tr("Passwort: mindestens {0} Zeichen, nicht zu einfach.").format(MIN_LENGTH)
            elif self.pw1.text() != self.pw2.text():
                ok, msg = False, self.tr("Die Passwörter stimmen nicht überein.")
        elif on:
            msg = self.tr("Passwort jetzt notieren oder kopieren – es wird nach dem Abschluss noch einmal angezeigt, "
                          "danach nicht mehr.")
        self.hint.setText(msg)
        self._ok = ok
        self.changed.emit()

    def valid(self) -> bool:
        return self._ok

    def password(self) -> str | None:
        return self._current() if self.enable.isChecked() else None

    def reset(self) -> None:
        """Für einen neuen Abschluss: keine Kopie, neues Passwort, Eingaben leeren."""
        self.enable.setChecked(False)
        self.gen.setChecked(True)
        self.generated.setText(generate_password())
        self.pw1.clear()
        self.pw2.clear()
        self._update()
