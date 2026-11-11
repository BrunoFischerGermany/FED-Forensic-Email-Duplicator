"""Assistent „Sicherung“: Case → Konto → Ordner → Sicherung → Abschluss."""

from __future__ import annotations

import html
import json
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from PySide6.QtCore import QT_TRANSLATE_NOOP, QCoreApplication, QDate, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QFontDatabase
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDateEdit, QFileDialog, QFormLayout, QGridLayout,
                               QHBoxLayout, QHeaderView, QInputDialog, QLineEdit, QMessageBox, QPlainTextEdit,
                               QProgressBar, QPushButton, QScrollArea, QSpinBox, QStackedWidget, QTreeWidget,
                               QTreeWidgetItem, QVBoxLayout, QWidget)

from fed.core import acquisition as acq
from fed.core import autoconfig, keepawake, oauth, syscheck
from fed.core import timefmt as tf
from fed.core.auth import (APP_PASSWORD_URL, OAuthSession, is_gmail, is_microsoft, log_oauth_login, make_connector,
                           normalize_app_password)
from fed.core.case import Case, CaseInfo
from fed.core.config import Config
from fed.core.finalize import finish_case
from fed.core.naming import next_evidence, normalize_email, suggest_case_number
from fed.gui.accounts import AccountsRail, AccountsTable, incomplete, state_text
from fed.core.report import _fmt_bytes
from fed.gui.help import show_help
from fed.gui.richedit import RichNoteEdit
from fed.gui.widgets import (Card, ElidedLabel, KeyValues, PasswordField, Segmented, label, mark_invalid, restyle,
                            run_async, vbox)
from fed.core.msg import describe


UROLE = Qt.ItemDataRole.UserRole
CHECKED, UNCHECKED = Qt.CheckState.Checked, Qt.CheckState.Unchecked
RIGHT = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter


def stay_awake(st, what: str) -> None:
    if not st.cfg.programm.wach_halten:
        return
    method = keepawake.shared().acquire()
    if st.case:
        st.case.log("INFO" if method else "WARNING", "keep_awake_on",
                    {"for": what, "method": method or "unavailable"})


def allow_sleep(st) -> None:
    if not st.cfg.programm.wach_halten:
        return
    ka = keepawake.shared()
    if ka.active:
        ka.release()
        if st.case and not ka.active:
            st.case.log("INFO", "keep_awake_off", "")


@dataclass
class State:
    cfg: Config
    case: Case | None = None
    account_id: int | None = None
    email: str = ""
    connect: object = None
    oauth_session: OAuthSession | None = None
    system: syscheck.SystemInfo | None = None
    finish: object = None
    spec: acq.AccountSpec | None = None
    attempts: list = field(default_factory=list)
    jobs: object = None  # JobManager – bleibt beim Wechsel des Case erhalten
    vault: object = None  # SessionVault – Anmeldedaten nur im Arbeitsspeicher, bis FED beendet wird


def page(title: str, subtitle: str = "") -> tuple[QWidget, QVBoxLayout]:
    w = QWidget()
    w.setObjectName("Page")
    lay = QVBoxLayout(w)
    lay.setContentsMargins(40, 30, 40, 24)
    lay.setSpacing(20)
    lay.addWidget(label(title, "H1"))
    sub = label(subtitle, "Muted", wrap=True)
    lay.addWidget(sub)
    lay.itemAt(1).widget().setVisible(bool(subtitle))
    w.subtitle = sub
    return w, lay


def scroll(w: QWidget) -> QScrollArea:
    s = QScrollArea()
    s.setWidgetResizable(True)
    s.setFrameShape(QScrollArea.Shape.NoFrame)
    s.setWidget(w)
    return s


def fmt_secs(s: float | None) -> str:
    if s is None:
        return "–"
    s = int(s)
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"


# ====================================================================== Schritt 1
def ask_device_owner(parent: QWidget) -> str | None:
    """Zu Beginn dokumentieren, auf wessen Gerät FED läuft: ``own`` (Ja), ``subject`` (Nein), ``None`` (Abbrechen)."""
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Question)
    box.setWindowTitle(QCoreApplication.translate("CasePage", "Ausführendes Gerät"))
    box.setText(QCoreApplication.translate("CasePage", "Wird FED auf einem eigenen Gerät ausgeführt (z. B. Dienstgerät)?"))
    box.setInformativeText(QCoreApplication.translate(
        "CasePage", "Ja – eigenes Gerät\nNein – Gerät der betroffenen Person\nAbbrechen – nicht fortfahren (kein Fall)\n\n"
                    "Die Antwort wird im Protokoll und im Bericht dokumentiert."))
    box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
                           | QMessageBox.StandardButton.Cancel)
    box.setDefaultButton(QMessageBox.StandardButton.Cancel)
    box.setEscapeButton(QMessageBox.StandardButton.Cancel)
    r = box.exec()
    return {QMessageBox.StandardButton.Yes: "own", QMessageBox.StandardButton.No: "subject"}.get(
        QMessageBox.StandardButton(r))


class CasePage(QWidget):
    next = Signal()

    def __init__(self, st: State) -> None:
        super().__init__()
        self.st = st
        w, lay = page(self.tr("Fall anlegen"),
                      self.tr("Ohne Fall startet keine Sicherung. Examiner und Agentur kommen aus der config.toml."))
        cols = QHBoxLayout()
        cols.setSpacing(24)
        form_card = Card()
        f = QGridLayout()
        f.setHorizontalSpacing(16)
        f.setVerticalSpacing(6)
        self.case_no = QLineEdit(self._suggest())
        self.examiner = QLineEdit(st.cfg.examiner_name)
        self.notes = RichNoteEdit()
        self.notes.setPlaceholderText(self.tr("z. B. Auftrag, Aktenzeichen, Beschluss"))
        self.notes.setFixedHeight(130)
        self.target = QLineEdit(st.cfg.programm.zielpfad or str(Path.cwd()))
        browse = QPushButton(self.tr("Durchsuchen …"))
        browse.clicked.connect(self._browse)
        self.browse_btn = browse
        f.addWidget(label(self.tr("Aktenzeichen *"), "H2"), 0, 0)
        f.addWidget(label(self.tr("Examiner *"), "H2"), 0, 1)
        f.addWidget(self.case_no, 1, 0)
        f.addWidget(self.examiner, 1, 1)
        f.addWidget(label(self.tr("Anmerkungen"), "H2"), 2, 0, 1, 2)
        f.addWidget(self.notes, 3, 0, 1, 2)
        f.addWidget(label(self.tr("Zielordner *"), "H2"), 4, 0, 1, 2)
        tr_ = QHBoxLayout()
        tr_.addWidget(self.target, 1)
        tr_.addWidget(browse)
        f.addLayout(tr_, 5, 0, 1, 2)
        self.target_hint = ElidedLabel("", "Hint")
        f.addWidget(self.target_hint, 6, 0, 1, 2)
        self.name_hint = label("", "Warn")
        self.name_hint.setWordWrap(True)
        self.name_hint.setVisible(False)
        f.addWidget(self.name_hint, 7, 0, 1, 2)
        form_card.add(f)
        self.target.textChanged.connect(self._update_hint)
        self.case_no.textChanged.connect(self._update_hint)

        sys_card = Card(self.tr("Systemprüfung"))
        self.sys = KeyValues()
        sys_card.add(self.sys)
        self.free_bar = QProgressBar()
        self.free_bar.setFixedHeight(8)
        sys_card.add(self.free_bar)
        recheck = QPushButton(self.tr("Erneut prüfen"))
        recheck.clicked.connect(self.check_system)
        sys_card.add(recheck)
        sys_card.lay.addStretch(1)
        cols.addWidget(form_card, 3)
        cols.addWidget(sys_card, 2)
        lay.addLayout(cols)
        lay.addStretch(1)
        self.error = label("", "Error", wrap=True)
        lay.addWidget(self.error)
        open_btn = QPushButton(self.tr("Vorhandenen Fall öffnen …"))
        open_btn.clicked.connect(self._open_existing)
        self.go = QPushButton(self.tr("Fall anlegen und weiter"))
        self.go.setObjectName("Primary")
        self.go.clicked.connect(self._go_clicked)
        self.open_btn = open_btn
        btns = QHBoxLayout()
        btns.addStretch(1)
        btns.addWidget(open_btn)
        btns.addWidget(self.go)
        lay.addLayout(btns)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll(w))
        self._update_hint()
        QTimer.singleShot(0, self.check_system)

    def _suggest(self) -> str:
        try:
            return suggest_case_number(self.st.cfg.case_nummer_muster or "{jahr}-{lfd:04}")
        except ValueError:
            return ""

    def _browse(self) -> None:
        d = QFileDialog.getExistingDirectory(self, self.tr("Zielordner wählen"), self.target.text())
        if d:
            self.target.setText(d)
            self.check_system()

    def _update_hint(self) -> None:
        from fed.core.naming import file_name

        raw = self.case_no.text().strip()
        safe = file_name(raw or "…", self.st.cfg.programm.leerzeichen_ersetzen)
        self.target_hint.setText(self.tr("Fall-Ordner wird angelegt: ") + str(Path(self.target.text()) / safe))
        changed = bool(raw) and safe != raw
        self.name_hint.setVisible(changed)
        if changed:
            text = (self.tr("Ordnername: „{0}“ – nicht erlaubte Zeichen (z. B. / \\ : *) und Leerzeichen werden durch "
                            "„_“ ersetzt (Einstellungen). Das Aktenzeichen selbst bleibt unverändert in Bericht und Log.")
                    if self.st.cfg.programm.leerzeichen_ersetzen else
                    self.tr("Ordnername: „{0}“ – Zeichen, die in Ordnernamen nicht erlaubt sind (z. B. / \\ : *), werden "
                            "durch „_“ ersetzt. Das Aktenzeichen selbst bleibt unverändert in Bericht und Log."))
            self.name_hint.setText(text.format(safe))

    def check_system(self) -> None:
        target = Path(self.target.text() or ".")
        ntp = self.st.cfg.programm.ntp_server if self.st.cfg.programm.ntp else None
        self.sys.set(self.tr("Status"), self.tr("prüfe …"))

        def work(cancel, report):
            return syscheck.collect(target if target.exists() else Path.cwd(), ntp, self.st.cfg.proxy_url)

        run_async(work, self._show_system, lambda e, tb: self.sys.set(self.tr("Status"), e, "error"), parent=self)

    def _show_system(self, info: syscheck.SystemInfo) -> None:
        self.st.system = info
        s = self.sys
        s.clear()
        s.set(self.tr("Benutzer"), info.user, mono=True)
        s.set(self.tr("Rechner"), f"{info.hostname} · {info.os.split('(')[0].strip()}")
        ntp = f" · NTP {info.ntp_offset_s:+.1f} s" if info.ntp_offset_s is not None else ""
        s.set(self.tr("Uhrzeit"), f"{info.local_time.replace('T', ' ')}\nUTC {info.utc_time[11:19]}{ntp}")
        s.set(self.tr("Internet"), "● online" if info.online else "● offline", "ok" if info.online else "error")
        rem = {True: (self.tr("extern ✓"), "ok"), False: (self.tr("intern – Warnung"), "warn"),
               None: (self.tr("unbekannt"), "warn")}[info.target_removable]
        s.set(self.tr("Ziel-Datenträger"), rem[0], rem[1])
        import shutil

        total = shutil.disk_usage(info.target_path).total or 1
        s.set(self.tr("Freier Speicher"), f"{_fmt_bytes(info.free_bytes)} / {_fmt_bytes(total)}")
        self.free_bar.setValue(int(100 * (1 - info.free_bytes / total)))

    def _validate(self) -> bool:
        self.error.setText("")
        if not self.case_no.text().strip() or not self.examiner.text().strip():
            self.error.setText(self.tr("Aktenzeichen und Examiner sind Pflicht."))
            return False
        info = self.st.system
        if info and not info.online:
            self.error.setText(self.tr("Keine Internetverbindung – eine Sicherung ist so nicht möglich."))
            return False
        if info and info.target_removable is not True:
            r = QMessageBox.warning(self, self.tr("Ziel nicht extern"),
                                    self.tr("Der Zielordner liegt nicht (erkennbar) auf einem externen Datenträger. "
                                            "Trotzdem fortfahren? Die Bestätigung wird protokolliert."),
                                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if r != QMessageBox.StandardButton.Yes:
                return False
        return True

    def sync(self) -> None:
        """Ist schon ein Fall offen, zeigt die Seite ihn nur an: „Weiter“ statt „Fall anlegen“."""
        case = self.st.case
        is_open = case is not None
        if is_open:
            info = case.info()
            self.case_no.setText(info.get("case_number") or "")
            self.examiner.setText(info.get("examiner") or "")
            self.notes.set_value(info.get("notes") or "", info.get("notes_html"))
            self.target.setText(str(case.root.parent))
            self.error.setText("")
        for w in (self.case_no, self.examiner, self.target):
            w.setReadOnly(is_open)
        self.notes.setReadOnly(is_open)
        self.browse_btn.setEnabled(not is_open)
        self.go.setText(self.tr("Weiter") if is_open else self.tr("Fall anlegen und weiter"))
        self.open_btn.setVisible(not is_open)

    def _go_clicked(self) -> None:
        if self.st.case is not None:  # Fall läuft schon – nicht neu anlegen
            self.next.emit()
            return
        self._create()

    def _create(self) -> None:
        if not self._validate():
            return
        owner = ask_device_owner(self)
        if owner is None:  # Abbrechen: kein Fall
            return
        try:
            case = Case.create(Path(self.target.text()), CaseInfo(self.case_no.text(), self.examiner.text(),
                                                                   self.notes.toPlainText(), self.notes.html() or None),
                               replace_spaces=self.st.cfg.programm.leerzeichen_ersetzen)
        except FileExistsError as e:
            folder = self._existing_folder()
            if folder is not None and QMessageBox.question(
                    self, self.tr("Fall existiert bereits"),
                    self.tr("Für dieses Aktenzeichen gibt es im Zielordner schon einen Fall:\n{0}\n\n"
                            "Diesen Fall öffnen und fortsetzen?").format(folder)) == QMessageBox.StandardButton.Yes:
                self._open(folder)
                return
            self.error.setText(str(e))
            return
        except (ValueError, OSError) as e:
            self.error.setText(str(e))
            return
        try:
            case.store_config_snapshot(self.st.cfg.raw_text, self.st.cfg.logo_path())
        except ValueError as e:
            case.log("WARNING", "logo_rejected", str(e))
        case.set_device_owner(owner, self.st.system.hostname if self.st.system else "")
        if self.st.system:
            case.record_system_check(self.st.system.as_dict())
            if self.st.system.target_removable is not True:
                case.log("WARNING", "target_not_external_confirmed", {"path": self.st.system.target_path})
        self.st.case = case
        self.next.emit()

    def _existing_folder(self) -> Path | None:
        """Ordner des Falls, den „Fall anlegen“ mit den aktuellen Eingaben anlegen würde – falls es ihn gibt."""
        from fed.core.naming import file_name

        try:
            name = file_name(self.case_no.text().strip(), self.st.cfg.programm.leerzeichen_ersetzen)
        except Exception:
            return None
        folder = Path(self.target.text()) / name
        return folder if (folder / "case.sqlite").exists() else None

    def _open_existing(self) -> None:
        d = QFileDialog.getExistingDirectory(self, self.tr("Fall-Ordner wählen"), self.target.text())
        if d:
            self._open(Path(d))

    def _open(self, folder: Path) -> None:
        try:
            case = Case.open(folder)
        except (FileNotFoundError, OSError) as e:
            self.error.setText(str(e))
            return
        if not case.closed:  # Fortsetzung, evtl. auf einem anderen Rechner: erneut dokumentieren
            owner = ask_device_owner(self)
            if owner is None:
                case.close()
                return
            case.set_device_owner(owner, self.st.system.hostname if self.st.system else "")
        self.st.case = case
        self.next.emit()


# ====================================================================== Schritt 3
class AccountPage(QWidget):
    next = Signal()
    back = Signal()
    finish = Signal()
    new_account = Signal()

    def __init__(self, st: State) -> None:
        super().__init__()
        self.st = st
        self.found: autoconfig.ImapSettings | None = None
        w, lay = page(self.tr("Konto einrichten"),
                      self.tr("Adresse eingeben – Server, Port und Verschlüsselung werden wie bei Thunderbird "
                              "automatisch ermittelt."))
        self.accounts_card = Card(self.tr("Bereits in diesem Fall"))
        self.accounts = AccountsTable()
        self.accounts_card.add(self.accounts)
        self.accounts_card.add(label(self.tr("Doppelklick auf ein Konto übernimmt es zum Fortsetzen; für ein neues "
                                             "Konto unten die Adresse eingeben."), "Hint", wrap=True))
        self.accounts.picked.connect(self._pick)
        self.accounts.notes_changed.connect(self._notes_changed)
        self.accounts_card.setVisible(False)
        lay.addWidget(self.accounts_card)
        cols = QHBoxLayout()
        cols.setSpacing(24)
        left = QVBoxLayout()
        left.setSpacing(24)
        c1 = Card()
        g = QGridLayout()
        g.setVerticalSpacing(6)
        g.setHorizontalSpacing(16)
        self.email = QLineEdit()
        self.email.setPlaceholderText("name@example.de")
        self.evidence = QLineEdit()
        self.evidence.setPlaceholderText("A-01")
        g.addWidget(label(self.tr("E-Mail-Adresse *"), "H2"), 0, 0)
        g.addWidget(label(self.tr("Asservatennummer *"), "H2"), 0, 1)
        g.addWidget(self.email, 1, 0)
        g.addWidget(self.evidence, 1, 1)
        c1.add(g)
        self.evidence_hint = label("", "Hint", wrap=True)
        self.evidence_hint.setVisible(False)
        c1.add(self.evidence_hint)
        self.discover_info = label(self.tr("Einstellungen werden nach Eingabe der Adresse gesucht."), "Muted", wrap=True)
        c1.add(self.discover_info)
        mf = QFormLayout()
        self.server = QLineEdit()
        self.port = QSpinBox()
        self.port.setRange(1, 65535)
        self.port.setValue(993)
        self.security = QComboBox()
        self.security.addItems(["ssl", "starttls"])
        self.user = QLineEdit()
        self.user.setPlaceholderText(self.tr("Standard: E-Mail-Adresse"))
        mf.addRow(self.tr("Server"), self.server)
        mf.addRow(self.tr("Port"), self.port)
        mf.addRow(self.tr("Sicherheit"), self.security)
        mf.addRow(self.tr("Benutzername"), self.user)
        c1.add(mf)
        self.email.editingFinished.connect(self._discover)
        self.email.editingFinished.connect(self._check_known)
        self.server.textChanged.connect(lambda _t: self._update_pw_hint())
        left.addWidget(c1)

        c2 = Card(self.tr("Anmeldung"))
        self.method = Segmented([("password", self.tr("Passwort")), ("google_app", self.tr("Google (App-Passwort)")),
                                 ("google", "Google (OAuth)"), ("microsoft", "Microsoft 365 (OAuth)")])
        self.method.changed.connect(self._method_changed)
        c2.add(self.method)
        self.pw_label = label(self.tr("Passwort"), "H2")
        self.password = PasswordField()
        self.pw_hint = label(self.tr("Nur im Arbeitsspeicher – wird nie gespeichert oder geloggt."), "Hint")
        self.oauth_hint = label("", "Hint", wrap=True)
        for lab in (self.pw_hint, self.oauth_hint):  # Link „Anleitung öffnen“ -> Hilfe-Fenster
            lab.linkActivated.connect(lambda section: show_help(self, "oauth", section))
        self.device = QCheckBox(self.tr("Anmeldung per Gerätecode (anderes Gerät)"))
        self.login_as = QLineEdit()
        self.login_as.setPlaceholderText(self.tr("anmelden als (nur bei geteiltem Postfach)"))
        # Anmeldedaten aus dieser Sitzung (nur im Arbeitsspeicher) – Hinweis und „Vergessen“
        self.vault_row = QWidget()
        vr = QHBoxLayout(self.vault_row)
        vr.setContentsMargins(0, 0, 0, 0)
        self.vault_lbl = label("", "Ok", wrap=True)
        forget = QPushButton(self.tr("Vergessen"))
        forget.setToolTip(self.tr("Gespeicherte Anmeldedaten dieses Kontos sofort aus dem Arbeitsspeicher löschen"))
        forget.clicked.connect(self._forget)
        vr.addWidget(self.vault_lbl, 1)
        vr.addWidget(forget)
        self.vault_row.setVisible(False)
        self._recalled: str | None = None
        self.password.textEdited.connect(lambda *_: self._recall_off())
        for x in (self.pw_label, self.password, self.vault_row, self.pw_hint, self.oauth_hint, self.device,
                  self.login_as):
            c2.add(x)
        left.addWidget(c2)
        cn = Card(self.tr("Notiz zum Konto"))
        self.acc_notes = RichNoteEdit()
        self.acc_notes.setPlaceholderText(self.tr("z. B. Herkunft der Zugangsdaten, Kontoinhaber, Einwilligung/Beschluss, "
                                                  "Besonderheiten – erscheint im Bericht"))
        self.acc_notes.setFixedHeight(120)
        cn.add(self.acc_notes)
        left.addWidget(cn)
        cols.addLayout(left, 3)

        # Aktionen oben im Verbindungstest – ohne Scrollen erreichbar, Ergebnis und Fehler direkt darunter
        self.test_btn = QPushButton(self.tr("Verbindung testen"))
        self.test_btn.clicked.connect(self._test)
        self.go = QPushButton(self.tr("Postfach scannen"))
        self.go.setObjectName("Primary")
        self.go.setToolTip(self.tr("Testet die Verbindung, falls noch nicht geschehen, und liest dann die Ordner"))
        self._ok = False  # Verbindungstest mit den aktuellen Eingaben erfolgreich
        self._go_after_test = False
        self.go.clicked.connect(self._go_clicked)
        c3 = Card(self.tr("Verbindungstest"))
        actions = QHBoxLayout()
        actions.addWidget(self.test_btn)
        actions.addWidget(self.go, 1)
        c3.add(actions)
        self.error = label("", "Error", wrap=True)
        c3.add(self.error)
        self.test_state = label(self.tr("noch nicht getestet"), "Muted")
        c3.add(self.test_state)
        self.test_state.linkActivated.connect(lambda section: show_help(self, "oauth", section))
        self.test = KeyValues()
        c3.add(self.test)
        c3.lay.addStretch(1)
        cols.addWidget(c3, 2, Qt.AlignmentFlag.AlignTop)
        lay.addLayout(cols)
        lay.addStretch(1)
        back = QPushButton(self.tr("Zurück"))
        back.clicked.connect(self.back.emit)
        # ohne neues Konto direkt zum Abschluss (z. B. nach einem Neustart von FED)
        self.finish_btn = QPushButton(self.tr("Fall abschließen …"))
        self.finish_btn.setToolTip(self.tr("Kein weiteres Konto – den Fall mit den gesicherten Konten abschließen"))
        self.finish_btn.clicked.connect(self.finish.emit)
        self.finish_btn.setVisible(False)
        self.new_btn = QPushButton(self.tr("+ Weiteres Konto"))
        self.new_btn.setToolTip(self.tr("Formular leeren und ein weiteres Konto zu diesem Fall hinzufügen"))
        self.new_btn.clicked.connect(self.new_account.emit)
        self.new_btn.setVisible(False)
        # Leiste unten bleibt beim Scrollen sichtbar
        footer = QWidget()
        footer.setObjectName("Page")
        b = QHBoxLayout(footer)
        b.setContentsMargins(40, 10, 40, 16)
        b.addWidget(back)
        b.addWidget(self.new_btn)
        b.addWidget(self.finish_btn)
        b.addStretch(1)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(scroll(w), 1)
        outer.addWidget(footer)
        self._method_changed("password")
        for x in (self.server, self.user, self.password, self.email):
            x.textChanged.connect(lambda *_: self._untested())

    def _untested(self) -> None:
        """Eingaben geändert: der letzte Verbindungstest gilt nicht mehr."""
        self._ok = False

    def _go_clicked(self) -> None:
        """„Postfach scannen“: ohne gültigen Test erst testen, bei Erfolg automatisch weiter."""
        if self._ok:
            self._go()
            return
        self._go_after_test = True
        self._test()
        if self.test_btn.isEnabled():  # Test gar nicht gestartet (Eingaben unvollständig)
            self._go_after_test = False

    def reset(self) -> None:
        """Formular für ein neues Konto leeren; Anmeldeart zurück auf „Passwort“."""
        for x in (self.email, self.evidence, self.server, self.user, self.password, self.login_as):
            x.clear()
        self.acc_notes.clear()
        self.port.setValue(993)
        self.security.setCurrentText("ssl")
        self.device.setChecked(False)
        self.method.set_value("password")
        self._method_changed("password")
        self.found = None
        self._recall_off()
        self._untested()
        self.test.clear()
        self.test_state.setText(self.tr("noch nicht getestet"))
        restyle(self.test_state, "Muted")
        self.discover_info.setText(self.tr("Einstellungen werden nach Eingabe der Adresse gesucht."))
        self.error.setText("")

    def prepare(self) -> None:
        """Vor dem Einrichten eines (weiteren) Kontos: Übersicht zeigen, Evidence-Nummer vorschlagen."""
        rows = self.accounts.refresh(self.st.case)
        self.accounts_card.setVisible(bool(rows))
        self.finish_btn.setVisible(bool(rows) and not self.st.case.closed)
        self.new_btn.setVisible(bool(rows) and not self.st.case.closed)
        suggestion = next_evidence([o["evidence"] for o in rows])
        if suggestion and not self.evidence.text().strip():
            self.evidence.setText(suggestion)
            self.evidence.selectAll()
            self.evidence_hint.setText(self.tr("Asservatennummer vorgeschlagen (nach {0}) – bitte prüfen.")
                                       .format(rows[-1]["evidence"]))
        else:
            self.evidence_hint.setText("")
        self.evidence_hint.setVisible(bool(self.evidence_hint.text()))

    def _notes_changed(self, account_id: int, text: str) -> None:
        """Notiz in der Übersicht geändert: Formular nachziehen, wenn es dasselbe Konto zeigt."""
        row = self.st.case.conn.execute("SELECT email, notes, notes_html FROM account WHERE id = ?",
                                        (account_id,)).fetchone()
        if row and normalize_email(self.email.text()) == row[0]:
            self.acc_notes.set_value(row[1], row[2])

    def _pick(self, o: dict) -> None:
        """Konto aus der Übersicht übernehmen (Server, Port, Benutzer aus der Case-Datei)."""
        row = self.st.case.conn.execute("SELECT server, port, security, username, auth_method FROM account "
                                        "WHERE id = ?", (o["id"],)).fetchone()
        self.email.setText(o["email"])
        self.server.setText(row[0])
        self.port.setValue(row[1])
        self.security.setCurrentText(row[2])
        self.user.setText(row[3] if row[3] != o["email"] else "")
        m = {"oauth_google": "google", "oauth_microsoft": "microsoft",
             "app_password_google": "google_app"}.get(row[4], "password")
        self.method.set_value(m)
        self._method_changed(m)
        self.found = None
        self.acc_notes.set_value(o.get("notes", ""), o.get("notes_html"))
        self._check_known()
        self.password.setFocus()

    def _known(self, email: str) -> dict | None:
        return next((o for o in self.accounts.rows if o["email"].casefold() == email.casefold()), None)

    def _check_known(self) -> None:
        o = self._known(normalize_email(self.email.text()))
        if o:
            sym, text, _ = state_text(o)
            self.evidence_hint.setText(self.tr("Dieses Konto ist schon im Fall ({0}, {1} {2}) – es wird fortgesetzt, "
                                               "die Asservatennummer bleibt {0}.").format(o["evidence"], sym, text))
            self.evidence.setText(o["evidence"])
            self.evidence_hint.setVisible(True)
        self._recall()

    # ------------------------------------------------------------ Anmeldedaten der Sitzung
    def _recall(self) -> None:
        """Passwort/OAuth-Anmeldung aus dieser Sitzung übernehmen, wenn das Konto schon einmal erfolgreich
        getestet wurde – nichts davon wird auf die Festplatte geschrieben oder geloggt."""
        vault = self.st.vault
        email = normalize_email(self.email.text())
        entry = vault.get(email) if vault is not None and email else None
        if entry is None:
            return
        if self.method.value() != entry.method:  # Adresse getippt: Anmeldeart des letzten Erfolgs übernehmen
            if self.password.text():
                return
            self.method.set_value(entry.method)
            self._method_changed(entry.method)
        if entry.method in ("password", "google_app"):
            if self.password.text() and self.password.text() != entry.password:
                return  # selbst eingegebenes Passwort hat Vorrang
            self.password.setText(entry.password)
        elif entry.oauth is not None:
            self.st.oauth_session = entry.oauth
        self._recalled = email
        self.vault_lbl.setText(self.tr("✓ Anmeldedaten aus dieser Sitzung übernommen (zuletzt erfolgreich um {0}) – "
                                       "nur im Arbeitsspeicher, bis FED beendet wird.").format(entry.when))
        self.vault_row.setVisible(True)

    def _recall_off(self) -> None:
        self._recalled = None
        self.vault_row.setVisible(False)

    def _forget(self) -> None:
        email = self._recalled or normalize_email(self.email.text())
        if self.st.vault is not None:
            self.st.vault.forget(email)
        if self.method.value() in ("password", "google_app"):
            self.password.clear()
        else:
            self.st.oauth_session = None
        self._recall_off()
        self._untested()
        self.test_state.setText(self.tr("noch nicht getestet"))
        restyle(self.test_state, "Muted")

    def _method_changed(self, m: str) -> None:
        pw = m in ("password", "google_app")
        if self._recalled:
            self._recall_off()
        for x in (self.pw_label, self.password, self.pw_hint):
            x.setVisible(pw)
        self.pw_label.setText(self.tr("App-Passwort") if m == "google_app" else self.tr("Passwort"))
        self.pw_hint.setWordWrap(True)
        self._update_pw_hint()
        for x in (self.oauth_hint, self.login_as):
            x.setVisible(not pw)
        self.device.setVisible(m == "microsoft")
        cid = {"google": self.st.cfg.oauth_google.client_id, "microsoft": self.st.cfg.oauth_microsoft.client_id}.get(m)
        text = (self.tr("Beim Test öffnet sich der Browser zur Anmeldung. Das Passwort sieht FED nicht.")
                if cid else self.tr("Keine Client-ID in den Einstellungen eingetragen (Einstellungen → "
                                    "Google / Microsoft 365)."))
        self.oauth_hint.setText(self._with_help_link(text, "microsoft" if m == "microsoft" else "google"))
        restyle(self.oauth_hint, "Hint" if cid else "Warn")
        self._untested()

    def _ms_password_text(self) -> str:
        return self.tr("Microsoft (Outlook.com, Hotmail, Live, Microsoft 365) nimmt für IMAP weder das "
                       "Kontopasswort noch ein App-Passwort an – nur „Microsoft 365 (OAuth)“. IMAP muss im Postfach "
                       "erlaubt sein: Outlook.com → Einstellungen → E-Mail → Weiterleitung und IMAP; Microsoft 365 → "
                       "Administrator (Admin Center → Benutzer → E-Mail-Apps).")

    def _with_help_link(self, text: str, section: str) -> str:
        link = html.escape(self.tr("Anleitung öffnen"))
        return f'{html.escape(text)} <a href="{section}">{link}</a>'

    def _update_pw_hint(self) -> None:
        """Hinweis unter dem Passwortfeld; bei einem Microsoft-Server mit Passwort-Anmeldung als Warnung."""
        m = self.method.value()
        if m in ("password", "google_app") and is_microsoft(self.server.text()):
            self.pw_hint.setText(self._with_help_link(self._ms_password_text(), "microsoft"))
            restyle(self.pw_hint, "Warn")
            return
        self.pw_hint.setText(
            self.tr("16 Buchstaben aus {0} (erfordert 2-Schritt-Bestätigung; Leerzeichen egal). Das normale "
                    "Google-Passwort funktioniert nicht. Nur im Arbeitsspeicher – wird nie gespeichert oder geloggt.")
            .format(APP_PASSWORD_URL) if m == "google_app"
            else self.tr("Nur im Arbeitsspeicher – wird nie gespeichert oder geloggt."))
        restyle(self.pw_hint, "Hint")

    def _discover(self) -> None:
        email = normalize_email(self.email.text())
        if "@" not in email or (self.found and self.found.username == email):
            return
        self.discover_info.setText(self.tr("Suche Einstellungen …"))

        def work(cancel, report):
            return autoconfig.discover(email, proxy=self.st.cfg.proxy_url)

        def done(res):
            s, log = res
            self.found = s
            if s:
                self.server.setText(s.host)
                self.port.setValue(s.port)
                self.security.setCurrentText(s.security)
                if s.username and s.username != email:
                    self.user.setText(s.username)
                step = next((ln.split(".")[0] for ln in log if "gefunden (" in ln or "erreichbar" in ln), "?")
                self.discover_info.setText(self.tr("Einstellungen gefunden – Quelle: ") + f"{describe(s.source)} "
                                           + self.tr("(Versuch {0} von 6)").format(step))
                restyle(self.discover_info, "Ok")
                if s.provider is autoconfig.Provider.GOOGLE:
                    # mit eingerichteter Client-ID OAuth, sonst App-Passwort
                    g = "google" if self.st.cfg.oauth_google.client_id else "google_app"
                    self.method.set_value(g)
                    self._method_changed(g)
                elif s.provider is autoconfig.Provider.MICROSOFT:
                    self.method.set_value("microsoft")
                    self._method_changed("microsoft")
            else:
                self.discover_info.setText(self.tr("Nichts gefunden – bitte Server manuell eintragen."))
                restyle(self.discover_info, "Warn")

        run_async(work, done, lambda e, tb: self.discover_info.setText(e), parent=self)

    def _connector(self):
        m = self.method.value()
        user = normalize_email(self.user.text()) or normalize_email(self.email.text())
        kw = {"timeout": self.st.cfg.programm.socket_timeout_s}
        if m in ("password", "google_app"):
            pw = normalize_app_password(self.password.text()) if m == "google_app" else self.password.text()
            return make_connector(self.server.text().strip(), self.port.value(), self.security.currentText(), user,
                                  password=pw, **kw)
        return make_connector(self.server.text().strip(), self.port.value(), self.security.currentText(), user,
                              oauth_session=self.st.oauth_session, **kw)

    def _missing(self) -> list:
        """Leere Pflichtfelder: Adresse, Evidence-Nummer, Server und – bei Passwort-Anmeldung – das Passwort."""
        need = [self.email, self.evidence, self.server]
        if self.method.value() in ("password", "google_app"):
            need.append(self.password)
        return [w for w in need if not w.text().strip()]

    def _test(self) -> None:
        self.error.setText("")
        missing = self._missing()
        for w in (self.email, self.evidence, self.server, self.password):
            mark_invalid(w, w in missing)
        problems = []
        if missing:  # nicht testen – sonst meldet der Server nur „authentication failed“
            names = {self.email: self.tr("E-Mail-Adresse"), self.evidence: self.tr("Asservatennummer"),
                     self.server: self.tr("Server"), self.password: self.tr("Passwort")}
            problems.append(self.tr("Bitte ausfüllen: {0}").format(", ".join(names[w] for w in missing)))
        ev, email = self.evidence.text().strip(), normalize_email(self.email.text())
        other = next((o for o in self.accounts.rows if ev and o["evidence"].casefold() == ev.casefold()
                      and o["email"].casefold() != email.casefold()), None)
        if other:
            mark_invalid(self.evidence)
            problems.append(self.tr("Die Asservatennummer {0} ist im Fall schon für {1} vergeben.")
                            .format(ev, other["email"]))
        if problems:
            self.error.setText("\n".join(problems))
            self.test_state.setText("")
            (missing[0] if missing else self.evidence).setFocus()
            return
        m = self.method.value()
        self.test_btn.setEnabled(False)
        self.go.setEnabled(False)
        self.test_state.setText(self.tr("teste …"))
        restyle(self.test_state, "Muted")
        cfg = self.st.cfg
        # alle Eingaben im GUI-Thread lesen – der Worker fasst keine Widgets an
        host, port, sec = self.server.text().strip(), self.port.value(), self.security.currentText()
        user = normalize_email(self.user.text()) or normalize_email(self.email.text())
        password = normalize_app_password(self.password.text()) if m == "google_app" else self.password.text()
        self._tested = (normalize_email(self.email.text()), m, password)
        hint = normalize_email(self.login_as.text()) or normalize_email(self.email.text())
        device = self.device.isChecked()
        timeout = cfg.programm.socket_timeout_s
        st = self.st

        def work(cancel, report):
            if m in ("password", "google_app"):
                connect = make_connector(host, port, sec, user, password=password, timeout=timeout)
            else:
                prov = (oauth.GOOGLE if m == "google"
                        else oauth.microsoft(oauth.microsoft_tenant(cfg.oauth_microsoft.tenant, hint)))
                cid = cfg.oauth_google.client_id if m == "google" else cfg.oauth_microsoft.client_id
                secret = cfg.oauth_google.client_secret if m == "google" else ""
                sess = OAuthSession(prov, cid, secret, cfg.proxy_url)
                sess.login_interactive(login_hint=hint, device_code=device, show_device_code=report, cancel=cancel)
                st.oauth_session = sess
                connect = make_connector(host, port, sec, user, oauth_session=sess, timeout=timeout)
            c = connect()
            try:
                return c.tls_info(), c.server_info(), len(c.list_folders())
            finally:
                c.logout()

        def progress(dc):
            if isinstance(dc, oauth.DeviceCode):
                self.test_state.setText(self.tr("Code {0} auf {1} eingeben").format(dc.user_code, dc.verification_uri))

        run_async(work, self._test_ok, self._test_failed, progress, parent=self)

    def _test_ok(self, res) -> None:
        tls, info, nfolders = res
        self.test_btn.setEnabled(True)
        self.test_state.setText(self.tr("✓ erfolgreich"))
        restyle(self.test_state, "Ok")
        t = self.test
        t.clear()
        t.set("TLS", f"{tls['version']}")
        t.set(self.tr("Zertifikat"), self.tr("gültig bis ") + str(tls["not_after"]), "ok")
        pairs = tls["sha256"].split(":")
        t.set("SHA-256", "\n".join(":".join(pairs[i:i + 11]) for i in range(0, len(pairs), 11)), mono=True)
        t.set(self.tr("Server"), info.get("id") or "–")
        t.set("Capabilities", " ".join(info["capabilities"][:8]) + (" …" if len(info["capabilities"]) > 8 else ""),
              mono=True)
        if self.st.oauth_session and self.method.value() in ("google", "microsoft"):
            t.set(self.tr("Angemeldet als"), self.st.oauth_session.token.account or "?")
        now = datetime.now(UTC)
        t.set(self.tr("Anmeldung"), self.tr("OK · ") + now.strftime("%H:%M:%S UTC") + " / "
              + tf.local_str(now, with_date=False), "ok")
        t.set(self.tr("Ordner"), self.tr("{0} gefunden").format(nfolders))
        self._ok = True
        self.go.setEnabled(True)
        tested = getattr(self, "_tested", None)
        if self.st.vault is not None and tested and tested[0] == normalize_email(self.email.text()):
            email, m, password = tested
            self.st.vault.put(email, m, password if m in ("password", "google_app") else None,
                              self.st.oauth_session if m in ("google", "microsoft") else None)
        if self._go_after_test:  # „Postfach scannen“ hatte den Test ausgelöst
            self._go_after_test = False
            self._go()

    def _login_hint(self, err: str) -> str:
        """Hinweis bei abgelehnter Anmeldung mit Passwort/App-Passwort (übersetzbar)."""
        m = self.method.value()
        if m == "microsoft":  # OAuth-Fehler (AADSTS…) kommen ohne die IMAP-Schlüsselwörter
            return oauth.explain_error("microsoft", err)
        low = err.lower()
        if not any(k in low for k in ("authenticationfailed", "authentication failed", "invalid credentials",
                                      "login failed", "logon failure", "auth", "credentials")):
            return ""
        if m not in ("password", "google_app"):
            return ""
        if is_microsoft(self.server.text()):
            return self._ms_password_text()
        if is_gmail(self.server.text()):
            return self.tr("Google nimmt kein normales Kontopasswort an: App-Passwort (16 Buchstaben, {0}, "
                           "2-Schritt-Bestätigung nötig) oder OAuth verwenden. In Google Workspace muss der "
                           "Administrator App-Passwörter erlauben.").format(APP_PASSWORD_URL)
        return self.tr("Bitte prüfen: Benutzername und Passwort korrekt? Bei vielen Anbietern muss der Zugriff per "
                       "IMAP erst im Webmail freigeschaltet werden (z. B. GMX/WEB.DE: Einstellungen → POP3/IMAP "
                       "Abruf), oder es ist ein App-Passwort nötig (z. B. bei aktivierter Zwei-Faktor-Anmeldung, "
                       "iCloud, Yahoo).")

    def _test_failed(self, err: str, tb: str) -> None:
        self.test_btn.setEnabled(True)
        hint = self._login_hint(err)
        m, host = self.method.value(), self.server.text()
        section = ("microsoft" if m == "microsoft" or is_microsoft(host)
                   else "google" if m in ("google", "google_app") or is_gmail(host) else None)
        if hint and section:  # mit Link zur passenden Anleitung
            self.test_state.setText(html.escape(self.tr("✗ fehlgeschlagen: ") + err).replace("\n", "<br>")
                                    + "<br><br>" + self._with_help_link(hint, section))
        else:
            self.test_state.setText(self.tr("✗ fehlgeschlagen: ") + err + (f"\n\n{hint}" if hint else ""))
        self.test_state.setWordWrap(True)
        restyle(self.test_state, "Error")
        self._untested()
        self._go_after_test = False
        self.go.setEnabled(True)

    def _go(self) -> None:
        self.password.hide_text()
        self.st.email = normalize_email(self.email.text())
        self.st.connect = self._connector()
        case = self.st.case
        if self._recalled == self.st.email:  # nachvollziehbar: Anmeldedaten nicht neu eingegeben (Wert nie im Log)
            case.log("INFO", "credentials_from_session", {"email": self.st.email, "method": self.method.value()})
        if self.st.oauth_session and self.st.oauth_session.token:
            warn = log_oauth_login(case, self.st.oauth_session.token, self.st.email)
            if warn:
                QMessageBox.warning(self, self.tr("Anderes Konto"), warn)
        row = case.conn.execute("SELECT id FROM account WHERE email = ?", (self.st.email,)).fetchone()
        self.st.account_id = row[0] if row else None
        others = case.conn.execute("SELECT COUNT(*) FROM account").fetchone()[0]
        if self.st.account_id is None and others >= 1 and case.zip_mode() is None:
            ask_zip_mode(self, case, self.st.cfg)  # beim 2. Konto: ZIP-Aufteilung für den Abschluss
        self.st.spec = acq.AccountSpec(
            self.st.email, self.evidence.text().strip(), self.server.text().strip(), self.port.value(),
            self.security.currentText(), normalize_email(self.user.text()) or self.st.email,
            {"password": "password", "google_app": "app_password_google", "google": "oauth_google",
             "microsoft": "oauth_microsoft"}[self.method.value()],
            self.found.source if self.found else "manual", notes=self.acc_notes.toPlainText(),
            notes_html=self.acc_notes.html() or None)
        if self.st.account_id is not None:
            acq.set_account_notes(case, self.st.account_id, self.acc_notes.toPlainText(),
                                  self.acc_notes.html() or None)
        self.next.emit()


# ====================================================================== Schritt 4/5
class FolderPage(QWidget):
    next = Signal()
    back = Signal()

    def __init__(self, st: State) -> None:
        super().__init__()
        self.st = st
        w, lay = page(self.tr("Ordner wählen"))
        self.sub = w.subtitle
        top = QHBoxLayout()
        top.addStretch(1)
        for text, fn in ((self.tr("Alle"), lambda: self._check_all(True)), (self.tr("Keine"), lambda: self._check_all(False)),
                         (self.tr("Neu scannen"), self.scan)):
            b = QPushButton(text)
            b.clicked.connect(fn)
            top.addWidget(b)
        lay.addLayout(top)
        self.gmail_box = Card()
        self.gmail_lbl = label(self.tr(
            "Gmail erkannt: Ordner sind bei Gmail Labels – eine Mail mit mehreren Labels stünde mehrfach im ZIP. "
            "Empfohlen und vorausgewählt: „Alle Nachrichten“, „Spam“ und „Papierkorb“ (jede Mail genau einmal); "
            "die Labels jeder Mail werden dokumentiert (Fall-Datei, log.csv, Bericht)."), "Hint", wrap=True)
        gb = QHBoxLayout()
        b1 = QPushButton(self.tr("Gmail-Empfehlung"))
        b1.clicked.connect(self._gmail_recommended)
        b2 = QPushButton(self.tr("Alle Label-Ordner (mit Duplikaten)"))
        b2.clicked.connect(lambda: self._check_all(True))
        gb.addWidget(b1)
        gb.addWidget(b2)
        gb.addStretch(1)
        self.gmail_box.add(self.gmail_lbl)
        self.gmail_box.lay.addLayout(gb)
        self.gmail_box.setVisible(False)
        lay.addWidget(self.gmail_box)
        cols = QHBoxLayout()
        cols.setSpacing(24)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels([self.tr("Ordner"), self.tr("Mails"), self.tr("Größe")])
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.tree.setMinimumHeight(260)
        self.tree.itemChanged.connect(self._update_summary)
        cols.addWidget(self.tree, 3)
        right = QVBoxLayout()
        right.setSpacing(24)
        c = Card(self.tr("Zeitraum (optional)"))
        self.use_from = QCheckBox(self.tr("Von"))
        self.use_to = QCheckBox(self.tr("Bis einschließlich"))
        self.d_from = QDateEdit(QDate.currentDate().addYears(-1))
        self.d_to = QDateEdit(QDate.currentDate())
        for d in (self.d_from, self.d_to):
            d.setCalendarPopup(True)
            d.setDisplayFormat("dd.MM.yyyy")
        g = QGridLayout()
        g.addWidget(self.use_from, 0, 0)
        g.addWidget(self.d_from, 0, 1)
        g.addWidget(self.use_to, 1, 0)
        g.addWidget(self.d_to, 1, 1)
        c.add(g)
        c.add(label(self.tr("Bezieht sich auf die Eingangszeit auf dem Server. Mit Filter gilt die Sicherung als "
                            "Teilsicherung."), "Hint", wrap=True))
        right.addWidget(c)
        cf = Card(self.tr("Inhalt (optional)"))
        self.flt = {}
        fg = QFormLayout()
        for key, text, ph in (("from", self.tr("Absender enthält"), self.tr("z. B. mueller@firma.de, @lieferant.example")),
                              ("to", self.tr("Empfänger enthält"), self.tr("To, CC oder BCC")),
                              ("subject", self.tr("Betreff enthält"), self.tr("z. B. Rechnung, Angebot")),
                              ("text", self.tr("Text enthält"), self.tr("Kopf und Inhalt, z. B. Kontonummer"))):
            e = QLineEdit()
            e.setPlaceholderText(ph)
            self.flt[key] = e
            fg.addRow(text, e)
        cf.add(fg)
        cf.add(label(self.tr("Mehrere Werte mit Komma = oder; ausgefüllte Felder untereinander = und. Die Suche führt "
                             "der Server aus. Mit Inhaltsfilter gilt die Sicherung als Teilsicherung; die Kriterien "
                             "stehen im Bericht."), "Hint", wrap=True))
        right.addWidget(cf)
        s = Card(self.tr("Zusammenfassung"))
        self.summary = KeyValues()
        s.add(self.summary)
        right.addWidget(s)
        right.addStretch(1)
        cols.addLayout(right, 2)
        lay.addLayout(cols, 1)
        self.error = label("", "Error", wrap=True)
        lay.addWidget(self.error)
        back = QPushButton(self.tr("Zurück"))
        back.clicked.connect(self.back.emit)
        self.go = QPushButton(self.tr("Sicherung starten"))
        self.go.setObjectName("Primary")
        self.go.clicked.connect(self._go)
        # Knöpfe fest unten, der Inhalt darüber scrollt – so bleibt auf kleinen Bildschirmen alles
        # bedienbar und nichts wird zusammengedrückt
        bar = QWidget()
        bar.setObjectName("ButtonBar")
        b = QHBoxLayout(bar)
        m = lay.contentsMargins()
        b.setContentsMargins(m.left(), 12, m.right(), 16)
        b.addWidget(back)
        b.addStretch(1)
        b.addWidget(self.go)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(scroll(w), 1)
        outer.addWidget(bar)

    def scan(self) -> None:
        st = self.st
        self.go.setEnabled(False)
        self.sub.setText(self.tr("Postfach wird gescannt …"))
        self.sub.setVisible(True)

        def work(cancel, report):
            imap = st.connect()
            try:
                if st.account_id is None:
                    st.account_id = acq.register_account(st.case, st.spec, imap)
                return acq.scan(st.case, st.account_id, imap, cancel)
            finally:
                imap.logout()

        run_async(work, self._show, lambda e, tb: (self.sub.setText(self.tr("Scan fehlgeschlagen: ") + e),
                                                   restyle(self.sub, "Error")), parent=self)

    def _show(self, rows) -> None:
        self.tree.blockSignals(True)
        self.tree.clear()
        cur = self.st.case.conn.execute(
            "SELECT display_name, delimiter, count_server, size_server, selected, flags FROM folder "
            "WHERE account_id = ? ORDER BY id", (self.st.account_id,))
        items: dict[str, QTreeWidgetItem] = {}
        total_m = total_b = 0
        for name, delim, cnt, size, sel, flags in sorted(cur.fetchall(), key=lambda r: (r[0] != "INBOX", r[0].lower())):
            parent_name, _, leaf = name.rpartition(delim) if delim and delim in name else ("", "", name)
            parent = items.get(parent_name)
            it = QTreeWidgetItem([leaf, f"{cnt or 0:,}".replace(",", "."), _fmt_bytes(size or 0)])
            it.setData(0, UROLE, name)
            it.setData(1, UROLE, cnt or 0)
            it.setData(2, UROLE, size or 0)
            it.setCheckState(0, CHECKED if sel else UNCHECKED)
            if "\\Noselect" in (flags or ""):
                it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsUserCheckable)
                it.setCheckState(0, UNCHECKED)
                it.setToolTip(0, self.tr("Ordner ist nicht auswählbar (\\Noselect)"))
            it.setTextAlignment(1, RIGHT)
            it.setTextAlignment(2, RIGHT)
            (parent.addChild(it) if parent else self.tree.addTopLevelItem(it))
            items[name] = it
            total_m += cnt or 0
            total_b += size or 0
        self.tree.expandAll()
        self.tree.blockSignals(False)
        self.sub.setText(self.tr("Scan abgeschlossen um {0} UTC ({1}) · {2} Ordner · {3} Mails · {4} laut Server").format(
            datetime.now(UTC).strftime("%H:%M:%S"), tf.local_str(datetime.now(UTC), with_date=False), len(items), f"{total_m:,}".replace(",", "."), _fmt_bytes(total_b)))
        restyle(self.sub, "Muted")
        self.gmail_box.setVisible(acq.is_gmail_account(self.st.case, self.st.account_id))
        row = self.st.case.conn.execute("SELECT filters FROM account WHERE id = ?", (self.st.account_id,)).fetchone()
        existing = json.loads(row[0]) if row and row[0] else {}
        for k, e in self.flt.items():
            e.setText(", ".join(existing.get(k, [])))
        self._update_summary()
        self.go.setEnabled(True)

    def _gmail_recommended(self) -> None:
        names = set(acq.gmail_recommended(self.st.case, self.st.account_id))
        self.tree.blockSignals(True)
        for it in self._iter():
            if it.flags() & Qt.ItemFlag.ItemIsUserCheckable:
                it.setCheckState(0, CHECKED if it.data(0, UROLE) in names else UNCHECKED)
        self.tree.blockSignals(False)
        self._update_summary()

    def _iter(self):
        stack = [self.tree.topLevelItem(i) for i in range(self.tree.topLevelItemCount())]
        while stack:
            it = stack.pop()
            yield it
            stack.extend(it.child(i) for i in range(it.childCount()))

    def _check_all(self, on: bool) -> None:
        self.tree.blockSignals(True)
        for it in self._iter():
            if it.flags() & Qt.ItemFlag.ItemIsUserCheckable:  # \\Noselect-Ordner bleiben aus
                it.setCheckState(0, CHECKED if on else UNCHECKED)
        self.tree.blockSignals(False)
        self._update_summary()

    def _update_summary(self, *_) -> None:
        sel = [it for it in self._iter() if it.checkState(0) == CHECKED]
        m = sum(it.data(1, UROLE) for it in sel)
        b = sum(it.data(2, UROLE) for it in sel)
        not_sel = [it for it in self._iter() if it.checkState(0) != CHECKED]
        self.summary.set(self.tr("Ausgewählt"), self.tr("{0} Mails · {1}").format(f"{m:,}".replace(",", "."), _fmt_bytes(b)))
        self.summary.set(self.tr("Nicht ausgewählt"), self.tr("{0} Ordner · {1} Mails").format(
            len(not_sel), f"{sum(it.data(1, UROLE) for it in not_sel):,}".replace(",", ".")))
        free = self.st.system.free_bytes if self.st.system else None
        if free is not None:
            ok = free > b * 1.2
            self.summary.set(self.tr("Freier Speicher am Ziel"), _fmt_bytes(free) + (" ✓" if ok else self.tr(" – zu wenig")),
                             "ok" if ok else "error")
            self.go.setEnabled(ok and bool(sel))

    def _go(self) -> None:
        names = {it.data(0, UROLE) for it in self._iter() if it.checkState(0) == CHECKED}
        acq.set_selection(self.st.case, self.st.account_id, names)
        df = datetime(*self.d_from.date().getDate(), tzinfo=UTC) if self.use_from.isChecked() else None
        dt = datetime(*self.d_to.date().getDate(), tzinfo=UTC) if self.use_to.isChecked() else None
        acq.set_date_filter(self.st.case, self.st.account_id, df, dt)
        acq.set_content_filter(self.st.case, self.st.account_id, {k: e.text() for k, e in self.flt.items()})
        self.next.emit()


# ====================================================================== Schritt 6
class AcquirePage(QWidget):
    finished = Signal(object)
    another = Signal()
    finish = Signal()

    def __init__(self, st: State) -> None:
        super().__init__()
        self.st = st
        self.job = None
        w, lay = page(self.tr("Sicherung läuft"))
        self.sub = w.subtitle
        self.title = lay.itemAt(0).widget()
        self.account_lbl = ElidedLabel("", "H2")
        lay.insertWidget(1, self.account_lbl)
        head = QHBoxLayout()
        head.addStretch(1)
        self.cancel_btn = QPushButton(self.tr("Abbrechen"))
        self.cancel_btn.setObjectName("Danger")
        self.cancel_btn.clicked.connect(self._cancel)
        head.addWidget(self.cancel_btn)
        lay.insertLayout(0, head)
        total = Card()
        r = QHBoxLayout()
        r.addWidget(label(self.tr("Gesamtfortschritt"), "H2"))
        r.addStretch(1)
        self.pct = label("0 %", "Huge")
        r.addWidget(self.pct)
        total.add(r)
        self.bar = QProgressBar()
        self.bar.setFixedHeight(14)
        total.add(self.bar)
        r2 = QHBoxLayout()
        self.bytes_lbl = label("", "Muted")
        self.eta_lbl = label("", "Muted")
        r2.addWidget(self.bytes_lbl)
        r2.addStretch(1)
        r2.addWidget(self.eta_lbl)
        total.add(r2)
        lay.addWidget(total)
        stats = Card()
        g = QGridLayout()
        self.stat = {}
        for i, (k, t) in enumerate((("mails", self.tr("Mails")), ("data", self.tr("Datenmenge")),
                                    ("speed", self.tr("Geschwindigkeit")), ("time", self.tr("Laufzeit")))):
            g.addWidget(label(t, "Muted"), 0, i)
            self.stat[k] = (label("–", "Big"), label("", "Hint"))
            g.addWidget(self.stat[k][0], 1, i)
            g.addWidget(self.stat[k][1], 2, i)
        stats.add(g)
        lay.addWidget(stats)
        cols = QHBoxLayout()
        cols.setSpacing(20)
        fc = Card()
        self.folder_lbl = ElidedLabel("", "H2")
        self.folder_bar = QProgressBar()
        self.folder_bar.setFixedHeight(8)
        self.uid_lbl = label("", "Mono")
        fc.add(self.folder_lbl)
        fc.add(self.folder_bar)
        fc.add(self.uid_lbl)
        fc.lay.addStretch(1)
        cols.addWidget(fc, 3)
        lc = Card(self.tr("Log"))
        self.issues = label("", "Warn")
        lc.add(self.issues)
        self.log = QPlainTextEdit()
        self.log.setObjectName("Mono")
        f = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
        f.setPointSize(9)
        self.log.setFont(f)
        self.log.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.log.setReadOnly(True)
        self.log.setMinimumHeight(160)
        lc.add(self.log)
        cols.addWidget(lc, 2)
        self.accounts_card = Card(self.tr("Konten in diesem Fall"))
        self.accounts = AccountsTable()
        self.accounts_card.add(self.accounts)
        zr = QHBoxLayout()
        self.zip_lbl = label("", "Muted")
        self.zip_btn = QPushButton(self.tr("ändern …"))
        self.zip_btn.clicked.connect(self._change_zip_mode)
        zr.addWidget(self.zip_lbl)
        zr.addWidget(self.zip_btn)
        zr.addStretch(1)
        self.accounts_card.lay.addLayout(zr)
        self.accounts_card.setVisible(False)
        lay.addWidget(self.accounts_card)
        lay.addLayout(cols, 1)
        b = QHBoxLayout()
        b.addStretch(1)
        self.resume_btn = QPushButton(self.tr("Fortsetzen"))
        self.restart_btn = QPushButton(self.tr("Neubeginn …"))
        self.another_btn = QPushButton(self.tr("Weiteres Konto im Fall sichern"))
        self.finish_btn = QPushButton(self.tr("Fall abschließen"))
        self.finish_btn.setObjectName("Primary")
        self.resume_btn.clicked.connect(lambda: self._restart_job(acq.Mode.RESUME))
        self.restart_btn.clicked.connect(self._restart)
        self.another_btn.clicked.connect(self.another.emit)
        self.finish_btn.clicked.connect(self.finish.emit)
        for x in (self.resume_btn, self.restart_btn, self.another_btn, self.finish_btn):
            b.addWidget(x)
            x.setVisible(False)
        lay.addLayout(b)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll(w))
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tail_log)

    # ------------------------------------------------------------ Auftrag anzeigen
    @property
    def worker(self):
        return self.job.worker if self.job else None

    def start(self, mode=acq.Mode.RESUME, reason: str = "", delete_old: bool = False) -> None:
        """Sicherung des Kontos aus dem Assistenten starten (oder fortsetzen) und anzeigen."""
        st = self.st
        evidence = st.spec.evidence_number if st.spec else ""
        if not evidence and st.account_id is not None:
            row = st.case.conn.execute("SELECT evidence FROM account WHERE id = ?", (st.account_id,)).fetchone()
            evidence = row[0] if row else ""
        job = st.jobs.start(st, st.account_id, st.email, evidence, st.connect, mode, reason, delete_old)
        self.show_job(job)

    def _restart_job(self, mode=acq.Mode.RESUME, reason: str = "", delete_old: bool = False) -> None:
        job = self.job
        if job is None:
            return
        self.st.jobs.start(self.st, job.account_id, job.email, job.evidence, job.connect, mode, reason, delete_old)
        self.show_job(job)

    def show_job(self, job) -> None:
        if self.job is not None and self.job is not job:
            for sig, slot in ((self.job.progress, self._on_progress), (self.job.finished, self._on_finished)):
                try:
                    sig.disconnect(slot)
                except (RuntimeError, TypeError):
                    pass
        if self.job is not job:
            job.progress.connect(self._on_progress)
            job.finished.connect(self._on_finished)
        self.job = job
        self.log.clear()
        self._last_log = job.log_from
        restyle(self.sub, "Muted")
        self._tail_log()
        if job.running:
            self._render_running()
            if job.last is not None:
                self._progress(job.last)
            else:
                self._reset_numbers()
            self.timer.start(1000)
        else:
            self.timer.stop()
            if job.last is not None:
                self._progress(job.last)
            if job.result is not None:
                self._done(job.result)
            elif job.error:
                self._failed(job.error, "")

    def _on_progress(self, job) -> None:
        if job is self.job:
            self._progress(job.last)

    def _on_finished(self, job) -> None:
        if job is not self.job:
            return
        self.timer.stop()
        self._tail_log()
        if job.result is not None:
            self._done(job.result)
        else:
            self._failed(job.error, "")

    def _reset_numbers(self) -> None:
        self.bar.setValue(0)
        self.pct.setText("0 %")
        self.bytes_lbl.setText("")
        self.eta_lbl.setText("")
        for big, small in self.stat.values():
            big.setText("–")
            small.setText("")
        self.folder_lbl.setText(self.tr("Ordner werden vorbereitet …"))
        self.folder_bar.setValue(0)
        self.uid_lbl.setText("")
        self.issues.setText("")

    def _render_running(self) -> None:
        job = self.job
        for x in (self.resume_btn, self.restart_btn, self.finish_btn):
            x.setVisible(False)
        self.another_btn.setText(self.tr("Weiteres Konto sichern – diese Sicherung läuft weiter"))
        self.another_btn.setVisible(True)
        self.cancel_btn.setVisible(True)
        self.cancel_btn.setEnabled(not job.cancelling)
        self._show_accounts()
        self.title.setText(self.tr("Sicherung läuft") if not job.cancelling else self.tr("Wird abgebrochen …"))
        started = datetime.fromtimestamp(job.started_wall, UTC)
        self.sub.setText(self.tr("Modus {0} · gestartet {1} UTC ({2}) · {3} Verbindungen · nur lesend "
                                 "(EXAMINE, BODY.PEEK)").format(
            self.tr("Fortsetzen") if job.mode is acq.Mode.RESUME else self.tr("Neubeginn"),
            started.strftime("%H:%M:%S"), tf.local_str(started, with_date=False) or "–",
            job.cfg.programm.verbindungen))
        self.sub.setVisible(True)

    def _progress(self, p) -> None:
        if p is None:
            return
        pct = int(100 * p.bytes_done / p.bytes_total) if p.bytes_total else 100
        self.bar.setValue(pct)
        self.pct.setText(f"{pct} %")
        bps, mpm = p.speed()
        eta = p.eta_seconds()
        self.bytes_lbl.setText(f"{_fmt_bytes(p.bytes_done)} / {_fmt_bytes(p.bytes_total)}")
        self.eta_lbl.setText(self.tr("Restzeit ca. ") + fmt_secs(eta))
        self.stat["mails"][0].setText(f"{p.mails_done:,} / {p.mails_total:,}".replace(",", "."))
        self.stat["mails"][1].setText(self.tr("{0} verbleibend").format(f"{p.mails_total - p.mails_done:,}".replace(",", ".")))
        self.stat["data"][0].setText(f"{_fmt_bytes(p.bytes_done)}")
        self.stat["data"][1].setText(self.tr("{0} verbleibend").format(_fmt_bytes(max(p.bytes_total - p.bytes_done, 0))))
        self.stat["speed"][0].setText(f"{bps / 1e6:.1f} MB/s")
        self.stat["speed"][1].setText(self.tr("ca. {0} Mails/min · Mittel 30 s").format(int(mpm)))
        self.stat["time"][0].setText(fmt_secs(time.monotonic() - p.started))
        self.stat["time"][1].setText(self.tr("Restzeit ca. ") + fmt_secs(eta))
        if p.phase == "auth_check":
            self.folder_lbl.setText(self.tr("Echtheitsprüfung (DKIM) der gesicherten Mails …"))
        else:
            self.folder_lbl.setText(self.tr("Aktueller Ordner: ") + f"{p.folder}  ·  {p.folder_done} / {p.folder_total}")
        self.folder_bar.setValue(int(100 * p.folder_done / p.folder_total) if p.folder_total else 100)
        self.uid_lbl.setText(f"UID {p.current_uid}")
        self.issues.setText(self.tr("{0} Warnungen · {1} Fehler").format(p.warnings, p.errors))

    def _tail_log(self) -> None:
        job = self.job
        if job is None or self.st.case is None:
            return
        # nur Einträge dieses Kontos (und allgemeine ohne Versuch), nicht die parallel laufender Konten
        rows = self.st.case.conn.execute(
            "SELECT id, ts_utc, level, event, details FROM log WHERE id > ? AND (attempt_id IS NULL OR attempt_id IN "
            "(SELECT id FROM attempt WHERE account_id = ?)) ORDER BY id",
            (getattr(self, "_last_log", 0), job.account_id)).fetchall()
        for id_, ts, level, event, details in rows:
            self._last_log = id_
            self.log.appendPlainText(f"{ts[11:19]} {level[:4]:<4} {event} {details[:90]}")
        self._tick = getattr(self, "_tick", 0) + 1
        if self._tick % 3 == 0:
            self._show_accounts()

    def _cancel(self) -> None:
        if self.job and self.job.running:
            self.job.cancel()
            self.cancel_btn.setEnabled(False)
            self.title.setText(self.tr("Wird abgebrochen …"))

    def _done(self, res) -> None:
        self.cancel_btn.setEnabled(True)
        self.cancel_btn.setVisible(False)
        txt = {"done": self.tr("Sicherung abgeschlossen"), "cancelled": self.tr("Sicherung abgebrochen"),
               "failed": self.tr("Sicherung fehlgeschlagen")}[res.status]
        self.title.setText(txt)
        self.sub.setText(self.tr("Versuch {0}: {1} gesichert · {2} bereits vorhanden · erwartet {3}").format(
            res.nr, res.saved, res.skipped_existing, res.expected) + ("\n" + "\n".join(res.warnings) if res.warnings else ""))
        if res.status == "done":
            self.bar.setValue(100)
            self.pct.setText("100 %")
        others = [j for j in self.st.jobs.running() if j is not self.job]
        self.resume_btn.setVisible(res.status != "done")
        self.restart_btn.setVisible(True)
        self.another_btn.setText(self.tr("Weiteres Konto im Fall sichern"))
        self.another_btn.setVisible(res.status == "done" or bool(others))
        self.finish_btn.setVisible(res.status == "done")
        self.finish_btn.setEnabled(not others)
        self.finish_btn.setToolTip(self.tr("Erst möglich, wenn alle laufenden Sicherungen beendet sind") if others else "")
        self._show_accounts()
        self.finished.emit(res)

    def _change_zip_mode(self) -> None:
        ask_zip_mode(self, self.st.case, self.st.cfg, force=True)
        self._show_accounts()

    def _show_accounts(self) -> None:
        email = self.job.email if self.job else self.st.email
        rows = self.accounts.refresh(self.st.case, email)
        self.accounts_card.setVisible(len(rows) > 1 or self.cancel_btn.isHidden())
        self.zip_lbl.setText(self.tr("ZIP-Archive am Ende: {0}").format(zip_mode_text(self.st.case.zip_mode())))
        for x in (self.zip_lbl, self.zip_btn):
            x.setVisible(len(rows) > 1)
        self.zip_btn.setEnabled(not self.st.jobs.running())
        cur = next((o for o in rows if o["email"] == email), None)
        if cur:
            nr = rows.index(cur) + 1
            self.account_lbl.setText(self.tr("Konto {0} von {1}: {2} · {3}").format(
                nr, len(rows), cur["evidence"], cur["email"]))
        if self.finish_btn.isVisible():
            others = [j for j in self.st.jobs.running() if j is not self.job]
            self.finish_btn.setEnabled(not others)

    def _failed(self, err: str, tb: str) -> None:
        self.timer.stop()
        self.cancel_btn.setVisible(False)
        self.title.setText(self.tr("Sicherung fehlgeschlagen"))
        self.sub.setText(err)
        restyle(self.sub, "Error")
        self._show_accounts()
        self.resume_btn.setVisible(True)
        self.restart_btn.setVisible(True)
        self.another_btn.setText(self.tr("Weiteres Konto im Fall sichern"))
        self.another_btn.setVisible(bool(self.st.jobs.running()))

    def _restart(self) -> None:
        reason, ok = QInputDialog.getText(self, self.tr("Neubeginn"),
                                          self.tr("Grund für den Neubeginn (Pflicht, wird protokolliert):"))
        if not ok or not reason.strip():
            return
        r = QMessageBox.question(self, self.tr("Altstand"), self.tr(
            "Bisherigen Stand nach „discarded/“ verschieben (empfohlen)?\n\nNein = endgültig löschen."),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No | QMessageBox.StandardButton.Cancel)
        if r == QMessageBox.StandardButton.Cancel:
            return
        delete = r == QMessageBox.StandardButton.No
        if delete and QMessageBox.warning(self, self.tr("Endgültig löschen"), self.tr(
                "Der bisherige Stand wird endgültig gelöscht. Wirklich?"),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        self._restart_job(acq.Mode.RESTART, reason.strip(), delete)


# ====================================================================== Schritt 7
class FinishPage(QWidget):
    new_case = Signal()
    retry = Signal()
    back = Signal()

    def __init__(self, st: State) -> None:
        super().__init__()
        self.st = st
        w, lay = page(self.tr("Fall abschließen"))
        self.title = lay.itemAt(0).widget()
        self.sub = w.subtitle
        cols = QHBoxLayout()
        cols.setSpacing(24)
        self.checks = Card()
        self.check_kv = KeyValues()
        self.checks.add(self.check_kv)
        self.hashes = Card()
        self.hash_kv = KeyValues()
        self.hashes.add(self.hash_kv)
        left = vbox(self.checks, self.hashes, spacing=24)
        cols.addLayout(left, 3)
        rep = Card(self.tr("Bericht"))
        self.report_lbl = ElidedLabel("", "Muted")
        rep.add(self.report_lbl)
        self.open_pdf = QPushButton(self.tr("Bericht öffnen"))
        self.open_pdf.setObjectName("Primary")
        self.open_dir = QPushButton(self.tr("Fall-Ordner öffnen"))
        self.open_pdf.clicked.connect(lambda: self._open(self.st.finish.report_pdf))
        self.open_dir.clicked.connect(lambda: self._open(self.st.case.root))
        rep.add(self.open_pdf)
        rep.add(self.open_dir)
        rep.lay.addStretch(1)
        cols.addWidget(rep, 2)
        lay.addLayout(cols)
        lay.addStretch(1)
        self.retry_btn = QPushButton(self.tr("Erneut versuchen"))
        self.retry_btn.setObjectName("Primary")
        self.retry_btn.clicked.connect(self.retry.emit)
        self.retry_btn.setVisible(False)
        self.back_btn = QPushButton(self.tr("Zurück zu den Konten"))
        self.back_btn.clicked.connect(self.back.emit)
        self.back_btn.setVisible(False)
        nb = QPushButton(self.tr("Neuen Fall anlegen"))
        nb.clicked.connect(self.new_case.emit)
        self.quit_btn = QPushButton(self.tr("FED beenden"))
        self.quit_btn.setObjectName("Primary")
        self.quit_btn.clicked.connect(lambda: self.window().close())
        nr = QHBoxLayout()
        nr.addWidget(self.back_btn)
        nr.addStretch(1)
        nr.addWidget(self.retry_btn)
        nr.addWidget(nb)
        nr.addWidget(self.quit_btn)
        lay.addLayout(nr)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll(w))

    @staticmethod
    def _open(path) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def _aborted(self) -> None:
        """Rückfrage abgebrochen: nichts abgeschlossen, Case bleibt offen."""
        self.title.setText(self.tr("Abschluss abgebrochen"))
        self.sub.setText(self.tr("Der Fall ist noch offen – nichts wurde verändert."))
        restyle(self.sub, "Muted")
        self.sub.setVisible(True)
        self.retry_btn.setVisible(True)
        self.back_btn.setVisible(True)

    def run(self) -> None:
        st = self.st
        self.retry_btn.setVisible(False)
        self.back_btn.setVisible(False)
        # Abschlussvermerk (optional) – vor ZIP und Bericht, damit er im Bericht steht
        from fed.gui.richedit import RichNoteDialog

        info = st.case.info()
        dlg = RichNoteDialog(
            self, title=self.tr("Abschlussvermerk"), heading=self.tr("Abschlussvermerk für den Bericht (optional)"),
            plain=info.get("closing_note"), html_value=info.get("closing_note_html"),
            placeholder=self.tr("z. B. Übergabe an …, Besonderheiten, Abweichungen vom Auftrag, Hinweise für die "
                                "Auswertung"),
            hint=self.tr("Erscheint im Bericht vor der Bestätigung und wird protokolliert. Leer lassen, wenn nichts "
                         "zu vermerken ist."),
            ok_text=self.tr("Weiter zum Abschluss"))
        accepted = dlg.exec()
        closing = (dlg.text(), dlg.html() or None)
        dlg.deleteLater()
        if not accepted:
            self._aborted()
            return
        mode = st.cfg.programm.arbeitsordner_loeschen
        delete = mode == "ja"
        if mode == "fragen":
            r = QMessageBox.question(
                self, self.tr("Arbeitsordner"),
                self.tr("Den Arbeitsordner „work/“ nach erfolgreicher ZIP-Verifikation löschen?\n\n"
                        "Alle Mails sind dann nur noch im geprüften ZIP enthalten; das spart den doppelten "
                        "Speicherplatz. Gelöscht wird nur, wenn jede Mail im ZIP denselben Hash hat. "
                        "Die Löschung wird protokolliert und im Bericht vermerkt."),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
            delete = r == QMessageBox.StandardButton.Yes
        password = None
        if st.cfg.programm.verschluesselung == "fragen":
            from fed.gui.encrypt_dialog import EncryptDialog

            dlg = EncryptDialog(self)
            if not dlg.exec():
                self._aborted()
                return  # Abbrechen = nicht abschließen
            password = dlg.password
            dlg.deleteLater()
        self._password = password
        self.title.setText(self.tr("Fall wird abgeschlossen …"))
        self.sub.setText(self.tr("ZIP packen, prüfen, Hashes und Berichte erzeugen"))
        restyle(self.sub, "Muted")
        self.sub.setVisible(True)
        self.open_pdf.setEnabled(False)
        self.check_kv.clear()

        def work(cancel, report):
            return finish_case(st.case, st.cfg, cancel=cancel, progress=report, delete_work=delete,
                               encrypt_password=password, closing_note=closing[0], closing_note_html=closing[1])

        def progress(p):
            self.check_kv.set(self.tr("Fortschritt"), f"{p.phase}: {p.mails_done} / {p.mails_total}")

        stay_awake(st, "finish")
        run_async(work, self._done, self._failed, progress, parent=self)

    def _done(self, res) -> None:
        allow_sleep(self.st)
        self.st.finish = res
        z = res.zip
        total = sum(r for (r,) in self.st.case.conn.execute("SELECT COUNT(*) FROM message WHERE status='valid'"))
        self.title.setText(self.tr("Fall abgeschlossen"))
        self.sub.setText(self.tr("{0} Mails gesichert und verifiziert · ab jetzt nur noch lesend").format(total))
        k = self.check_kv
        k.clear()
        changed = self.st.case.conn.execute(
            "SELECT COUNT(*) FROM folder WHERE selected = 1 AND status_before IS NOT NULL AND status_before != status_after"
        ).fetchone()[0]
        k.set(self.tr("Vorher/Nachher-Vergleich"), self.tr("unverändert ✓") if not changed
              else self.tr("{0} Ordner geändert").format(changed), "ok" if not changed else "warn")
        for z in res.zips:
            k.set(self.tr("ZIP gepackt"), f"{z.path.name} · {_fmt_bytes(z.size)}", "ok")
            k.set(self.tr("ZIP verifiziert"), self.tr("{0} .eml erneut gehasht, alle identisch").format(z.files)
                  if z.verified else "; ".join(describe(p) for p in z.problems), "ok" if z.verified else "error")
        deleted = not self.st.case.work_dir.exists()
        k.set(self.tr("Arbeitsordner"), self.tr("gelöscht (Mails nur noch im geprüften ZIP)") if deleted
              else self.tr("behalten"), "ok" if deleted else None)
        for ts, err in res.timestamps:
            if ts is not None:
                k.set(self.tr("Zeitstempel (RFC 3161)"), self.tr("{0} UTC · Seriennr. {1}").format(
                    ts.gen_time.replace("T", " ").rstrip("Z"), ts.serial), "ok")
            elif err:
                k.set(self.tr("Zeitstempel (RFC 3161)"), self.tr("fehlgeschlagen: {0}").format(err), "warn")
        reports = "report.pdf · report.txt · log.csv"
        if res.account_reports:
            reports += self.tr(" · {0} Einzelberichte je Konto").format(len(res.account_reports) // 2)
        k.set(self.tr("Berichte erzeugt"), reports, "ok")
        if res.encrypted:
            k.set(self.tr("Verschlüsselte Kopie"), f"{res.encrypted['file']} · {_fmt_bytes(res.encrypted['size'])} · "
                  + self.tr("geprüft"), "ok")
            k.set(self.tr("Passwort (nur jetzt sichtbar)"), getattr(self, "_password", "") or "", mono=True)
        self._password = None
        for z in res.zips:
            for p in z.problems:
                k.set(self.tr("Hinweis"), describe(p), "warn")
        h = self.hash_kv
        h.clear()
        for z in res.zips:
            for name in ("sha256", "md5", "sha512"):
                if name in z.hashes:
                    v = z.hashes[name]
                    key = name.upper().replace("SHA", "SHA-") + (f" · {z.path.name}" if len(res.zips) > 1 else "")
                    h.set(key, "\n".join(v[i:i + 64] for i in range(0, len(v), 64)), mono=True)
        self.report_lbl.setText(str(res.report_pdf))
        self.open_pdf.setEnabled(True)

    def _failed(self, err, tb) -> None:
        allow_sleep(self.st)
        self.title.setText(self.tr("Abschluss fehlgeschlagen"))
        self.sub.setText(err + "\n" + self.tr("Der Fall ist noch offen – die gesicherten Mails sind unverändert. "
                                               "Ursache beheben und erneut versuchen."))
        restyle(self.sub, "Error")
        self.retry_btn.setVisible(True)
        self.back_btn.setVisible(True)


def ask_zip_mode(parent, case, cfg, force: bool = False) -> str | None:
    """Fragt bei mehreren Konten, ob am Ende ein ZIP für alle oder eines je Konto entsteht.

    Ist in den Einstellungen eine feste Wahl hinterlegt, wird diese ohne Frage übernommen
    (außer ``force``). Die Antwort wird im Case gespeichert und protokolliert. ``None`` = offen gelassen.
    """
    preset = cfg.programm.zip_aufteilung
    if not force and preset in case.ZIP_MODES:
        case.set_zip_mode(preset, source="einstellung")
        return preset
    box = QMessageBox(parent)
    box.setWindowTitle(QCoreApplication.translate("Assistant", "ZIP-Archive bei mehreren Konten"))
    box.setIcon(QMessageBox.Icon.Question)
    box.setText(QCoreApplication.translate("Assistant", "Dieser Fall enthält mehrere Konten. Wie sollen die ZIP-Archive "
                                                        "am Ende erstellt werden?"))
    box.setInformativeText(QCoreApplication.translate(
        "Assistant", "Ein ZIP für alle Konten: ein Archiv, ein Hash, je Konto ein Ordner darin.\n\n"
                     "Ein ZIP je Konto: je Asservatennummer ein eigenes Archiv mit eigenem Hash (und Zeitstempel) – "
                     "lässt sich einzeln weitergeben.\n\nDie Wahl kann bis zum Abschluss geändert werden."))
    one = box.addButton(QCoreApplication.translate("Assistant", "Ein ZIP für alle Konten"),
                        QMessageBox.ButtonRole.AcceptRole)
    each = box.addButton(QCoreApplication.translate("Assistant", "Ein ZIP je Konto"), QMessageBox.ButtonRole.AcceptRole)
    later = box.addButton(QCoreApplication.translate("Assistant", "Später entscheiden"), QMessageBox.ButtonRole.RejectRole)
    current = case.zip_mode()
    box.setDefaultButton(each if current == "je_konto" else one)
    box.setEscapeButton(later)
    box.exec()
    clicked = box.clickedButton()
    if clicked is one:
        case.set_zip_mode("gesamt")
    elif clicked is each:
        case.set_zip_mode("je_konto")
    return case.zip_mode()


def zip_mode_text(mode: str | None) -> str:
    return {"gesamt": QCoreApplication.translate("Assistant", "ein ZIP für alle Konten"),
            "je_konto": QCoreApplication.translate("Assistant", "ein ZIP je Konto")}.get(
        mode or "", QCoreApplication.translate("Assistant", "noch offen – wird beim Abschluss gefragt"))


# ====================================================================== Assistent
STEPS = [QT_TRANSLATE_NOOP("Assistant", "Fall anlegen"), QT_TRANSLATE_NOOP("Assistant", "Systemprüfung"),
         QT_TRANSLATE_NOOP("Assistant", "Konto"), QT_TRANSLATE_NOOP("Assistant", "Postfach scannen"),
         QT_TRANSLATE_NOOP("Assistant", "Ordner wählen"), QT_TRANSLATE_NOOP("Assistant", "Sicherung"),
         QT_TRANSLATE_NOOP("Assistant", "Abschluss")]


class Assistant(QWidget):
    case_changed = Signal()

    def __init__(self, cfg: Config) -> None:
        super().__init__()
        self.st = State(cfg)
        from fed.gui.jobs import JobManager

        self.jobs = JobManager(self)
        self.st.jobs = self.jobs
        from fed.gui.vault import SessionVault

        self.vault = SessionVault()
        self.st.vault = self.vault
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        rail = QWidget()
        rail.setObjectName("Rail")
        rail.setFixedWidth(240)
        rl = QVBoxLayout(rail)
        rl.setContentsMargins(16, 24, 16, 16)
        rl.setSpacing(2)
        rl.addWidget(label(self.tr("ABLAUF"), "RailTitle"))
        self.step_btns = []
        for i, s in enumerate(STEPS):
            b = QPushButton(f"  {i + 1}   {self.tr(s)}")
            b.setObjectName("Step")
            b.setCheckable(True)
            b.setEnabled(False)
            rl.addWidget(b)
            self.step_btns.append(b)
        rl.addSpacing(20)
        self.rail_accounts = AccountsRail()
        self.rail_accounts.jobs = self.jobs
        self.rail_accounts.picked.connect(self._rail_pick)
        self.jobs.progress.connect(self.rail_accounts.update_progress)
        self.jobs.changed.connect(self.refresh_accounts)
        self.jobs.changed.connect(self.case_changed.emit)
        self.rail_accounts.setVisible(False)
        rl.addWidget(self.rail_accounts)
        rl.addStretch(1)
        lay.addWidget(rail)
        self._rail_timer = QTimer(self)
        self._rail_timer.timeout.connect(self.refresh_accounts)
        self._rail_timer.start(3000)
        self.stack = QStackedWidget()
        lay.addWidget(self.stack, 1)
        st = self.st
        self.p_case, self.p_acc, self.p_fold = CasePage(st), AccountPage(st), FolderPage(st)
        self.p_acq, self.p_fin = AcquirePage(st), FinishPage(st)
        for p in (self.p_case, self.p_acc, self.p_fold, self.p_acq, self.p_fin):
            self.stack.addWidget(p)
        self.p_case.next.connect(self._after_case)
        self.p_acc.back.connect(lambda: self._show(0, 0))
        self.p_acc.next.connect(self._after_account)
        self.p_fold.back.connect(lambda: self._show(1, 2))
        self.p_fold.next.connect(lambda: (self._show(3, 5), self.p_acq.start()))
        self.p_acq.another.connect(self._another)
        self.p_acq.finish.connect(self._finish)
        self.p_acq.finished.connect(lambda _: self.case_changed.emit())
        self.p_fin.new_case.connect(self._new_case)
        self.p_fin.retry.connect(self._finish)
        self.p_fin.back.connect(self._another)
        self.p_acc.finish.connect(self._finish)
        self.p_acc.new_account.connect(self._another)
        self.rail_accounts.add_account.connect(self._rail_add)
        self._show(0, 0)

    def _show(self, page_index: int, step: int) -> None:
        if page_index == 0:
            self.p_case.sync()
        self.stack.setCurrentIndex(page_index)
        for i, b in enumerate(self.step_btns):
            b.setChecked(i == step)
            b.setText(f"  {'✓' if i < step else i + 1}   {self.tr(STEPS[i])}")

    def refresh_accounts(self) -> None:
        try:
            self.rail_accounts.refresh(self.st.case)
        except Exception:  # Anzeige darf eine laufende Sicherung nie stören
            pass

    def show_job(self, job) -> None:
        self.p_acq.show_job(job)
        self._show(3, 5)

    def _after_account(self) -> None:
        job = self.jobs.get(self.st.account_id)
        if job is not None and job.running:  # dasselbe Konto läuft schon im Hintergrund
            QMessageBox.information(self, "FED", self.tr("Dieses Konto wird gerade gesichert – hier ist der "
                                                         "Fortschritt."))
            self.show_job(job)
            return
        self._show(2, 4)
        self.p_fold.scan()

    def _rail_pick(self, o: dict) -> None:
        """Klick auf ein Konto in der linken Leiste: laufende Sicherung ansehen, sonst Konto übernehmen."""
        if self.st.case is None or self.st.case.closed:
            return
        job = self.jobs.get(o["id"])
        if job is not None and job.running:
            self.show_job(job)
            return
        self.st.account_id = None
        self.st.oauth_session = None
        self.p_acc.reset()
        self.p_acc.prepare()
        self.p_acc._pick(o)
        self._show(1, 2)

    def _rail_add(self) -> None:
        """„+ Weiteres Konto“ in der linken Leiste."""
        if self.st.case is None or self.st.case.closed:
            return
        self._another()

    def _finish(self) -> None:
        running = self.jobs.running()
        if running:
            QMessageBox.information(self, self.tr("Fall abschließen"), self.tr(
                "Es laufen noch {0} Sicherungen:\n\n{1}\n\nDer Fall kann erst abgeschlossen werden, wenn alle "
                "beendet sind.").format(len(running), "\n".join(f"⟳  {j.evidence} · {j.email} – {j.percent} %"
                                                                 for j in running)))
            return
        open_ = incomplete(self.st.case) if self.st.case else []
        if open_:
            lines = "\n".join(f"{state_text(o)[0]}  {o['evidence']} · {o['email']} – {state_text(o)[1]}" for o in open_)
            r = QMessageBox.question(self, self.tr("Fall abschließen"), self.tr(
                "Nicht alle Konten sind vollständig gesichert:\n\n{0}\n\nNach dem Abschluss kann der Fall nicht mehr "
                "ergänzt werden. Trotzdem abschließen?").format(lines))
            if r != QMessageBox.StandardButton.Yes:
                return
        case = self.st.case
        n = case.conn.execute("SELECT COUNT(*) FROM account").fetchone()[0] if case else 0
        if n > 1 and case.zip_mode() is None:
            if ask_zip_mode(self, case, self.st.cfg) is None:
                return  # „Später entscheiden“ beim Abschluss = nicht abschließen
        self._show(4, 6)
        self.p_fin.run()

    def _after_case(self) -> None:
        self.case_changed.emit()
        self.refresh_accounts()
        self.p_acc.prepare()
        if self.st.case.closed:
            QMessageBox.information(self, self.tr("Fall abgeschlossen"),
                                    self.tr("Dieser Fall ist abgeschlossen und kann nur noch in der Historie angesehen werden."))
            return
        self._show(1, 2)

    def _another(self) -> None:
        self.st.account_id = None
        self.st.oauth_session = None
        p = self.p_acc
        p.reset()
        p.prepare()
        self.refresh_accounts()
        self._show(1, 2)

    def _new_case(self) -> None:
        if self.st.case:
            self.st.case.close()
        cfg = self.st.cfg
        self.st.__init__(cfg)
        self.st.jobs = self.jobs
        self.st.vault = self.vault  # Anmeldedaten gelten für die ganze Sitzung, auch im neuen Case
        self.jobs.clear()
        self.p_case.case_no.setText(self.p_case._suggest())
        self.p_case.notes.clear()
        self._another()
        self._show(0, 0)
        self.case_changed.emit()
