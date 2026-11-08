"""Assistent „Sicherung“: Case → Konto → Ordner → Sicherung → Abschluss."""

from __future__ import annotations

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
from fed.core.auth import APP_PASSWORD_URL, OAuthSession, is_gmail, log_oauth_login, make_connector, normalize_app_password
from fed.core.case import Case, CaseInfo
from fed.core.config import Config
from fed.core.finalize import finish_case
from fed.core.naming import next_evidence, normalize_email
from fed.gui.accounts import AccountsRail, AccountsTable, incomplete, state_text
from fed.core.report import _fmt_bytes
from fed.gui.widgets import Card, ElidedLabel, KeyValues, PasswordField, Segmented, label, restyle, run_async, vbox


UROLE = Qt.ItemDataRole.UserRole
CHECKED, UNCHECKED = Qt.CheckState.Checked, Qt.CheckState.Unchecked
RIGHT = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter


def stay_awake(st, what: str) -> None:
    if not st.cfg.programm.wach_halten:
        return
    method = keepawake.shared().acquire()
    if st.case:
        st.case.log("INFO" if method else "WARNING", "keep_awake_on",
                    {"for": what, "method": method or "nicht verfügbar"})


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
class CasePage(QWidget):
    next = Signal()

    def __init__(self, st: State) -> None:
        super().__init__()
        self.st = st
        w, lay = page(self.tr("Case anlegen"),
                      self.tr("Ohne Case startet keine Sicherung. Examiner und Agentur kommen aus der config.toml."))
        cols = QHBoxLayout()
        cols.setSpacing(24)
        form_card = Card()
        f = QGridLayout()
        f.setHorizontalSpacing(16)
        f.setVerticalSpacing(6)
        self.case_no = QLineEdit(self._suggest())
        self.examiner = QLineEdit(st.cfg.examiner_name)
        self.notes = QPlainTextEdit()
        self.notes.setPlaceholderText(self.tr("z. B. Auftrag, Aktenzeichen, Beschluss"))
        self.notes.setFixedHeight(90)
        self.target = QLineEdit(st.cfg.programm.zielpfad or str(Path.cwd()))
        browse = QPushButton(self.tr("Durchsuchen …"))
        browse.clicked.connect(self._browse)
        f.addWidget(label(self.tr("Casenumber *"), "H2"), 0, 0)
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
        open_btn = QPushButton(self.tr("Vorhandenen Case öffnen …"))
        open_btn.clicked.connect(self._open_existing)
        self.go = QPushButton(self.tr("Case anlegen und weiter"))
        self.go.setObjectName("Primary")
        self.go.clicked.connect(self._create)
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
        pattern = self.st.cfg.case_nummer_muster or "{jahr}-{lfd:04}"
        try:
            return pattern.format(jahr=datetime.now().year, lfd=1)
        except (KeyError, ValueError, IndexError):
            return ""

    def _browse(self) -> None:
        d = QFileDialog.getExistingDirectory(self, self.tr("Zielordner wählen"), self.target.text())
        if d:
            self.target.setText(d)
            self.check_system()

    def _update_hint(self) -> None:
        from fed.core.naming import sanitize_component

        self.target_hint.setText(self.tr("Case-Ordner wird angelegt: ")
                                 + str(Path(self.target.text()) / sanitize_component(self.case_no.text() or "…")))

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
            self.error.setText(self.tr("Casenumber und Examiner sind Pflicht."))
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

    def _create(self) -> None:
        if not self._validate():
            return
        try:
            case = Case.create(Path(self.target.text()), CaseInfo(self.case_no.text(), self.examiner.text(),
                                                                   self.notes.toPlainText()))
        except (FileExistsError, ValueError, OSError) as e:
            self.error.setText(str(e))
            return
        try:
            case.store_config_snapshot(self.st.cfg.raw_text, self.st.cfg.logo_path())
        except ValueError as e:
            case.log("WARNING", "logo_rejected", str(e))
        if self.st.system:
            case.record_system_check(self.st.system.as_dict())
            if self.st.system.target_removable is not True:
                case.log("WARNING", "target_not_external_confirmed", {"path": self.st.system.target_path})
        self.st.case = case
        self.next.emit()

    def _open_existing(self) -> None:
        d = QFileDialog.getExistingDirectory(self, self.tr("Case-Ordner wählen"), self.target.text())
        if not d:
            return
        try:
            self.st.case = Case.open(Path(d))
        except (FileNotFoundError, OSError) as e:
            self.error.setText(str(e))
            return
        self.next.emit()


# ====================================================================== Schritt 3
class AccountPage(QWidget):
    next = Signal()
    back = Signal()

    def __init__(self, st: State) -> None:
        super().__init__()
        self.st = st
        self.found: autoconfig.ImapSettings | None = None
        w, lay = page(self.tr("Konto einrichten"),
                      self.tr("Adresse eingeben – Server, Port und Verschlüsselung werden wie bei Thunderbird "
                              "automatisch ermittelt."))
        self.accounts_card = Card(self.tr("Bereits in diesem Case"))
        self.accounts = AccountsTable()
        self.accounts_card.add(self.accounts)
        self.accounts_card.add(label(self.tr("Doppelklick auf ein Konto übernimmt es zum Fortsetzen; für ein neues "
                                             "Konto unten die Adresse eingeben."), "Hint", wrap=True))
        self.accounts.picked.connect(self._pick)
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
        g.addWidget(label(self.tr("Evidence-Nummer *"), "H2"), 0, 1)
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
        self.device = QCheckBox(self.tr("Anmeldung per Gerätecode (anderes Gerät)"))
        self.login_as = QLineEdit()
        self.login_as.setPlaceholderText(self.tr("anmelden als (nur bei geteiltem Postfach)"))
        for x in (self.pw_label, self.password, self.pw_hint, self.oauth_hint, self.device, self.login_as):
            c2.add(x)
        left.addWidget(c2)
        cn = Card(self.tr("Notiz zum Konto"))
        self.acc_notes = QPlainTextEdit()
        self.acc_notes.setPlaceholderText(self.tr("z. B. Herkunft der Zugangsdaten, Kontoinhaber, Einwilligung/Beschluss, "
                                                  "Besonderheiten – erscheint im Bericht"))
        self.acc_notes.setFixedHeight(80)
        cn.add(self.acc_notes)
        left.addWidget(cn)
        cols.addLayout(left, 3)

        c3 = Card(self.tr("Verbindungstest"))
        self.test_state = label(self.tr("noch nicht getestet"), "Muted")
        c3.add(self.test_state)
        self.test = KeyValues()
        c3.add(self.test)
        c3.lay.addStretch(1)
        cols.addWidget(c3, 2)
        lay.addLayout(cols)
        lay.addStretch(1)
        self.error = label("", "Error", wrap=True)
        lay.addWidget(self.error)
        back = QPushButton(self.tr("Zurück"))
        back.clicked.connect(self.back.emit)
        self.test_btn = QPushButton(self.tr("Verbindung testen"))
        self.test_btn.clicked.connect(self._test)
        self.go = QPushButton(self.tr("Postfach scannen"))
        self.go.setObjectName("Primary")
        self.go.setEnabled(False)
        self.go.clicked.connect(self._go)
        b = QHBoxLayout()
        b.addWidget(back)
        b.addStretch(1)
        b.addWidget(self.test_btn)
        b.addWidget(self.go)
        lay.addLayout(b)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll(w))
        self._method_changed("password")
        for x in (self.server, self.user, self.password):
            x.textChanged.connect(lambda *_: self.go.setEnabled(False))

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
        self.go.setEnabled(False)
        self.test.clear()
        self.test_state.setText(self.tr("noch nicht getestet"))
        restyle(self.test_state, "Muted")
        self.discover_info.setText(self.tr("Einstellungen werden nach Eingabe der Adresse gesucht."))
        self.error.setText("")

    def prepare(self) -> None:
        """Vor dem Einrichten eines (weiteren) Kontos: Übersicht zeigen, Evidence-Nummer vorschlagen."""
        rows = self.accounts.refresh(self.st.case)
        self.accounts_card.setVisible(bool(rows))
        suggestion = next_evidence([o["evidence"] for o in rows])
        if suggestion and not self.evidence.text().strip():
            self.evidence.setText(suggestion)
            self.evidence.selectAll()
            self.evidence_hint.setText(self.tr("Evidence-Nummer vorgeschlagen (nach {0}) – bitte prüfen.")
                                       .format(rows[-1]["evidence"]))
        else:
            self.evidence_hint.setText("")
        self.evidence_hint.setVisible(bool(self.evidence_hint.text()))

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
        self.acc_notes.setPlainText(o.get("notes", ""))
        self._check_known()
        self.password.setFocus()

    def _known(self, email: str) -> dict | None:
        return next((o for o in self.accounts.rows if o["email"].casefold() == email.casefold()), None)

    def _check_known(self) -> None:
        o = self._known(normalize_email(self.email.text()))
        if o:
            sym, text, _ = state_text(o)
            self.evidence_hint.setText(self.tr("Dieses Konto ist schon im Case ({0}, {1} {2}) – es wird fortgesetzt, "
                                               "die Evidence-Nummer bleibt {0}.").format(o["evidence"], sym, text))
            self.evidence.setText(o["evidence"])
            self.evidence_hint.setVisible(True)

    def _method_changed(self, m: str) -> None:
        pw = m in ("password", "google_app")
        for x in (self.pw_label, self.password, self.pw_hint):
            x.setVisible(pw)
        self.pw_label.setText(self.tr("App-Passwort") if m == "google_app" else self.tr("Passwort"))
        self.pw_hint.setText(
            self.tr("16 Buchstaben aus {0} (erfordert 2-Schritt-Bestätigung; Leerzeichen egal). Das normale "
                    "Google-Passwort funktioniert nicht. Nur im Arbeitsspeicher – wird nie gespeichert oder geloggt.")
            .format(APP_PASSWORD_URL) if m == "google_app"
            else self.tr("Nur im Arbeitsspeicher – wird nie gespeichert oder geloggt."))
        self.pw_hint.setWordWrap(True)
        for x in (self.oauth_hint, self.login_as):
            x.setVisible(not pw)
        self.device.setVisible(m == "microsoft")
        cid = {"google": self.st.cfg.oauth_google.client_id, "microsoft": self.st.cfg.oauth_microsoft.client_id}.get(m)
        self.oauth_hint.setText(
            self.tr("Beim Test öffnet sich der Browser zur Anmeldung. Das Passwort sieht FED nicht.")
            if cid else self.tr("Keine Client-ID in den Einstellungen – siehe docs/oauth-einrichtung.md"))
        restyle(self.oauth_hint, "Hint" if cid else "Warn")
        self.go.setEnabled(False)

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
                self.discover_info.setText(self.tr("Einstellungen gefunden – Quelle: ") + f"{s.source} "
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

    def _test(self) -> None:
        self.error.setText("")
        if not normalize_email(self.email.text()) or not self.evidence.text().strip() or not self.server.text().strip():
            self.error.setText(self.tr("E-Mail-Adresse, Evidence-Nummer und Server sind Pflicht."))
            return
        ev, email = self.evidence.text().strip(), normalize_email(self.email.text())
        other = next((o for o in self.accounts.rows
                      if o["evidence"].casefold() == ev.casefold() and o["email"].casefold() != email.casefold()), None)
        if other:
            self.error.setText(self.tr("Die Evidence-Nummer {0} ist im Case schon für {1} vergeben.")
                               .format(ev, other["email"]))
            return
        m = self.method.value()
        self.test_btn.setEnabled(False)
        self.test_state.setText(self.tr("teste …"))
        restyle(self.test_state, "Muted")
        cfg = self.st.cfg
        # alle Eingaben im GUI-Thread lesen – der Worker fasst keine Widgets an
        host, port, sec = self.server.text().strip(), self.port.value(), self.security.currentText()
        user = normalize_email(self.user.text()) or normalize_email(self.email.text())
        password = normalize_app_password(self.password.text()) if m == "google_app" else self.password.text()
        hint = normalize_email(self.login_as.text()) or normalize_email(self.email.text())
        device = self.device.isChecked()
        timeout = cfg.programm.socket_timeout_s
        st = self.st

        def work(cancel, report):
            if m in ("password", "google_app"):
                connect = make_connector(host, port, sec, user, password=password, timeout=timeout)
            else:
                prov = oauth.GOOGLE if m == "google" else oauth.microsoft(cfg.oauth_microsoft.tenant)
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
        self.go.setEnabled(True)

    def _login_hint(self, err: str) -> str:
        """Hinweis bei abgelehnter Anmeldung mit Passwort/App-Passwort (übersetzbar)."""
        low = err.lower()
        if not any(k in low for k in ("authenticationfailed", "authentication failed", "invalid credentials",
                                      "login failed", "logon failure", "auth", "credentials")):
            return ""
        m = self.method.value()
        if m not in ("password", "google_app"):
            return ""
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
        self.test_state.setText(self.tr("✗ fehlgeschlagen: ") + err + (f"\n\n{hint}" if hint else ""))
        self.test_state.setWordWrap(True)
        restyle(self.test_state, "Error")
        self.go.setEnabled(False)

    def _go(self) -> None:
        self.password.hide_text()
        self.st.email = normalize_email(self.email.text())
        self.st.connect = self._connector()
        case = self.st.case
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
            self.found.source if self.found else "manuell", notes=self.acc_notes.toPlainText())
        if self.st.account_id is not None:
            acq.set_account_notes(case, self.st.account_id, self.acc_notes.toPlainText())
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
            "die Labels jeder Mail werden dokumentiert (Case-Datei, log.csv, Bericht)."), "Hint", wrap=True)
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
        self.tree.setMinimumHeight(360)
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
        b = QHBoxLayout()
        b.addWidget(back)
        b.addStretch(1)
        b.addWidget(self.go)
        lay.addLayout(b)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(w)

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
        self.worker = None
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
        self.accounts_card = Card(self.tr("Konten in diesem Case"))
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
        self.another_btn = QPushButton(self.tr("Weiteres Konto im Case sichern"))
        self.finish_btn = QPushButton(self.tr("Case abschließen"))
        self.finish_btn.setObjectName("Primary")
        self.resume_btn.clicked.connect(lambda: self.start(acq.Mode.RESUME))
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

    def start(self, mode=acq.Mode.RESUME, reason: str = "", delete_old: bool = False) -> None:
        st = self.st
        for x in (self.resume_btn, self.restart_btn, self.another_btn, self.finish_btn):
            x.setVisible(False)
        self.cancel_btn.setVisible(True)
        self._show_accounts()
        self.title.setText(self.tr("Sicherung läuft"))
        now = datetime.now(UTC)
        self.sub.setText(self.tr("Modus {0} · gestartet {1} UTC ({2}) · {3} Verbindungen · nur lesend "
                                 "(EXAMINE, BODY.PEEK)").format(
            self.tr("Fortsetzen") if mode is acq.Mode.RESUME else self.tr("Neubeginn"),
            now.strftime("%H:%M:%S"), tf.local_str(now, with_date=False) or "–", st.cfg.programm.verbindungen))
        self.sub.setVisible(True)
        self.bar.setValue(0)
        self._last_log = 0
        self.timer.start(1000)

        def work(cancel, report):
            return acq.run_attempt(st.case, st.account_id, st.connect, mode, cancel, report, reason, delete_old, st.cfg)

        stay_awake(st, "sicherung")
        self.worker = run_async(work, self._done, self._failed, self._progress, parent=self)

    def _progress(self, p) -> None:
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
        rows = self.st.case.conn.execute("SELECT id, ts_utc, level, event, details FROM log WHERE id > ? ORDER BY id",
                                         (getattr(self, "_last_log", 0),)).fetchall()
        for id_, ts, level, event, details in rows:
            self._last_log = id_
            self.log.appendPlainText(f"{ts[11:19]} {level[:4]:<4} {event} {details[:90]}")
        self._tick = getattr(self, "_tick", 0) + 1
        if self._tick % 3 == 0:
            self._show_accounts()

    def _cancel(self) -> None:
        if self.worker:
            self.worker.cancel.cancel()
            self.cancel_btn.setEnabled(False)
            self.sub.setText(self.tr("Wird abgebrochen …"))

    def _done(self, res) -> None:
        allow_sleep(self.st)
        self.timer.stop()
        self._tail_log()
        self.cancel_btn.setEnabled(True)
        self.cancel_btn.setVisible(False)
        self.st.attempts.append(res)
        txt = {"done": self.tr("Sicherung abgeschlossen"), "cancelled": self.tr("Sicherung abgebrochen"),
               "failed": self.tr("Sicherung fehlgeschlagen")}[res.status]
        self.title.setText(txt)
        self.sub.setText(self.tr("Versuch {0}: {1} gesichert · {2} bereits vorhanden · erwartet {3}").format(
            res.nr, res.saved, res.skipped_existing, res.expected) + ("\n" + "\n".join(res.warnings) if res.warnings else ""))
        if res.status == "done":
            self.bar.setValue(100)
            self.pct.setText("100 %")
        self.resume_btn.setVisible(res.status != "done")
        self.restart_btn.setVisible(True)
        self.another_btn.setVisible(res.status == "done")
        self.finish_btn.setVisible(res.status == "done")
        self._show_accounts()
        self.finished.emit(res)

    def _change_zip_mode(self) -> None:
        ask_zip_mode(self, self.st.case, self.st.cfg, force=True)
        self._show_accounts()

    def _show_accounts(self) -> None:
        rows = self.accounts.refresh(self.st.case, self.st.email)
        self.accounts_card.setVisible(len(rows) > 1 or self.cancel_btn.isHidden())
        self.zip_lbl.setText(self.tr("ZIP-Archive am Ende: {0}").format(zip_mode_text(self.st.case.zip_mode())))
        for x in (self.zip_lbl, self.zip_btn):
            x.setVisible(len(rows) > 1)
        self.zip_btn.setEnabled(self.cancel_btn.isHidden())
        cur = next((o for o in rows if o["email"] == self.st.email), None)
        if cur:
            nr = rows.index(cur) + 1
            self.account_lbl.setText(self.tr("Konto {0} von {1}: {2} · {3}").format(
                nr, len(rows), cur["evidence"], cur["email"]))

    def _failed(self, err: str, tb: str) -> None:
        allow_sleep(self.st)
        self.timer.stop()
        self._tail_log()
        self.cancel_btn.setVisible(False)
        self.title.setText(self.tr("Sicherung fehlgeschlagen"))
        self.sub.setText(err)
        restyle(self.sub, "Error")
        self._show_accounts()
        self.resume_btn.setVisible(True)
        self.restart_btn.setVisible(True)

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
        self.start(acq.Mode.RESTART, reason.strip(), delete)


# ====================================================================== Schritt 7
class FinishPage(QWidget):
    new_case = Signal()

    def __init__(self, st: State) -> None:
        super().__init__()
        self.st = st
        w, lay = page(self.tr("Case abschließen"))
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
        self.open_dir = QPushButton(self.tr("Case-Ordner öffnen"))
        self.open_pdf.clicked.connect(lambda: self._open(self.st.finish.report_pdf))
        self.open_dir.clicked.connect(lambda: self._open(self.st.case.root))
        rep.add(self.open_pdf)
        rep.add(self.open_dir)
        rep.lay.addStretch(1)
        cols.addWidget(rep, 2)
        lay.addLayout(cols)
        lay.addStretch(1)
        nb = QPushButton(self.tr("Neuen Case anlegen"))
        nb.clicked.connect(self.new_case.emit)
        self.quit_btn = QPushButton(self.tr("FED beenden"))
        self.quit_btn.setObjectName("Primary")
        self.quit_btn.clicked.connect(lambda: self.window().close())
        nr = QHBoxLayout()
        nr.addStretch(1)
        nr.addWidget(nb)
        nr.addWidget(self.quit_btn)
        lay.addLayout(nr)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll(w))

    @staticmethod
    def _open(path) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def run(self) -> None:
        st = self.st
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
                return  # Abbrechen = nicht abschließen
            password = dlg.password
            dlg.deleteLater()
        self._password = password
        self.title.setText(self.tr("Case wird abgeschlossen …"))
        self.sub.setText(self.tr("ZIP packen, prüfen, Hashes und Berichte erzeugen"))
        self.sub.setVisible(True)
        self.open_pdf.setEnabled(False)
        self.check_kv.clear()

        def work(cancel, report):
            return finish_case(st.case, st.cfg, cancel=cancel, progress=report, delete_work=delete,
                               encrypt_password=password)

        def progress(p):
            self.check_kv.set(self.tr("Fortschritt"), f"{p.phase}: {p.mails_done} / {p.mails_total}")

        stay_awake(st, "abschluss")
        run_async(work, self._done, self._failed, progress, parent=self)

    def _done(self, res) -> None:
        allow_sleep(self.st)
        self.st.finish = res
        z = res.zip
        total = sum(r for (r,) in self.st.case.conn.execute("SELECT COUNT(*) FROM message WHERE status='valid'"))
        self.title.setText(self.tr("Case abgeschlossen"))
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
                  if z.verified else "; ".join(z.problems), "ok" if z.verified else "error")
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
                k.set(self.tr("Hinweis"), p, "warn")
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
        self.sub.setText(err)
        restyle(self.sub, "Error")


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
    box.setText(QCoreApplication.translate("Assistant", "Dieser Case enthält mehrere Konten. Wie sollen die ZIP-Archive "
                                                        "am Ende erstellt werden?"))
    box.setInformativeText(QCoreApplication.translate(
        "Assistant", "Ein ZIP für alle Konten: ein Archiv, ein Hash, je Konto ein Ordner darin.\n\n"
                     "Ein ZIP je Konto: je Evidence-Nummer ein eigenes Archiv mit eigenem Hash (und Zeitstempel) – "
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
STEPS = [QT_TRANSLATE_NOOP("Assistant", "Case anlegen"), QT_TRANSLATE_NOOP("Assistant", "Systemprüfung"),
         QT_TRANSLATE_NOOP("Assistant", "Konto"), QT_TRANSLATE_NOOP("Assistant", "Postfach scannen"),
         QT_TRANSLATE_NOOP("Assistant", "Ordner wählen"), QT_TRANSLATE_NOOP("Assistant", "Sicherung"),
         QT_TRANSLATE_NOOP("Assistant", "Abschluss")]


class Assistant(QWidget):
    case_changed = Signal()

    def __init__(self, cfg: Config) -> None:
        super().__init__()
        self.st = State(cfg)
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
        self.rail_accounts.picked.connect(self._rail_pick)
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
        self.p_acc.next.connect(lambda: (self._show(2, 4), self.p_fold.scan()))
        self.p_fold.back.connect(lambda: self._show(1, 2))
        self.p_fold.next.connect(lambda: (self._show(3, 5), self.p_acq.start()))
        self.p_acq.another.connect(self._another)
        self.p_acq.finish.connect(self._finish)
        self.p_acq.finished.connect(lambda _: self.case_changed.emit())
        self.p_fin.new_case.connect(self._new_case)
        self._show(0, 0)

    def _show(self, page_index: int, step: int) -> None:
        self.stack.setCurrentIndex(page_index)
        for i, b in enumerate(self.step_btns):
            b.setChecked(i == step)
            b.setText(f"  {'✓' if i < step else i + 1}   {self.tr(STEPS[i])}")

    def refresh_accounts(self) -> None:
        try:
            self.rail_accounts.refresh(self.st.case)
        except Exception:  # Anzeige darf eine laufende Sicherung nie stören
            pass

    def _rail_pick(self, o: dict) -> None:
        """Klick auf ein Konto in der linken Leiste: zum Fortsetzen auf die Kontoseite übernehmen."""
        w = self.p_acq.worker
        if w is not None and w.thread.isRunning():
            QMessageBox.information(self, "FED", self.tr("Während einer laufenden Sicherung kann kein anderes Konto "
                                                         "gewählt werden."))
            return
        if self.st.case is None or self.st.case.closed:
            return
        self.st.account_id = None
        self.st.oauth_session = None
        self.p_acc.reset()
        self.p_acc.prepare()
        self.p_acc._pick(o)
        self._show(1, 2)

    def _finish(self) -> None:
        open_ = incomplete(self.st.case) if self.st.case else []
        if open_:
            lines = "\n".join(f"{state_text(o)[0]}  {o['evidence']} · {o['email']} – {state_text(o)[1]}" for o in open_)
            r = QMessageBox.question(self, self.tr("Case abschließen"), self.tr(
                "Nicht alle Konten sind vollständig gesichert:\n\n{0}\n\nNach dem Abschluss kann der Case nicht mehr "
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
            QMessageBox.information(self, self.tr("Case abgeschlossen"),
                                    self.tr("Dieser Case ist abgeschlossen und kann nur noch in der Historie angesehen werden."))
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
        self.p_case.case_no.setText(self.p_case._suggest())
        self.p_case.notes.clear()
        self._another()
        self._show(0, 0)
        self.case_changed.emit()
