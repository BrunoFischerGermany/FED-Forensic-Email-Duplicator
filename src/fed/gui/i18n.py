"""Sprache der Oberfläche: lädt ``fed_en.qm`` (Quelltexte sind Deutsch) und Qts eigene Texte."""

from __future__ import annotations

from importlib import resources

from PySide6.QtCore import QCoreApplication, QLibraryInfo, QLocale, QTranslator

_installed: list[QTranslator] = []


def system_language() -> str:
    """``de`` bei deutschem System, sonst ``en``."""
    return "de" if QLocale.system().language() == QLocale.Language.German else "en"


def install(language: str) -> str:
    """Setzt die Sprache (``de``/``en``) für alle danach erzeugten Fenster."""
    app = QCoreApplication.instance()
    for t in _installed:
        app.removeTranslator(t)
    _installed.clear()
    language = language if language in ("de", "en") else "de"
    if language == "en":
        t = QTranslator(app)
        qm = resources.files("fed") / "i18n" / "fed_en.qm"
        if t.load(str(qm)):
            app.installTranslator(t)
            _installed.append(t)
    qt = QTranslator(app)  # Standardtexte von Qt (Ja/Nein, Abbrechen, Dateidialog)
    if qt.load(QLocale(language), "qtbase", "_", QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)):
        app.installTranslator(qt)
        _installed.append(qt)
    QLocale.setDefault(QLocale(language))
    return language
