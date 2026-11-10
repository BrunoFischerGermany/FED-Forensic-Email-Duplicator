"""Ersteinrichtung: fragt die Grunddaten ab, wenn keine ``config.toml`` vorhanden ist
oder Agentur bzw. Examiner fehlen, und speichert sie neben dem Programm."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QHBoxLayout, QLabel,
                               QLineEdit, QMessageBox, QPlainTextEdit, QPushButton, QVBoxLayout)

from fed.core import config as cfgmod
from fed.core.languages import ui_languages
from fed.gui import i18n
from fed.gui import logo as logopick

MAX_LOGO = 2 * 1024 * 1024


def needs_setup(cfg: cfgmod.Config) -> bool:
    """True, wenn Grunddaten fehlen (keine Config-Datei oder Agentur/Examiner leer)."""
    return cfg.source_path is None or not cfg.agentur.name.strip() or not cfg.examiner_name.strip()


class SetupDialog(QDialog):
    """Abfrage von Agentur, Anschrift, Logo, Examiner, Sprache und Standard-Zielordner."""

    def __init__(self, cfg: cfgmod.Config, config_path: Path, parent=None) -> None:
        super().__init__(parent)
        self.cfg, self.path = cfg, config_path
        self._logo = cfg.agentur.logo
        self.setWindowTitle(self.tr("FED – Ersteinrichtung"))
        self.setMinimumWidth(520)

        intro = QLabel(self.tr("Bitte die Grunddaten erfassen. Sie erscheinen in jedem Bericht und werden in "
                               "{0} gespeichert. Änderungen sind später unter „Einstellungen“ möglich.")
                       .format(config_path.name))
        intro.setWordWrap(True)

        self.name = QLineEdit(cfg.agentur.name)
        self.name.setPlaceholderText(self.tr("z. B. Polizeipräsidium Musterstadt – K 34"))
        self.addr = QPlainTextEdit(cfg.agentur.adresse)
        self.addr.setFixedHeight(64)
        self.examiner = QLineEdit(cfg.examiner_name)
        self.lang = QComboBox()
        i18n.fill_combo(self.lang, ui_languages())
        self.lang.setCurrentIndex(max(0, self.lang.findData(cfg.programm.sprache)))
        self.target = QLineEdit(cfg.programm.zielpfad)
        self.target.setPlaceholderText(self.tr("Standard-Zielordner für Fälle (optional)"))
        browse = QPushButton(self.tr("Wählen…"))
        browse.clicked.connect(self._pick_target)
        trow = QHBoxLayout()
        trow.addWidget(self.target, 1)
        trow.addWidget(browse)

        self.logo_preview = QLabel()
        self.logo_preview.setFixedSize(160, 80)
        self.logo_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.logo_preview.setStyleSheet("background:#FFFFFF;border:1px dashed #C9CED8;border-radius:6px;color:#5B6270;")
        pick = QPushButton(self.tr("Logo wählen…"))
        pick.clicked.connect(self._pick_logo)
        rm = QPushButton(self.tr("Entfernen"))
        rm.clicked.connect(lambda: self._set_logo(""))
        lrow = QHBoxLayout()
        lrow.addWidget(self.logo_preview)
        lrow.addWidget(pick)
        lrow.addWidget(rm)
        lrow.addStretch(1)

        form = QFormLayout()
        form.addRow(self.tr("Agentur / Dienststelle *"), self.name)
        form.addRow(self.tr("Anschrift"), self.addr)
        form.addRow(self.tr("Logo (PNG/JPG/SVG, max. 2 MB)"), lrow)
        form.addRow(self.tr("Examiner *"), self.examiner)
        form.addRow(self.tr("Sprache"), self.lang)
        form.addRow(self.tr("Zielordner"), trow)

        self.buttons = QDialogButtonBox()
        self.ok = self.buttons.addButton(self.tr("Speichern"), QDialogButtonBox.ButtonRole.AcceptRole)
        self.buttons.addButton(self.tr("Später"), QDialogButtonBox.ButtonRole.RejectRole)
        self.buttons.accepted.connect(self.save)
        self.buttons.rejected.connect(self.reject)
        self.name.textChanged.connect(self._update)
        self.examiner.textChanged.connect(self._update)

        lay = QVBoxLayout(self)
        lay.addWidget(intro)
        lay.addLayout(form)
        lay.addWidget(self.buttons)
        self._show_logo()
        self._update()

    # ------------------------------------------------------------------ Logo / Ziel
    def _logo_abs(self) -> Path | None:
        if not self._logo:
            return None
        p = Path(self._logo)
        return p if p.is_absolute() else self.path.parent / p

    def _show_logo(self) -> None:
        p = self._logo_abs()
        if p and p.exists():
            self.logo_preview.setPixmap(logopick.pixmap(p).scaled(150, 70, Qt.AspectRatioMode.KeepAspectRatio,
                                                               Qt.TransformationMode.SmoothTransformation))
        else:
            self.logo_preview.clear()
            self.logo_preview.setText(self.tr("kein Logo"))

    def _set_logo(self, path: str) -> None:
        self._logo = path
        self._show_logo()

    def _pick_logo(self) -> None:
        f, _ = QFileDialog.getOpenFileName(self, self.tr("Logo wählen"), str(self.path.parent),
                                           logopick.file_filter())
        if not f:
            return
        if not logopick.accept(self, Path(f)):
            return
        try:
            f = str(Path(f).relative_to(self.path.parent))
        except ValueError:
            pass
        self._set_logo(f)

    def _pick_target(self) -> None:
        d = QFileDialog.getExistingDirectory(self, self.tr("Zielordner wählen"), self.target.text() or str(Path.home()))
        if d:
            self.target.setText(d)

    # ------------------------------------------------------------------ Speichern
    def _update(self) -> None:
        self.ok.setEnabled(bool(self.name.text().strip() and self.examiner.text().strip()))

    def save(self) -> None:
        c = self.cfg
        c.agentur.name = self.name.text().strip()
        c.agentur.adresse = self.addr.toPlainText().strip()
        c.agentur.logo = self._logo
        c.examiner_name = self.examiner.text().strip()
        c.programm.sprache = self.lang.currentData()
        c.programm.zielpfad = self.target.text().strip()
        try:
            cfgmod.validate(c)
            cfgmod.save(c, self.path)
            new = cfgmod.load(self.path)
        except (cfgmod.ConfigError, OSError) as e:
            QMessageBox.critical(self, "FED", self.tr("config.toml konnte nicht gespeichert werden: {0}").format(e))
            return
        c.raw_text, c.source_path = new.raw_text, new.source_path
        self.accept()
