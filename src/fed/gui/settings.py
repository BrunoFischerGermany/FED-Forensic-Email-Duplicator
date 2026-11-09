"""Tab „Einstellungen“: bearbeitet die config.toml neben dem Programm."""

from __future__ import annotations

import hashlib
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QComboBox, QFileDialog, QFormLayout, QGridLayout,
                               QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton, QRadioButton, QScrollArea,
                               QSpinBox, QVBoxLayout, QWidget)

from fed import APP_NAME, __version__
from fed.core import config as cfgmod
from fed.core.languages import report_languages, ui_languages
from fed.gui import i18n
from fed.gui.help import show_help
from fed.gui.widgets import Card, ElidedLabel, PasswordField, Segmented, label


class SettingsTab(QWidget):
    saved = Signal(object)
    theme_preview = Signal(str)

    def __init__(self, cfg: cfgmod.Config, config_path: Path) -> None:
        super().__init__()
        self.cfg = cfg
        self.path = config_path
        inner = QWidget()
        inner.setObjectName("Page")
        lay = QVBoxLayout(inner)
        lay.setContentsMargins(40, 28, 40, 24)
        lay.setSpacing(20)
        head = QHBoxLayout()
        head.addWidget(label(self.tr("Einstellungen"), "H1"))
        head.addStretch(1)
        self.discard = QPushButton(self.tr("Verwerfen"))
        self.save_btn = QPushButton(self.tr("Speichern"))
        self.save_btn.setObjectName("Primary")
        self.discard.clicked.connect(self.load)
        self.save_btn.clicked.connect(self.save)
        head.addWidget(self.discard)
        head.addWidget(self.save_btn)
        lay.addLayout(head)
        lay.addWidget(ElidedLabel(self.tr("Gespeichert in {0} · gilt für neue Cases, bestehende behalten ihre Kopie")
                                  .format(config_path), "Muted"))
        grid = QGridLayout()
        grid.setSpacing(20)

        a = Card(self.tr("Agentur und Logo"))
        f = QFormLayout()
        self.ag_name, self.ag_addr = QLineEdit(), QLineEdit()
        f.addRow(self.tr("Name"), self.ag_name)
        f.addRow(self.tr("Adresse"), self.ag_addr)
        a.add(f)
        lr = QHBoxLayout()
        self.logo_preview = QLabel()
        self.logo_preview.setFixedSize(160, 80)
        self.logo_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.logo_preview.setStyleSheet("background:#FFFFFF;border:1px dashed #C9CED8;border-radius:6px;color:#5B6270;")
        lr.addWidget(self.logo_preview)
        lb = QVBoxLayout()
        pick = QPushButton(self.tr("Logo wählen …"))
        pick.clicked.connect(self.pick_logo)
        rm = QPushButton(self.tr("Entfernen"))
        rm.setObjectName("Danger")
        rm.clicked.connect(lambda: self._set_logo(""))
        lb.addWidget(pick)
        lb.addWidget(rm)
        lr.addLayout(lb)
        lr.addStretch(1)
        a.add(lr)
        self.logo_hint = label("", "Hint", wrap=True)
        a.add(self.logo_hint)
        grid.addWidget(a, 0, 0)

        c = Card(self.tr("Examiner und Case"))
        f = QFormLayout()
        self.examiner, self.pattern, self.target = QLineEdit(), QLineEdit(), QLineEdit()
        self.target.setPlaceholderText(self.tr("leer = Laufwerk des Programms"))
        f.addRow(self.tr("Examiner (Vorbelegung)"), self.examiner)
        f.addRow(self.tr("Muster Casenumber"), self.pattern)
        f.addRow(self.tr("Standard-Zielordner"), self.target)
        c.add(f)
        grid.addWidget(c, 0, 1)

        d = Card(self.tr("Darstellung"))
        self.design = Segmented([("system", self.tr("Wie System")), ("hell", self.tr("Hell")), ("dunkel", self.tr("Dunkel"))])
        self.design.changed.connect(self.theme_preview.emit)
        d.add(self.design)
        f = QFormLayout()
        self.lang, self.rlang = QComboBox(), QComboBox()
        i18n.fill_combo(self.lang, ui_languages())
        self.rlang.addItem(self.tr("wie Oberfläche"), "")
        i18n.fill_combo(self.rlang, report_languages())
        self.mlist = QComboBox()
        self.mlist.addItem(self.tr("automatisch – Liste nur bis zur Grenze"), "auto")
        self.mlist.addItem(self.tr("Ordnerstruktur + Mail-Liste"), "liste")
        self.mlist.addItem(self.tr("nur Ordnerstruktur mit Anzahl"), "ordner")
        self.mlist_max = QSpinBox()
        self.mlist_max.setRange(10, 1_000_000)
        self.mlist_max.setSingleStep(100)
        self.mlist_max.setSuffix(self.tr(" Mails"))
        self.mlist.currentIndexChanged.connect(lambda _: self.mlist_max.setEnabled(self.mlist.currentData() == "auto"))
        f.addRow(self.tr("Sprache Oberfläche"), self.lang)
        f.addRow(self.tr("Sprache Bericht"), self.rlang)
        f.addRow(self.tr("Inhalt Bericht"), self.mlist)
        f.addRow(self.tr("Grenze"), self.mlist_max)
        d.add(f)
        grid.addWidget(d, 1, 0)

        s = Card(self.tr("Sicherung"))
        hr = QHBoxLayout()
        hr.addWidget(label(self.tr("Hashes (SHA-256 immer):")))
        self.h_md5, self.h_sha1, self.h_sha512 = QCheckBox("MD5"), QCheckBox("SHA-1"), QCheckBox("SHA-512")
        for x in (self.h_md5, self.h_sha1, self.h_sha512):
            hr.addWidget(x)
        hr.addStretch(1)
        s.add(hr)
        self.zt_int = QRadioButton(self.tr("Zeitstempel = Eingangszeit auf dem Server (INTERNALDATE)"))
        self.zt_date = QRadioButton(self.tr("Zeitstempel = Date-Header der Mail"))
        g = QButtonGroup(self)
        g.addButton(self.zt_int)
        g.addButton(self.zt_date)
        s.add(self.zt_int)
        s.add(self.zt_date)
        f = QFormLayout()
        self.timeout, self.chunk = QSpinBox(), QSpinBox()
        self.timeout.setRange(5, 600)
        self.timeout.setSuffix(" s")
        self.chunk.setRange(1, 200)
        self.chunk.setSuffix(" MB")
        self.conns = QSpinBox()
        self.conns.setRange(1, 10)
        f.addRow(self.tr("Gleichzeitige Verbindungen"), self.conns)
        f.addRow(self.tr("Netzwerk-Timeout"), self.timeout)
        f.addRow(self.tr("Stückweise laden ab"), self.chunk)
        s.add(f)
        self.workdel = QComboBox()
        self.workdel.addItem(self.tr("beim Abschluss fragen"), "fragen")
        self.workdel.addItem(self.tr("immer löschen (nach verifiziertem ZIP)"), "ja")
        self.workdel.addItem(self.tr("immer behalten"), "nein")
        self.zipmode = QComboBox()
        self.zipmode.addItem(self.tr("beim 2. Konto fragen"), "fragen")
        self.zipmode.addItem(self.tr("immer ein ZIP für alle Konten"), "gesamt")
        self.zipmode.addItem(self.tr("immer ein ZIP je Konto"), "je_konto")
        wl = QFormLayout()
        wl.addRow(self.tr("ZIP bei mehreren Konten"), self.zipmode)
        self.encmode = QComboBox()
        self.encmode.addItem(self.tr("beim Abschluss fragen"), "fragen")
        self.encmode.addItem(self.tr("aus"), "aus")
        wl.addRow(self.tr("Verschlüsselte Weitergabe-Kopie"), self.encmode)
        wl.addRow(self.tr("Arbeitsordner nach Abschluss"), self.workdel)
        s.add(wl)
        self.awake = QCheckBox(self.tr("Rechner während Sicherung und Abschluss nicht sperren / nicht schlafen lassen"))
        s.add(self.awake)
        self.dkim = QCheckBox(self.tr("DKIM-Signaturen bei der Sicherung prüfen und DNS-Schlüssel aufbewahren"))
        s.add(self.dkim)
        self.spaces = QCheckBox(self.tr("Leerzeichen in Case-Ordner- und Dateinamen durch „_“ ersetzen "
                                        "(gilt für neue Cases)"))
        s.add(self.spaces)
        grid.addWidget(s, 1, 1)

        n = Card(self.tr("Netzwerk und Zeit"))
        self.ntp = QCheckBox(self.tr("Zeitabgleich (NTP) beim Start"))
        n.add(self.ntp)
        f = QFormLayout()
        self.ntp_server, self.proxy, self.tsa = QLineEdit(), QLineEdit(), QLineEdit()
        self.proxy.setPlaceholderText("http://proxy.local:8080")
        self.tsa.setPlaceholderText(self.tr("leer = aus"))
        f.addRow(self.tr("NTP-Server"), self.ntp_server)
        f.addRow(self.tr("Proxy"), self.proxy)
        f.addRow(self.tr("Zeitstempeldienst (RFC 3161)"), self.tsa)
        n.add(f)
        grid.addWidget(n, 2, 0)

        o = Card(self.tr("Google / Microsoft 365"))
        f = QFormLayout()
        self.g_id, self.g_secret, self.m_id, self.m_tenant = QLineEdit(), PasswordField(), QLineEdit(), QLineEdit()
        f.addRow(self.tr("Google Client-ID"), self.g_id)
        f.addRow(self.tr("Google Client-Secret"), self.g_secret)
        f.addRow(self.tr("Microsoft Client-ID"), self.m_id)
        f.addRow(self.tr("Microsoft Tenant"), self.m_tenant)
        o.add(f)
        o.add(label(self.tr("Keine Passwörter oder Tokens in der Config – nur die App-Kennungen."), "Hint", wrap=True))
        hb = QHBoxLayout()
        self.help_ms = QPushButton(self.tr("Anleitung Microsoft"))
        self.help_google = QPushButton(self.tr("Anleitung Google"))
        self.help_ms.clicked.connect(lambda: show_help(self, "oauth", "microsoft"))
        self.help_google.clicked.connect(lambda: show_help(self, "oauth", "google"))
        hb.addWidget(self.help_ms)
        hb.addWidget(self.help_google)
        hb.addStretch(1)
        o.add(hb)
        grid.addWidget(o, 2, 1)
        for card in (a, c, d, s, n, o):
            card.lay.addStretch(1)
        lay.addLayout(grid)
        about = Card()
        ar = QHBoxLayout()
        ar.addWidget(label(f"<b>{APP_NAME} {__version__}</b> · GPL-3.0"))
        ar.addStretch(1)
        about.add(ar)
        lay.addWidget(about)
        lay.addStretch(1)
        sc = QScrollArea()
        sc.setWidgetResizable(True)
        sc.setFrameShape(QScrollArea.Shape.NoFrame)
        sc.setWidget(inner)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(sc)
        self.load()

    # ------------------------------------------------------------
    def load(self) -> None:
        c = self.cfg
        p = c.programm
        self.ag_name.setText(c.agentur.name)
        self.ag_addr.setText(c.agentur.adresse)
        self._logo = c.agentur.logo
        self._show_logo()
        self.examiner.setText(c.examiner_name)
        self.pattern.setText(c.case_nummer_muster)
        self.target.setText(p.zielpfad)
        self.design.set_value(p.design)
        self.lang.setCurrentIndex(max(self.lang.findData(p.sprache), 0))
        self.rlang.setCurrentIndex(max(self.rlang.findData(p.bericht_sprache), 0))
        self.mlist.setCurrentIndex(max(self.mlist.findData(p.bericht_mailliste), 0))
        self.mlist_max.setValue(p.bericht_mailliste_max)
        self.mlist_max.setEnabled(p.bericht_mailliste == "auto")
        self.h_md5.setChecked("md5" in p.hash_extra)
        self.h_sha1.setChecked("sha1" in p.hash_extra)
        self.h_sha512.setChecked("sha512" in p.hash_extra)
        (self.zt_int if p.zip_zeit_quelle == "internaldate" else self.zt_date).setChecked(True)
        self.timeout.setValue(p.socket_timeout_s)
        self.awake.setChecked(p.wach_halten)
        self.dkim.setChecked(p.dkim_pruefen)
        self.spaces.setChecked(p.leerzeichen_ersetzen)
        self.workdel.setCurrentIndex(max(self.workdel.findData(p.arbeitsordner_loeschen), 0))
        self.zipmode.setCurrentIndex(max(self.zipmode.findData(p.zip_aufteilung), 0))
        self.encmode.setCurrentIndex(max(self.encmode.findData(p.verschluesselung), 0))
        self.conns.setValue(p.verbindungen)
        self.chunk.setValue(p.chunk_mb)
        self.ntp.setChecked(p.ntp)
        self.ntp_server.setText(p.ntp_server)
        self.proxy.setText(c.proxy_url)
        self.tsa.setText(c.tsa_url)
        self.g_id.setText(c.oauth_google.client_id)
        self.g_secret.setText(c.oauth_google.client_secret)
        self.m_id.setText(c.oauth_microsoft.client_id)
        self.m_tenant.setText(c.oauth_microsoft.tenant)

    def _logo_abs(self) -> Path | None:
        if not self._logo:
            return None
        p = Path(self._logo)
        return p if p.is_absolute() else (self.path.parent / p)

    def _show_logo(self) -> None:
        p = self._logo_abs()
        if p and p.exists():
            pm = QPixmap(str(p)).scaled(150, 70, Qt.AspectRatioMode.KeepAspectRatio,
                                        Qt.TransformationMode.SmoothTransformation)
            self.logo_preview.setPixmap(pm)
            self.logo_hint.setText(self.tr("PNG oder JPG, max. 2 MB · wird beim Anlegen eines Case in die case.sqlite "
                                           "kopiert · SHA-256 ") + hashlib.sha256(p.read_bytes()).hexdigest()[:16] + "…")
        else:
            self.logo_preview.clear()
            self.logo_preview.setText(self.tr("kein Logo"))
            self.logo_hint.setText(self.tr("PNG oder JPG, max. 2 MB · im PDF max. 40 × 20 mm"))

    def _set_logo(self, path: str) -> None:
        self._logo = path
        self._show_logo()

    def pick_logo(self) -> None:
        f, _ = QFileDialog.getOpenFileName(self, self.tr("Logo wählen"), str(self.path.parent), self.tr("Bilder (*.png *.jpg *.jpeg)"))
        if not f:
            return
        if Path(f).stat().st_size > 2 * 1024 * 1024:
            QMessageBox.warning(self, "FED", self.tr("Das Logo ist größer als 2 MB."))
            return
        try:
            f = str(Path(f).relative_to(self.path.parent))
        except ValueError:
            pass
        self._set_logo(f)

    def save(self) -> None:
        c = self.cfg
        p = c.programm
        old_language = p.sprache
        c.agentur.name, c.agentur.adresse, c.agentur.logo = self.ag_name.text(), self.ag_addr.text(), self._logo
        c.examiner_name, c.case_nummer_muster, p.zielpfad = self.examiner.text(), self.pattern.text(), self.target.text()
        p.design = self.design.value()
        p.sprache, p.bericht_sprache = self.lang.currentData(), self.rlang.currentData()
        p.bericht_mailliste, p.bericht_mailliste_max = self.mlist.currentData(), self.mlist_max.value()
        p.hash_extra = [n for n, cb in (("md5", self.h_md5), ("sha1", self.h_sha1), ("sha512", self.h_sha512))
                        if cb.isChecked()]
        p.zip_zeit_quelle = "internaldate" if self.zt_int.isChecked() else "date_header"
        p.socket_timeout_s, p.chunk_mb = self.timeout.value(), self.chunk.value()
        p.wach_halten = self.awake.isChecked()
        p.dkim_pruefen = self.dkim.isChecked()
        p.leerzeichen_ersetzen = self.spaces.isChecked()
        p.arbeitsordner_loeschen = self.workdel.currentData()
        p.zip_aufteilung = self.zipmode.currentData()
        p.verschluesselung = self.encmode.currentData()
        p.verbindungen = self.conns.value()
        p.ntp, p.ntp_server = self.ntp.isChecked(), self.ntp_server.text()
        c.proxy_url, c.tsa_url = self.proxy.text(), self.tsa.text()
        c.oauth_google.client_id, c.oauth_google.client_secret = self.g_id.text().strip(), self.g_secret.text().strip()
        c.oauth_microsoft.client_id, c.oauth_microsoft.tenant = self.m_id.text().strip(), self.m_tenant.text().strip()
        try:
            cfgmod.validate(c)
            cfgmod.save(c, self.path)
            new = cfgmod.load(self.path)
        except (cfgmod.ConfigError, OSError) as e:
            QMessageBox.critical(self, "FED", str(e))
            return
        self.cfg.raw_text, self.cfg.source_path = new.raw_text, new.source_path
        self.saved.emit(self.cfg)
        if p.sprache != old_language:
            QMessageBox.information(self, "FED", "Die neue Sprache gilt ab dem nächsten Start von FED.\n"
                                                 "The new language takes effect the next time FED is started.")
