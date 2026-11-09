"""Hilfe im Programm: Anleitungen aus ``fed/resources/help/<thema>_<sprache>.md``.

Das Fenster ist nicht modal – man kann die Anleitung offen lassen und dabei die
Einstellungen ausfüllen. Links öffnen im System-Browser.
"""

from __future__ import annotations

from importlib import resources

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QTextBrowser, QVBoxLayout, QWidget

from fed.core.msg import current_language

# Abschnitt -> Wort in der Überschrift (## …), die angesprungen wird
SECTIONS = {"microsoft": "Microsoft", "google": "Google"}


def help_text(topic: str, language: str | None = None) -> str:
    """Markdown der Anleitung in der Sprache der Oberfläche (sonst Englisch, sonst Deutsch)."""
    folder = resources.files("fed") / "resources" / "help"
    for lang in (language or current_language(), "en", "de"):
        f = folder / f"{topic}_{lang}.md"
        if f.is_file():
            return f.read_text(encoding="utf-8")
    raise FileNotFoundError(topic)


class HelpDialog(QDialog):
    def __init__(self, topic: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.topic = topic
        self.language = current_language()
        self.browser = QTextBrowser()
        self.browser.setOpenExternalLinks(True)
        self.browser.setMarkdown(help_text(topic))
        lines = help_text(topic).splitlines()
        self.setWindowTitle(next((ln[2:] for ln in lines if ln.startswith("# ")), topic))
        lay = QVBoxLayout(self)
        lay.addWidget(self.browser)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        bb.rejected.connect(self.close)
        lay.addWidget(bb)
        self.resize(760, 680)

    def go_to(self, section: str | None) -> None:
        """Springt zur Überschrift (Ebene 2) des Abschnitts, z. B. ``microsoft``."""
        word = SECTIONS.get(section or "")
        if not word:
            return
        doc = self.browser.document()
        block = doc.begin()
        while block.isValid():
            if block.blockFormat().headingLevel() == 2 and word in block.text():
                break
            block = block.next()
        else:
            return

        def scroll(b=block):
            top = doc.documentLayout().blockBoundingRect(b).top()
            self.browser.verticalScrollBar().setValue(int(top))

        scroll()
        QTimer.singleShot(0, scroll)  # nach dem ersten Layout erneut


def show_help(parent: QWidget, topic: str = "oauth", section: str | None = None) -> HelpDialog:
    """Öffnet die Anleitung (je Fenster nur einmal) und springt zum Abschnitt."""
    window = parent.window()
    dlg = getattr(window, "_fed_help", None)
    if dlg is None or (dlg.topic, dlg.language) != (topic, current_language()):
        dlg = HelpDialog(topic, window)
        window._fed_help = dlg
    dlg.show()
    dlg.raise_()
    dlg.activateWindow()
    dlg.go_to(section)
    return dlg
