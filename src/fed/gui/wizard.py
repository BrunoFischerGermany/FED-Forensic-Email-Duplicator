"""Assistent mit den Schritten 1–7 des Konzepts (Grundgerüst)."""

from __future__ import annotations

from PySide6.QtWidgets import QLabel, QLineEdit, QVBoxLayout, QWizard, QWizardPage

from fed.core.config import Config


def _placeholder(title: str, text: str) -> QWizardPage:
    page = QWizardPage()
    page.setTitle(title)
    layout = QVBoxLayout(page)
    layout.addWidget(QLabel(text))
    return page


class AccountPage(QWizardPage):
    """Schritt 3: Adresse, Autoerkennung, Passwort (ein-/ausblendbar)."""

    def __init__(self) -> None:
        super().__init__()
        self.setTitle(self.tr("3 Konto"))
        layout = QVBoxLayout(self)
        self.email = QLineEdit(placeholderText=self.tr("E-Mail-Adresse"))
        self.password = QLineEdit(placeholderText=self.tr("Passwort"))
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        toggle = self.password.addAction(
            self.style().standardIcon(self.style().StandardPixmap.SP_DialogApplyButton),
            QLineEdit.ActionPosition.TrailingPosition,
        )
        toggle.setCheckable(True)
        toggle.setToolTip(self.tr("Passwort ein-/ausblenden"))
        toggle.toggled.connect(
            lambda on: self.password.setEchoMode(
                QLineEdit.EchoMode.Normal if on else QLineEdit.EchoMode.Password
            )
        )
        self._toggle = toggle
        layout.addWidget(self.email)
        layout.addWidget(self.password)

    def cleanupPage(self) -> None:
        self._toggle.setChecked(False)

    def validatePage(self) -> bool:
        self._toggle.setChecked(False)  # beim Verlassen wieder ausblenden
        return True


class AcquisitionWizard(QWizard):
    def __init__(self, cfg: Config) -> None:
        super().__init__()
        self.cfg = cfg
        self.addPage(_placeholder(self.tr("1 Case anlegen"), self.tr("Casenumber, Evidence, Examiner, Notes")))
        self.addPage(_placeholder(self.tr("2 Systemprüfung"), self.tr("Benutzer, Zeit, Internet, Datenträger, Speicher")))
        self.addPage(AccountPage())
        self.addPage(_placeholder(self.tr("4 Postfach scannen"), self.tr("Ordner, Anzahl, Größe")))
        self.addPage(_placeholder(self.tr("5 Ordner wählen"), self.tr("Baum mit Häkchen, Zeitraum-Filter")))
        self.addPage(_placeholder(self.tr("6 Sicherung"), self.tr("Fortschritt, Abbrechen")))
        self.addPage(_placeholder(self.tr("7 Bericht"), self.tr("ZIP, Hashes, TXT/PDF")))
