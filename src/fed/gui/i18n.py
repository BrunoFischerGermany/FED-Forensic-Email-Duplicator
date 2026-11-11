"""Sprache der Oberfläche: lädt ``fed_en.qm`` (Quelltexte sind Deutsch) und Qts eigene Texte."""

from __future__ import annotations

from importlib import resources

from PySide6.QtCore import QCoreApplication, QLibraryInfo, QLocale, QTranslator

_installed: list[QTranslator] = []


def system_language() -> str:
    """Sprache des Systems, falls FED sie kennt, sonst Englisch."""
    from fed.core.languages import FALLBACK, ui_languages

    code = QLocale.system().name().split("_")[0]
    return code if code in ui_languages() else FALLBACK


def language_name(code: str) -> str:
    """Name der Sprache in ihr selbst, z. B. ``Deutsch``, ``English``, ``Français``."""
    name = QLocale(code).nativeLanguageName()
    return (name[:1].upper() + name[1:]) if name else code


def fill_combo(combo, codes: list[str]) -> None:
    for c in codes:
        combo.addItem(language_name(c), c)


def install(language: str) -> str:
    """Setzt die Sprache (``de``/``en``) für alle danach erzeugten Fenster."""
    app = QCoreApplication.instance()
    for t in _installed:
        app.removeTranslator(t)
    _installed.clear()
    from fed.core.languages import SOURCE, ui_languages

    language = language if language in ui_languages() else SOURCE
    if language != SOURCE:
        t = QTranslator(app)
        qm = resources.files("fed") / "i18n" / f"fed_{language}.qm"
        if t.load(str(qm)):
            app.installTranslator(t)
            _installed.append(t)
    # Standardtexte von Qt (Ja/Nein, Abbrechen, Schließen, Dateidialog): im gebauten Programm aus
    # fed/i18n/qt (von build.py mitgegeben), sonst aus der Qt-Installation
    for folder in (str(resources.files("fed") / "i18n" / "qt"),
                   QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)):
        qt = QTranslator(app)
        if qt.load(QLocale(language), "qtbase", "_", folder):
            app.installTranslator(qt)
            _installed.append(qt)
            break
    QLocale.setDefault(QLocale(language))
    from fed.core import msg

    msg.set_language(language)
    return language
