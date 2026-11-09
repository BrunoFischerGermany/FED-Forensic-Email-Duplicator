"""Notiz-Editor mit einfacher Formatierung: fett, kursiv, unterstrichen, Farbe, Marker, Aufzählung.

Gespeichert wird das bereinigte HTML aus :mod:`fed.core.richtext` – nicht Qt's eigenes HTML.
Eingefügter Text kommt immer unformatiert (keine fremden Schriften, Links oder Bilder).
"""

from __future__ import annotations

from PySide6.QtCore import QT_TRANSLATE_NOOP, QCoreApplication, Qt, Signal
from PySide6.QtGui import (QAction, QBrush, QColor, QFont, QIcon, QKeySequence, QPixmap, QTextCharFormat, QTextCursor,
                           QTextFormat, QTextListFormat)
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QMenu, QTextEdit, QToolButton,
                               QVBoxLayout, QWidget)

from fed.core import richtext as rt

COLOR_NAMES = {
    "red": QT_TRANSLATE_NOOP("RichNoteEdit", "Rot"),
    "orange": QT_TRANSLATE_NOOP("RichNoteEdit", "Orange"),
    "green": QT_TRANSLATE_NOOP("RichNoteEdit", "Grün"),
    "blue": QT_TRANSLATE_NOOP("RichNoteEdit", "Blau"),
}


def _tr(text: str) -> str:
    return QCoreApplication.translate("RichNoteEdit", text)


def _swatch(color: str) -> QIcon:
    pm = QPixmap(14, 14)
    pm.fill(QColor(color))
    return QIcon(pm)


class _PlainPasteEdit(QTextEdit):
    def insertFromMimeData(self, source) -> None:
        if source.hasText():
            self.textCursor().insertText(source.text())


def blocks_from_document(doc) -> list[rt.Block]:
    """QTextDocument -> bereinigtes Modell (nur die erlaubten Formate)."""
    out = []
    block = doc.begin()
    while block.isValid():
        kind = "li" if block.textList() is not None else "p"
        runs = []
        it = block.begin()
        while not it.atEnd():
            frag = it.fragment()
            if frag.isValid() and frag.text():
                f = frag.charFormat()
                color = bg = None
                if f.hasProperty(QTextFormat.Property.ForegroundBrush):
                    color = f.foreground().color().name()
                if f.hasProperty(QTextFormat.Property.BackgroundBrush) and f.background().style() != Qt.BrushStyle.NoBrush:
                    bg = f.background().color().name()
                style = rt.Style(f.fontWeight() >= QFont.Weight.DemiBold, f.fontItalic(), f.fontUnderline(), color, bg)
                runs.append((frag.text().replace(" ", " "), style))
            it += 1
        out.append(rt.Block(kind, rt._merge(runs)))
        block = block.next()
    while out and not out[-1].text.strip():
        out.pop()
    return out


class RichNoteEdit(QWidget):
    """Mehrzeilige Notiz mit Werkzeugleiste. API ähnlich QPlainTextEdit: ``toPlainText``, ``setPlainText``,
    dazu ``html()`` / ``set_html()`` für die formatierte Fassung."""

    textChanged = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.edit = _PlainPasteEdit()
        self.edit.setAcceptRichText(False)
        self.edit.setTabChangesFocus(True)
        self.edit.textChanged.connect(self.textChanged.emit)
        self.edit.currentCharFormatChanged.connect(self._sync)
        bar = QHBoxLayout()
        bar.setContentsMargins(0, 0, 0, 0)
        bar.setSpacing(4)

        def tool(text, tip, slot, checkable=False, shortcut=None) -> QToolButton:
            b = QToolButton()
            b.setText(text)
            b.setToolTip(tip + (f"  ({QKeySequence(shortcut).toString(QKeySequence.SequenceFormat.NativeText)})"
                                if shortcut else ""))
            b.setCheckable(checkable)
            b.setAutoRaise(True)
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            b.clicked.connect(slot)
            if shortcut:
                act = QAction(self)
                act.setShortcut(shortcut)
                act.setShortcutContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
                act.triggered.connect(b.click)
                self.addAction(act)
            bar.addWidget(b)
            return b

        self.b_bold = tool("B", self.tr("Fett"), self._bold, True, QKeySequence.StandardKey.Bold)
        f = self.b_bold.font()
        f.setBold(True)
        self.b_bold.setFont(f)
        self.b_italic = tool("I", self.tr("Kursiv"), self._italic, True, QKeySequence.StandardKey.Italic)
        f = self.b_italic.font()
        f.setItalic(True)
        self.b_italic.setFont(f)
        self.b_under = tool("U", self.tr("Unterstrichen"), self._underline, True, QKeySequence.StandardKey.Underline)
        f = self.b_under.font()
        f.setUnderline(True)
        self.b_under.setFont(f)
        self.b_color = tool("A ▾", self.tr("Schriftfarbe"), lambda: None)
        menu = QMenu(self.b_color)
        menu.addAction(self.tr("Standard")).triggered.connect(lambda: self._color(None))
        for key, name in COLOR_NAMES.items():
            menu.addAction(_swatch(rt.COLORS[key]), self.tr(name)).triggered.connect(
                lambda _=False, c=rt.COLORS[key]: self._color(c))
        self.b_color.setMenu(menu)
        self.b_color.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.b_mark = tool(self.tr("Marker"), self.tr("Hervorheben (Marker)"), self._mark, True)
        self.b_mark.setIcon(_swatch(rt.HIGHLIGHT))
        self.b_mark.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.b_list = tool("• " + self.tr("Liste"), self.tr("Aufzählung ein/aus"), self._list, True)
        self.b_clear = tool("⌫ " + self.tr("Format"), self.tr("Formatierung der Auswahl entfernen"), self._clear)
        bar.addStretch(1)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        lay.addLayout(bar)
        lay.addWidget(self.edit, 1)

    # ------------------------------------------------------------ API
    def html(self) -> str:
        """Bereinigtes HTML; leer, wenn die Notiz leer ist."""
        blocks = blocks_from_document(self.edit.document())
        return rt.to_html(blocks) if blocks else ""

    def set_html(self, value: str | None) -> None:
        self.edit.setHtml(rt.to_html(rt.parse(value)) if value else "")

    def toPlainText(self) -> str:
        return rt.to_plain(blocks_from_document(self.edit.document()))

    def setPlainText(self, text: str) -> None:
        self.edit.setPlainText(text)

    def set_value(self, plain: str | None, html_value: str | None) -> None:
        if html_value:
            self.set_html(html_value)
        else:
            self.setPlainText(plain or "")

    def setPlaceholderText(self, text: str) -> None:
        self.edit.setPlaceholderText(text)

    def clear(self) -> None:
        self.edit.clear()

    def setFocus(self) -> None:
        self.edit.setFocus()

    # ------------------------------------------------------------ Formate
    def _merge(self, fmt: QTextCharFormat) -> None:
        cur = self.edit.textCursor()
        if not cur.hasSelection():
            cur.select(QTextCursor.SelectionType.WordUnderCursor)
            if not cur.hasSelection():  # an Leerraum: für das Weiterschreiben setzen
                self.edit.mergeCurrentCharFormat(fmt)
                return
        cur.mergeCharFormat(fmt)
        self.edit.mergeCurrentCharFormat(fmt)

    def _bold(self) -> None:
        f = QTextCharFormat()
        f.setFontWeight(QFont.Weight.Bold if self.b_bold.isChecked() else QFont.Weight.Normal)
        self._merge(f)

    def _italic(self) -> None:
        f = QTextCharFormat()
        f.setFontItalic(self.b_italic.isChecked())
        self._merge(f)

    def _underline(self) -> None:
        f = QTextCharFormat()
        f.setFontUnderline(self.b_under.isChecked())
        self._merge(f)

    def _color(self, color: str | None) -> None:
        if color is None:
            self._clear_property(QTextFormat.Property.ForegroundBrush)
            return
        f = QTextCharFormat()
        f.setForeground(QBrush(QColor(color)))
        self._merge(f)

    def _mark(self) -> None:
        if self.b_mark.isChecked():
            f = QTextCharFormat()
            f.setBackground(QBrush(QColor(rt.HIGHLIGHT)))
            f.setForeground(QBrush(QColor("#000000")))  # auf gelb immer lesbar, auch im dunklen Design
            self._merge(f)
        else:
            self._clear_property(QTextFormat.Property.BackgroundBrush)

    def _clear_property(self, prop) -> None:
        cur = self.edit.textCursor()
        if not cur.hasSelection():
            cur.select(QTextCursor.SelectionType.WordUnderCursor)
        start, end = cur.selectionStart(), cur.selectionEnd()
        c = QTextCursor(self.edit.document())
        c.beginEditBlock()
        pos = start
        while pos < end:  # Zeichen für Zeichen, damit andere Formate erhalten bleiben
            c.setPosition(pos)
            c.setPosition(pos + 1, QTextCursor.MoveMode.KeepAnchor)
            fmt = c.charFormat()
            fmt.clearProperty(prop)
            if prop == QTextFormat.Property.BackgroundBrush:
                fmt.clearProperty(QTextFormat.Property.ForegroundBrush)
            c.setCharFormat(fmt)
            pos += 1
        c.endEditBlock()
        fmt = self.edit.currentCharFormat()
        fmt.clearProperty(prop)
        self.edit.setCurrentCharFormat(fmt)

    def _list(self) -> None:
        cur = self.edit.textCursor()
        lst = cur.currentList()
        cur.beginEditBlock()
        if lst is not None:
            block = cur.block()
            lst.remove(block)
            bf = block.blockFormat()
            bf.setIndent(0)
            cur.setBlockFormat(bf)
        else:
            cur.createList(QTextListFormat.Style.ListDisc)
        cur.endEditBlock()
        self._sync(self.edit.currentCharFormat())

    def _clear(self) -> None:
        cur = self.edit.textCursor()
        if not cur.hasSelection():
            cur.select(QTextCursor.SelectionType.WordUnderCursor)
        cur.setCharFormat(QTextCharFormat())
        self.edit.setCurrentCharFormat(QTextCharFormat())
        self._sync(QTextCharFormat())

    def _sync(self, f: QTextCharFormat) -> None:
        self.b_bold.setChecked(f.fontWeight() >= QFont.Weight.DemiBold)
        self.b_italic.setChecked(f.fontItalic())
        self.b_under.setChecked(f.fontUnderline())
        self.b_mark.setChecked(f.hasProperty(QTextFormat.Property.BackgroundBrush)
                               and f.background().style() != Qt.BrushStyle.NoBrush)
        self.b_list.setChecked(self.edit.textCursor().currentList() is not None)


class RichNoteDialog(QDialog):
    """Dialog mit formatierbarer Notiz, z. B. Abschlussvermerk vor dem Bericht."""

    def __init__(self, parent=None, *, title: str, heading: str, plain: str | None = None,
                 html_value: str | None = None, placeholder: str = "", hint: str = "", ok_text: str = "") -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumSize(560, 340)
        head = QLabel(heading)
        head.setObjectName("H2")
        head.setWordWrap(True)
        self.edit = RichNoteEdit()
        self.edit.set_value(plain, html_value)
        self.edit.setPlaceholderText(placeholder)
        info = QLabel(hint)
        info.setObjectName("Hint")
        info.setWordWrap(True)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        if ok_text:
            buttons.button(QDialogButtonBox.StandardButton.Ok).setText(ok_text)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addWidget(head)
        lay.addWidget(self.edit, 1)
        lay.addWidget(info)
        lay.addWidget(buttons)
        self.edit.setFocus()

    def text(self) -> str:
        return self.edit.toPlainText()

    def html(self) -> str:
        return self.edit.html()
