"""Mail-Betrachter (nur lesend): Kopfdaten, Text, Anlagen, Kopfzeilen, Weg, Echtheit."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QDialog, QFileDialog, QHBoxLayout, QHeaderView, QLabel,
                               QMessageBox, QPlainTextEdit, QPushButton, QTableWidget, QTableWidgetItem, QTabWidget,
                               QTextBrowser, QVBoxLayout, QWidget)

from fed.core import timefmt as tf
from fed.core.case import Case
from fed.core.report import _fmt_bytes
from fed.core.viewer import MailView, auth_summary, export_attachment, open_message
from fed.gui.widgets import ElidedLabel, KeyValues, restyle


class _SafeBrowser(QTextBrowser):
    """Zeigt HTML ohne externe Inhalte: keine Bilder/Stylesheets aus dem Netz, Links öffnen nicht."""

    def __init__(self) -> None:
        super().__init__()
        self.setOpenLinks(False)
        self.setOpenExternalLinks(False)

    def loadResource(self, kind, url: QUrl):
        return None  # nichts nachladen – weder http(s) noch file:// noch cid:


def _table(cols: list[str]) -> QTableWidget:
    t = QTableWidget(0, len(cols))
    t.setHorizontalHeaderLabels(cols)
    t.verticalHeader().setVisible(False)
    t.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    t.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    t.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    t.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
    t.horizontalHeader().setStretchLastSection(True)
    return t


class MailViewer(QDialog):
    def __init__(self, case: Case, message_id: int, parent=None) -> None:
        super().__init__(parent)
        self.case = case
        self.view: MailView = open_message(case, message_id)
        v = self.view
        self.setWindowTitle(self.tr("Mail ansehen – {0}").format(v.subject or self.tr("(ohne Betreff)")))
        self.resize(960, 720)

        head = KeyValues()
        head.set(self.tr("Betreff"), v.subject or "–")
        head.set(self.tr("Von"), v.from_ or "–")
        head.set(self.tr("An"), v.to or "–")
        if v.cc:
            head.set("CC", v.cc)
        head.set(self.tr("Datum (Kopfzeile)"), v.date or "–")
        head.set(self.tr("Eingang (Server)"), tf.both(v.db["internaldate"]))
        head.set(self.tr("Konto · Ordner · UID"), f"{v.db['evidence']} · {v.db['account']} · {v.db['folder']} · "
                                                     f"UID {v.db['uid']}")
        integrity = ElidedLabel("", "Ok" if v.sha256_ok else "Error")
        src = self.tr("Arbeitsordner") if v.source == "work" else v.source
        integrity.setText((self.tr("✓ SHA-256 stimmt mit der Case-Datei überein") if v.sha256_ok
                           else self.tr("✗ SHA-256 weicht von der Case-Datei ab – Datei verändert!"))
                          + f" · {self.tr('Quelle')}: {src} · {v.db['zip_path']}")
        restyle(integrity, "Ok" if v.sha256_ok else "Error")
        note = QLabel(self.tr("Nur lesend – die Datei wird nicht verändert. Ansehen und Speichern werden protokolliert."))
        note.setObjectName("Hint")

        tabs = QTabWidget()
        tabs.addTab(self._content_tab(), self.tr("Inhalt"))
        tabs.addTab(self._attachments_tab(), self.tr("Anlagen ({0})").format(len(v.attachments)))
        tabs.addTab(self._headers_tab(), self.tr("Kopfzeilen"))
        tabs.addTab(self._route_tab(), self.tr("Weg (Received)"))
        tabs.addTab(self._auth_tab(), self.tr("Echtheit"))

        close = QPushButton(self.tr("Schließen"))
        close.clicked.connect(self.accept)
        bottom = QHBoxLayout()
        bottom.addWidget(note, 1)
        bottom.addWidget(close)
        lay = QVBoxLayout(self)
        lay.addWidget(head)
        lay.addWidget(integrity)
        lay.addWidget(tabs, 1)
        lay.addLayout(bottom)

    # ------------------------------------------------------------ Tabs
    def _content_tab(self) -> QWidget:
        v = self.view
        w = QWidget()
        lay = QVBoxLayout(w)
        self.body = _SafeBrowser()
        self.show_html = QCheckBox(self.tr("HTML-Darstellung (ohne externe Inhalte)"))
        self.show_html.setEnabled(bool(v.html))
        self.show_html.toggled.connect(self._render_body)
        lay.addWidget(self.show_html)
        lay.addWidget(self.body, 1)
        self.show_html.setChecked(bool(v.html) and not v.text)
        self._render_body()
        return w

    def _render_body(self, *_) -> None:
        v = self.view
        if self.show_html.isChecked() and v.html:
            self.body.setHtml(v.html)
        else:
            self.body.setPlainText(v.text or (self.tr("(nur HTML-Inhalt – Häkchen oben setzen)") if v.html
                                              else self.tr("(kein Text)")))

    def _attachments_tab(self) -> QWidget:
        v = self.view
        w = QWidget()
        lay = QVBoxLayout(w)
        self.att = _table([self.tr("Name"), self.tr("Typ"), self.tr("Größe"), self.tr("eingebettet"), "SHA-256"])
        self.att.setRowCount(len(v.attachments))
        for r, a in enumerate(v.attachments):
            for c, val in enumerate([a.name, a.mime, _fmt_bytes(a.size), self.tr("ja") if a.inline else "", a.sha256]):
                self.att.setItem(r, c, QTableWidgetItem(val))
        save = QPushButton(self.tr("Anlage speichern unter …"))
        save.setEnabled(bool(v.attachments))
        save.clicked.connect(self._save_attachment)
        self.att.doubleClicked.connect(lambda *_: self._save_attachment())
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(save)
        lay.addWidget(self.att, 1)
        lay.addLayout(row)
        return w

    def _save_attachment(self) -> None:
        r = self.att.currentRow()
        if r < 0:
            r = 0
        if not self.view.attachments:
            return
        a = self.view.attachments[r]
        target, _ = QFileDialog.getSaveFileName(self, self.tr("Anlage speichern"), str(Path.home() / a.name))
        if not target:
            return
        sha = export_attachment(self.case, self.view, a.index, Path(target))
        ok = sha == a.sha256
        (QMessageBox.information if ok else QMessageBox.warning)(
            self, "FED", (self.tr("Gespeichert, SHA-256 geprüft:\n{0}") if ok
                          else self.tr("Gespeichert, aber SHA-256 weicht ab:\n{0}")).format(sha))

    def _headers_tab(self) -> QWidget:
        t = QPlainTextEdit()
        t.setReadOnly(True)
        t.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        t.setPlainText("\n".join(f"{k}: {val}" for k, val in self.view.headers))
        t.setStyleSheet("font-family: monospace;")
        return t

    def _route_tab(self) -> QWidget:
        hops = self.view.hops
        t = _table([self.tr("Nr."), self.tr("von"), self.tr("an Server"), self.tr("Protokoll"),
                    self.tr("Zeit (UTC / lokal)"), self.tr("Dauer")])
        t.setRowCount(len(hops))
        for r, h in enumerate(hops):
            when = tf.both(h.time.isoformat()) if h.time else "–"
            delay = "" if h.delay_s is None else f"{h.delay_s:+.0f} s"
            for c, val in enumerate([str(h.nr), h.from_, h.by, h.with_, when, delay]):
                it = QTableWidgetItem(val)
                it.setToolTip(h.raw)
                t.setItem(r, c, it)
            if h.delay_s is not None and h.delay_s < 0:
                t.item(r, 5).setForeground(Qt.GlobalColor.red)
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.addWidget(t, 1)
        hint = QLabel(self.tr("Älteste Station oben (Absender-Seite). Nur die Einträge der eigenen Server des "
                              "Anbieters sind verlässlich; frühere Stationen kann der Absender fälschen. "
                              "Negative Dauer = Uhren der Server weichen ab."))
        hint.setObjectName("Hint")
        hint.setWordWrap(True)
        lay.addWidget(hint)
        return w

    def _auth_tab(self) -> QWidget:
        s = auth_summary(self.view)
        kv = KeyValues()
        names = {"pass": self.tr("gültig"), "fail": self.tr("ungültig"), "permerror": self.tr("nicht prüfbar"),
                 "temperror": self.tr("vorläufig nicht prüfbar (DNS)"), "none": self.tr("nicht signiert"),
                 None: self.tr("nicht geprüft")}
        kv.set(self.tr("DKIM (geprüft bei der Sicherung)"), names.get(s["dkim"], s["dkim"]),
               {"pass": "ok", "fail": "error"}.get(s["dkim"] or ""))
        if s["domain"]:
            kv.set(self.tr("Signiert von"), f"{s['domain']} (Selector {s['selector']})")
        if s["detail"]:
            kv.set(self.tr("Begründung"), s["detail"])
        p = s["provider"]
        if p:
            kv.set(self.tr("Prüfung durch den Anbieter"), p.get("authserv", "–"))
            for k in ("spf", "dkim", "dmarc", "arc"):
                if k in p:
                    kv.set(k.upper(), p[k], "ok" if p[k] == "pass" else "warn")
        else:
            kv.set(self.tr("Prüfung durch den Anbieter"), self.tr("keine Authentication-Results-Kopfzeile"))
        labels = self.view.db.get("gm_labels")
        if labels:
            import json

            kv.set(self.tr("Gmail-Labels"), ", ".join(json.loads(labels)))
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.addWidget(kv)
        lay.addStretch(1)
        return w
