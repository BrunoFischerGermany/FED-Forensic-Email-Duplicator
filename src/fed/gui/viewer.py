"""Mail-Betrachter (nur lesend): Kopfdaten, Text, Anlagen, Kopfzeilen, Weg, Echtheit."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QT_TRANSLATE_NOOP, QBuffer, QByteArray, QCoreApplication, QIODevice, Qt, QUrl
from PySide6.QtGui import QImage, QPixmap, QTextDocument
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QDialog, QFileDialog, QHBoxLayout, QHeaderView, QLabel,
                               QMessageBox, QPlainTextEdit, QPushButton, QScrollArea, QSplitter, QStackedWidget,
                               QTableWidget, QTableWidgetItem, QTabWidget, QTextBrowser, QVBoxLayout, QWidget)

from fed.core import filecheck as fcheck
from fed.core import timefmt as tf
from fed.core.case import Case
from fed.core.report import _fmt_bytes
from fed.core.viewer import (MailView, auth_summary, check_attachment, export_attachment, inline_resource,
                             open_message)
from fed.gui.widgets import ElidedLabel, KeyValues, restyle
from fed.core.msg import describe

IMAGE_FORMATS = {"png", "jpeg", "gif", "bmp", "tiff", "webp", "ico"}
PREVIEW_LIMIT = 50 * 1024 * 1024  # größere Anlagen nur Prüfung + Hex


def _tr(text: str) -> str:
    return QCoreApplication.translate("MailViewer", text)


_tr_marker = QT_TRANSLATE_NOOP("MailViewer", "{0} Einträge")


# Texte der Befunde (Codes aus fed.core.filecheck)
FINDING_TEXT = {
    "executable": QT_TRANSLATE_NOOP("MailViewer", "Ausführbarer Inhalt: {0} – nicht öffnen"),
    "ext_mismatch": QT_TRANSLATE_NOOP("MailViewer", "Endung .{0} passt nicht zum Inhalt ({1}, üblich: {2})"),
    "mime_mismatch": QT_TRANSLATE_NOOP("MailViewer", "Angegebener Typ {0} weicht vom Inhalt ab ({1})"),
    "double_extension": QT_TRANSLATE_NOOP("MailViewer", "Doppelte Endung {0} – typische Tarnung"),
    "bidi_name": QT_TRANSLATE_NOOP("MailViewer", "Name enthält Steuerzeichen für die Schreibrichtung (verschleiert die Endung)"),
    "padded_name": QT_TRANSLATE_NOOP("MailViewer", "Viele Leerzeichen vor der Endung (verschleiert die Endung)"),
    "macros": QT_TRANSLATE_NOOP("MailViewer", "Enthält Makros (VBA)"),
    "remote_template": QT_TRANSLATE_NOOP("MailViewer", "Lädt eine Vorlage/ein Objekt aus dem Internet nach"),
    "embedded_objects": QT_TRANSLATE_NOOP("MailViewer", "Enthält eingebettete Objekte"),
    "pdf_active": QT_TRANSLATE_NOOP("MailViewer", "PDF mit aktiven Inhalten: {0}"),
    "pdf_embedded_file": QT_TRANSLATE_NOOP("MailViewer", "PDF enthält eingebettete Dateien"),
    "pdf_encrypted": QT_TRANSLATE_NOOP("MailViewer", "PDF ist verschlüsselt"),
    "pdf_offset": QT_TRANSLATE_NOOP("MailViewer", "PDF-Kennung erst ab Byte {0} (Daten davor)"),
    "zip_encrypted": QT_TRANSLATE_NOOP("MailViewer", "Archiv ist verschlüsselt – Inhalt nicht prüfbar"),
    "zip_damaged": QT_TRANSLATE_NOOP("MailViewer", "Archiv beschädigt oder unvollständig"),
    "archive_contains_executable": QT_TRANSLATE_NOOP("MailViewer", "Archiv enthält ausführbare Dateien: {0}"),
    "office_encrypted": QT_TRANSLATE_NOOP("MailViewer", "Office-Datei ist verschlüsselt"),
    "script": QT_TRANSLATE_NOOP("MailViewer", "Skript (.{0}) – wird beim Doppelklick ausgeführt"),
    "risky_ext": QT_TRANSLATE_NOOP("MailViewer", "Endung .{0} kann beim Öffnen Code ausführen"),
    "disk_image": QT_TRANSLATE_NOOP("MailViewer", "Datenträger-Abbild – häufig zum Verstecken von Schadsoftware genutzt"),
    "empty": QT_TRANSLATE_NOOP("MailViewer", "Leere Datei"),
    "high_entropy": QT_TRANSLATE_NOOP("MailViewer", "Hohe Entropie ({0} Bit/Byte) – verschlüsselt oder komprimiert"),
}

# Formatnamen aus fed.core.filecheck.FORMATS (für die Übersetzung; Test prüft Vollständigkeit)
FORMAT_NAMES = [
    QT_TRANSLATE_NOOP("MailViewer", "PDF-Dokument"),
    QT_TRANSLATE_NOOP("MailViewer", "PNG-Bild"),
    QT_TRANSLATE_NOOP("MailViewer", "JPEG-Bild"),
    QT_TRANSLATE_NOOP("MailViewer", "GIF-Bild"),
    QT_TRANSLATE_NOOP("MailViewer", "BMP-Bild"),
    QT_TRANSLATE_NOOP("MailViewer", "TIFF-Bild"),
    QT_TRANSLATE_NOOP("MailViewer", "WebP-Bild"),
    QT_TRANSLATE_NOOP("MailViewer", "Symbol (ICO)"),
    QT_TRANSLATE_NOOP("MailViewer", "HEIC-Bild"),
    QT_TRANSLATE_NOOP("MailViewer", "SVG-Grafik"),
    QT_TRANSLATE_NOOP("MailViewer", "Word-Dokument (OOXML)"),
    QT_TRANSLATE_NOOP("MailViewer", "Word-Dokument mit Makros"),
    QT_TRANSLATE_NOOP("MailViewer", "Excel-Tabelle (OOXML)"),
    QT_TRANSLATE_NOOP("MailViewer", "Excel-Tabelle mit Makros"),
    QT_TRANSLATE_NOOP("MailViewer", "PowerPoint (OOXML)"),
    QT_TRANSLATE_NOOP("MailViewer", "PowerPoint mit Makros"),
    QT_TRANSLATE_NOOP("MailViewer", "OpenDocument"),
    QT_TRANSLATE_NOOP("MailViewer", "Office 97–2003 / Outlook-Nachricht (OLE)"),
    QT_TRANSLATE_NOOP("MailViewer", "Java-Archiv"),
    QT_TRANSLATE_NOOP("MailViewer", "Android-App"),
    QT_TRANSLATE_NOOP("MailViewer", "ZIP-Archiv"),
    QT_TRANSLATE_NOOP("MailViewer", "RAR-Archiv"),
    QT_TRANSLATE_NOOP("MailViewer", "7-Zip-Archiv"),
    QT_TRANSLATE_NOOP("MailViewer", "GZIP-Archiv"),
    QT_TRANSLATE_NOOP("MailViewer", "BZIP2-Archiv"),
    QT_TRANSLATE_NOOP("MailViewer", "XZ-Archiv"),
    QT_TRANSLATE_NOOP("MailViewer", "Windows-Archiv (CAB)"),
    QT_TRANSLATE_NOOP("MailViewer", "Datenträger-Abbild (ISO)"),
    QT_TRANSLATE_NOOP("MailViewer", "Windows-Programm (PE)"),
    QT_TRANSLATE_NOOP("MailViewer", "Linux-Programm (ELF)"),
    QT_TRANSLATE_NOOP("MailViewer", "macOS-Programm (Mach-O)"),
    QT_TRANSLATE_NOOP("MailViewer", "Windows-Verknüpfung (LNK)"),
    QT_TRANSLATE_NOOP("MailViewer", "RTF-Dokument"),
    QT_TRANSLATE_NOOP("MailViewer", "MP3-Audio"),
    QT_TRANSLATE_NOOP("MailViewer", "MP4/QuickTime-Video"),
    QT_TRANSLATE_NOOP("MailViewer", "WAV-Audio"),
    QT_TRANSLATE_NOOP("MailViewer", "AVI-Video"),
    QT_TRANSLATE_NOOP("MailViewer", "Ogg-Audio/Video"),
    QT_TRANSLATE_NOOP("MailViewer", "FLAC-Audio"),
    QT_TRANSLATE_NOOP("MailViewer", "E-Mail (RFC 5322)"),
    QT_TRANSLATE_NOOP("MailViewer", "Kalendereintrag (iCalendar)"),
    QT_TRANSLATE_NOOP("MailViewer", "Kontakt (vCard)"),
    QT_TRANSLATE_NOOP("MailViewer", "HTML-Seite"),
    QT_TRANSLATE_NOOP("MailViewer", "HTML-Anwendung (HTA)"),
    QT_TRANSLATE_NOOP("MailViewer", "XML-Datei"),
    QT_TRANSLATE_NOOP("MailViewer", "PostScript"),
    QT_TRANSLATE_NOOP("MailViewer", "Text"),
    QT_TRANSLATE_NOOP("MailViewer", "leere Datei"),
    QT_TRANSLATE_NOOP("MailViewer", "unbekannt"),
]


def format_name(fmt: fcheck.Format, detail: str = "") -> str:
    name = _tr(fmt.name)
    if detail.isdigit():
        return name + " · " + _tr("{0} Einträge").format(detail)
    return name + (f" · {detail}" if detail else "")


def finding_text(f: fcheck.Finding) -> str:
    names = {x.name for x in fcheck.FORMATS.values()}
    args = [_tr(a) if isinstance(a, str) and a in names else a for a in f.args]
    return _tr(FINDING_TEXT.get(f.code, f.code)).format(*args)


class _SafeBrowser(QTextBrowser):
    """Zeigt HTML ohne externe Inhalte: nichts aus dem Netz, keine lokalen Dateien, Links öffnen nicht.
    Eingebettete Bilder (``cid:``) kommen nur aus der Mail selbst und nur, wenn sie wirklich Bilder sind."""

    def __init__(self, resolver=None) -> None:
        super().__init__()
        self.resolver = resolver
        self.setOpenLinks(False)
        self.setOpenExternalLinks(False)

    def loadResource(self, kind, url: QUrl):
        if (self.resolver and url.scheme().lower() == "cid"
                and kind == QTextDocument.ResourceType.ImageResource):
            data = self.resolver(url.toString())
            if data and fcheck.identify(data)[0].key in IMAGE_FORMATS:
                img = QImage.fromData(data)
                if not img.isNull():
                    return img
        return None


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
        integrity.setText((self.tr("✓ SHA-256 stimmt mit der Fall-Datei überein") if v.sha256_ok
                           else self.tr("✗ SHA-256 weicht von der Fall-Datei ab – Datei verändert!"))
                          + " · " + self.tr("Quelle: {0}").format(src) + f" · {v.db['zip_path']}")
        restyle(integrity, "Ok" if v.sha256_ok else "Error")
        note = QLabel(self.tr("Nur lesend – die Datei wird nicht verändert. Ansehen und Speichern werden protokolliert."))
        note.setObjectName("Hint")

        tabs = QTabWidget()
        tabs.addTab(self._content_tab(), self.tr("Inhalt"))
        att_tab = self._attachments_tab()
        tabs.addTab(att_tab, (self.tr("Anlagen ({0}) ⚠") if self.att_warnings else self.tr("Anlagen ({0})"))
                    .format(len(v.attachments)))
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
        self.body = _SafeBrowser(lambda ref: inline_resource(v, ref))
        self.show_html = QCheckBox(self.tr("HTML-Darstellung (ohne externe Inhalte, eingebettete Bilder aus der Mail)"))
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
        self._checks: dict[int, fcheck.FileCheck] = {}
        self._logged: set[int] = set()
        self._pdf = None
        w = QWidget()
        lay = QVBoxLayout(w)
        self.att = _table([self.tr("Prüfung"), self.tr("Name"), self.tr("Typ (angegeben)"), self.tr("erkannt"),
                           self.tr("Größe"), self.tr("eingebettet"), "SHA-256"])
        self.att.setRowCount(len(v.attachments))
        warn_count = 0
        for r, a in enumerate(v.attachments):
            _, fc = check_attachment(self.case, v, a.index, log=False)
            self._checks[a.index] = fc
            mark = {"ok": "✓", "info": "ℹ", "warn": "⚠"}[fc.verdict]
            warn_count += fc.verdict == "warn"
            for c, val in enumerate([mark, a.name, a.mime, format_name(fc.fmt), _fmt_bytes(a.size),
                                     self.tr("ja") if a.inline else "", a.sha256]):
                it = QTableWidgetItem(val)
                if c == 0:
                    it.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                    it.setToolTip("\n".join(finding_text(f) for f in fc.findings) or self.tr("unauffällig"))
                    if fc.verdict == "warn":
                        it.setForeground(Qt.GlobalColor.red)
                self.att.setItem(r, c, it)
        self.att_warnings = warn_count

        # rechts: Vorschau · Prüfung · Hex
        self.preview = QStackedWidget()
        self.pv_msg = QLabel()
        self.pv_msg.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.pv_msg.setWordWrap(True)
        self.pv_msg.setObjectName("Muted")
        self.pv_img = QLabel()
        self.pv_img.setAlignment(Qt.AlignmentFlag.AlignCenter)
        img_scroll = QScrollArea()
        img_scroll.setWidgetResizable(True)
        img_scroll.setWidget(self.pv_img)
        self.pv_text = QPlainTextEdit()
        self.pv_text.setReadOnly(True)
        self.pv_pdf = None
        try:
            from PySide6.QtPdf import QPdfDocument
            from PySide6.QtPdfWidgets import QPdfView

            self._QPdfDocument = QPdfDocument
            self.pv_pdf = QPdfView()
            self.pv_pdf.setPageMode(QPdfView.PageMode.MultiPage)
            self.pv_pdf.setZoomMode(QPdfView.ZoomMode.FitToWidth)
        except ImportError:  # QtPdf fehlt in der Installation
            self._QPdfDocument = None
        for wdg in (self.pv_msg, img_scroll, self.pv_text) + ((self.pv_pdf,) if self.pv_pdf else ()):
            self.preview.addWidget(wdg)
        self._img_scroll = img_scroll
        self.check_kv = KeyValues()
        self.findings = QLabel()
        self.findings.setWordWrap(True)
        self.findings.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        chk = QWidget()
        cl = QVBoxLayout(chk)
        cl.addWidget(self.check_kv)
        cl.addWidget(self.findings)
        cl.addStretch(1)
        chk_scroll = QScrollArea()
        chk_scroll.setWidgetResizable(True)
        chk_scroll.setWidget(chk)
        self.hex = QPlainTextEdit()
        self.hex.setReadOnly(True)
        self.hex.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.hex.setObjectName("Mono")  # Festbreitenschrift des Systems (Design)
        self.side = QTabWidget()
        self.side.addTab(self.preview, self.tr("Vorschau"))
        self.side.addTab(chk_scroll, self.tr("Prüfung"))
        self.side.addTab(self.hex, "Hex")

        split = QSplitter(Qt.Orientation.Vertical)
        split.addWidget(self.att)
        split.addWidget(self.side)
        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 3)
        save = QPushButton(self.tr("Anlage speichern unter …"))
        save.setEnabled(bool(v.attachments))
        save.clicked.connect(self._save_attachment)
        note = QLabel(self.tr("Vorschau nur im Speicher – nichts wird ausgeführt oder auf die Festplatte "
                              "geschrieben. Jede angesehene Anlage wird mit Hash protokolliert."))
        note.setObjectName("Hint")
        note.setWordWrap(True)
        row = QHBoxLayout()
        row.addWidget(note, 1)
        row.addWidget(save)
        lay.addWidget(split, 1)
        lay.addLayout(row)
        self.att.itemSelectionChanged.connect(self._show_attachment)
        if v.attachments:
            self.att.selectRow(0)
        else:
            self._message(self.tr("Keine Anlagen"))
        return w

    def _message(self, text: str) -> None:
        self.pv_msg.setText(text)
        self.preview.setCurrentWidget(self.pv_msg)

    def _show_attachment(self) -> None:
        r = self.att.currentRow()
        if r < 0 or r >= len(self.view.attachments):
            return
        a = self.view.attachments[r]
        log = a.index not in self._logged
        self._logged.add(a.index)
        data, fc = check_attachment(self.case, self.view, a.index, log=log)
        self._checks[a.index] = fc
        self._fill_check(a, fc)
        self.hex.setPlainText(fcheck.hexdump(data) + (
            "\n" + self.tr("… (gekürzt, {0} von {1})").format(_fmt_bytes(64 * 1024), _fmt_bytes(len(data)))
            if len(data) > 64 * 1024 else ""))
        self._render_preview(data, fc)

    def _fill_check(self, a, fc: fcheck.FileCheck) -> None:
        kv = self.check_kv
        yes_no = {True: (self.tr("passt"), "ok"), False: (self.tr("passt NICHT"), "error"),
                  None: (self.tr("nicht beurteilbar"), None)}
        kv.set(self.tr("Name"), fc.name)
        kv.set(self.tr("Erkannt am Inhalt"), format_name(fc.fmt, fc.detail))
        t, st = yes_no[fc.ext_ok]
        kv.set(self.tr("Endung"), (f".{fc.ext} – " if fc.ext else self.tr("keine – ")) + t, st)
        t, st = yes_no[fc.mime_ok]
        kv.set(self.tr("Angegebener Typ"), f"{fc.declared_mime or '–'} – {t}", st)
        kv.set(self.tr("Größe"), f"{_fmt_bytes(fc.size)} ({fc.size:,} Bytes)".replace(",", "."))
        kv.set(self.tr("Entropie"), f"{fc.entropy:.2f} Bit/Byte")
        kv.set("MD5", fc.md5, mono=True)
        kv.set("SHA-1", fc.sha1, mono=True)
        same = fc.sha256 == a.sha256
        kv.set("SHA-256", fc.sha256 + ("" if same else "  ≠ " + a.sha256), None if same else "error", mono=same)
        kv.set(self.tr("Ergebnis"), {"ok": self.tr("✓ unauffällig"), "info": self.tr("ℹ Hinweise"),
                                     "warn": self.tr("⚠ auffällig – mit Vorsicht behandeln")}[fc.verdict],
               {"ok": "ok", "warn": "error"}.get(fc.verdict))
        if fc.findings:
            self.findings.setText("\n".join(("⚠ " if f.level == "warn" else "ℹ ") + finding_text(f)
                                             for f in fc.findings))
        else:
            self.findings.setText(self.tr("Keine Auffälligkeiten gefunden. Die Prüfung erkennt typische "
                                          "Tarnungen und aktive Inhalte, ersetzt aber keinen Virenscanner."))

    def _render_preview(self, data: bytes, fc: fcheck.FileCheck) -> None:
        if fc.fmt.executable:
            self._message(self.tr("Keine Vorschau: ausführbarer Inhalt. Siehe „Prüfung“ und „Hex“."))
            return
        if len(data) > PREVIEW_LIMIT:
            self._message(self.tr("Keine Vorschau: Datei größer als {0}.").format(_fmt_bytes(PREVIEW_LIMIT)))
            return
        kind = fc.fmt.key
        if kind in IMAGE_FORMATS:
            img = QImage.fromData(data)
            if img.isNull():
                self._message(self.tr("Bild lässt sich nicht darstellen (beschädigt oder unvollständig)."))
                return
            pm = QPixmap.fromImage(img)
            maxw = max(self._img_scroll.viewport().width() - 8, 200)
            if pm.width() > maxw:
                pm = pm.scaledToWidth(maxw, Qt.TransformationMode.SmoothTransformation)
            self.pv_img.setPixmap(pm)
            self.pv_img.setToolTip(f"{img.width()} × {img.height()} px")
            self.preview.setCurrentWidget(self._img_scroll)
        elif kind == "pdf" and self.pv_pdf is not None:
            buf = QBuffer(self)
            buf.setData(QByteArray(data))
            buf.open(QIODevice.OpenModeFlag.ReadOnly)
            doc = self._QPdfDocument(self)
            doc.load(buf)
            if doc.status() == self._QPdfDocument.Status.Error or doc.pageCount() == 0:
                self._message(self.tr("PDF lässt sich nicht darstellen (beschädigt, verschlüsselt oder leer)."))
                return
            old = self._pdf
            self._pdf = (doc, buf)
            self.pv_pdf.setDocument(doc)
            if old:
                old[0].deleteLater()
                old[1].deleteLater()
            self.preview.setCurrentWidget(self.pv_pdf)
        elif fc.fmt.preview == "text" or kind in ("text", "empty"):
            text = fcheck.decode_text(data)
            if len(data) > 2 * 1024 * 1024:
                text += "\n" + self.tr("… (gekürzt)")
            self.pv_text.setPlainText(text)
            self.preview.setCurrentWidget(self.pv_text)
        else:
            self._message(self.tr("Keine Vorschau für {0}. Siehe „Prüfung“ und „Hex“.").format(format_name(fc.fmt)))

    def _save_attachment(self) -> None:
        r = self.att.currentRow()
        if r < 0:
            r = 0
        if not self.view.attachments:
            return
        a = self.view.attachments[r]
        fc = self._checks.get(a.index)
        if fc is not None and fc.verdict == "warn":
            ans = QMessageBox.warning(
                self, "FED", self.tr("Diese Anlage ist auffällig:\n\n{0}\n\nTrotzdem speichern? Die Datei nicht "
                                     "auf diesem Rechner öffnen.").format(
                    "\n".join("• " + finding_text(f) for f in fc.warnings)),
                QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel)
            if ans != QMessageBox.StandardButton.Save:
                return
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
        t.setObjectName("Mono")
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
            kv.set(self.tr("Begründung"), describe(s["detail"]))
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
