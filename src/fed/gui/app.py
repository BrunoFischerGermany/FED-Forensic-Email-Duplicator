"""Einstieg der GUI."""

from __future__ import annotations

import sys
from pathlib import Path


def program_file() -> Path | None:
    """Die gestartete Programmdatei im gebauten Programm, sonst ``None``."""
    if getattr(sys, "frozen", False) or "__compiled__" in globals():
        return Path(sys.argv[0]).resolve()
    return None


# Gesetzt, wenn macOS FED aus einer verschobenen Kopie startet (App Translocation):
# {"running": Kopie, "original": Ablageort des FED.app oder None, "config_dir": genutzter Ordner}
TRANSLOCATION: dict | None = None


def _writable(d: Path) -> bool:
    import os

    return d.is_dir() and os.access(d, os.W_OK)


def resolve_program_dir(exe: Path | None) -> Path:
    """Ordner für config.toml und FED.ini zu einer Programmdatei (siehe ``program_dir``)."""
    global TRANSLOCATION
    if exe is None:
        return Path.cwd()
    exe_dir = exe.parent
    # Windows: FED\FED.exe (Startprogramm) startet FED\app\FED.exe -> Einstellungen in FED\
    if exe_dir.name.lower() == "app" and (exe_dir.parent / "FED.exe").is_file():
        return exe_dir.parent
    # macOS: FED.app/Contents/MacOS/fed -> Ordner, in dem FED.app liegt
    if exe_dir.name == "MacOS" and exe_dir.parent.name == "Contents" and exe_dir.parent.parent.suffix == ".app":
        from fed.core import macos

        bundle = exe_dir.parent.parent
        if macos.is_translocated(bundle):
            orig = macos.original_bundle(bundle)
            target = orig.parent if orig and _writable(orig.parent) else macos.user_data_dir()
            target.mkdir(parents=True, exist_ok=True)
            TRANSLOCATION = {"running": bundle, "original": orig, "config_dir": target}
            return target
        return bundle.parent
    return exe_dir


_PROGRAM_DIR: Path | None = None


def program_dir() -> Path:
    """Ordner des Programms (auch im Nuitka-Build; dort liegen config.toml und FED.ini).

    Startet macOS FED aus einer schreibgeschützten Kopie (App Translocation), ist das der Ordner, in
    dem FED.app wirklich liegt – oder, falls der nicht ermittelbar ist, ~/Library/Application Support/FED.
    """
    global _PROGRAM_DIR
    if _PROGRAM_DIR is None:
        _PROGRAM_DIR = resolve_program_dir(program_file())
    return _PROGRAM_DIR


def translocation_text(info: dict) -> str:
    from PySide6.QtCore import QCoreApplication

    where = (QCoreApplication.translate("App", "Einstellungen werden neben FED.app gespeichert:\n{0}").format(info["config_dir"])
             if info["original"] else
             QCoreApplication.translate("App", "Der Ablageort von FED.app ist nicht ermittelbar – Einstellungen werden hier gespeichert:\n{0}")
             .format(info["config_dir"]))
    return (QCoreApplication.translate("App", "macOS führt FED aus einer schreibgeschützten Kopie aus („App Translocation“), weil das "
                      "Programm aus dem Internet geladen und nicht von Apple notarisiert ist.")
            + "\n\n" + where + "\n\n"
            + QCoreApplication.translate("App", "Dauerhaft beheben (danach startet FED direkt am Ablageort):\n"
                        "• FED.app im Finder in einen anderen Ordner ziehen, z. B. „Programme“, oder\n"
                        "• im Terminal: xattr -dr com.apple.quarantine <Pfad>/FED.app"))


def selftest(out: Path) -> int:
    """Prüft ein gebautes Programm ohne Fenster (``FED_SELFTEST=<datei.json>``): Pfade, Programm-Hash,
    Übersetzungen, Qt, Schriften/PDF, Case-Datei. Wird im Build-Workflow nach jedem Bau ausgeführt."""
    import json
    import os
    import tempfile
    import traceback

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    res: dict = {"ok": False}
    try:
        from PySide6.QtWidgets import QApplication, QLabel

        from fed import __version__
        from fed.core.case import Case, CaseInfo
        from fed.core.hashing import tool_fingerprint
        from fed.core.languages import report_languages, ui_languages
        from fed.gui import i18n

        app = QApplication.instance() or QApplication([])
        i18n.install("en")
        en = app.translate("MainWindow", "Einstellungen")
        QLabel("FED").grab()
        exe = program_file()
        res.update(version=__version__, program_file=str(exe) if exe else None, program_dir=str(program_dir()),
                   translocated=bool(TRANSLOCATION),
                   tool_sha256=tool_fingerprint(), ui_languages=ui_languages(), report_languages=report_languages(),
                   translation_en=en)
        with tempfile.TemporaryDirectory() as tmp:
            from fed.core.report import write_pdf

            import logging

            from fed.core import pdftext

            missing: list[str] = []
            handler = logging.Handler()
            handler.emit = lambda rec: missing.append(rec.getMessage()) if "missing" in rec.getMessage() else None
            logging.getLogger("fpdf").addHandler(handler)
            # alle Ersatzschriften und die Textformung (uharfbuzz) müssen im gebauten Programm wirken
            case = Case.create(Path(tmp), CaseInfo("SELFTEST", "FED", "Русский · 中文 日本語 · မြန်မာ · "
                                                  "العربية 2026 · עברית · Tiếng Việt · 👍"))
            pdf = Path(tmp) / "t.pdf"
            write_pdf(case, pdf, "en")
            res["pdf_bytes"] = pdf.stat().st_size
            res["log_ok"] = case.verify_log()[0]
            case.close()
            logging.getLogger("fpdf").removeHandler(handler)
            res["text_shaping"] = pdftext.shaping_available()
            res["pdf_missing_glyphs"] = missing
        res["ok"] = (en == "Settings" and res["pdf_bytes"] > 1000 and res["log_ok"] and "en" in res["ui_languages"]
                     and res["text_shaping"] and not res["pdf_missing_glyphs"])
    except Exception:
        res["error"] = traceback.format_exc()
    out.write_text(json.dumps(res, indent=2), encoding="utf-8")
    return 0 if res["ok"] else 1


def main() -> int:
    import os

    if os.environ.get("FED_SELFTEST"):
        return selftest(Path(os.environ["FED_SELFTEST"]))

    from importlib import resources

    from PySide6.QtCore import QSettings
    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QApplication

    from fed import APP_NAME
    from fed.core import config as cfgmod
    from fed.gui.main_window import MainWindow

    # Portabler Modus: Qt-Einstellungen als INI im Programmordner, nichts in Registry/Profil
    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(program_dir()))

    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setStyle("Fusion")
    app.setWindowIcon(QIcon(str(resources.files("fed.resources").joinpath("fed-icon-256.png"))))
    config_broken = False
    try:
        cfg = cfgmod.load_default(program_dir())
    except cfgmod.ConfigError as e:
        from PySide6.QtWidgets import QMessageBox

        QMessageBox.critical(None, APP_NAME, f"config.toml ungültig / invalid:\n{e}")
        cfg = cfgmod.Config()
        config_broken = True  # ungültige Datei nicht durch die Ersteinrichtung überschreiben

    from fed.gui import i18n, theme
    from fed.gui.setup_dialog import SetupDialog, needs_setup

    if cfg.source_path is None:  # erster Start: Sprache des Systems vorschlagen
        cfg.programm.sprache = i18n.system_language()
    i18n.install(cfg.programm.sprache)
    if not config_broken and needs_setup(cfg):
        theme.apply(app, "dunkel" if theme.is_dark(cfg.programm.design) else "hell")
        dlg = SetupDialog(cfg, program_dir() / cfgmod.CONFIG_FILENAME)
        dlg.exec()
        dlg.deleteLater()
        i18n.install(cfg.programm.sprache)  # in der Ersteinrichtung gewählte Sprache
    win = MainWindow(cfg, program_dir())
    win.show()
    if TRANSLOCATION:  # macOS startet eine verschobene Kopie – erklären, wo gespeichert wird
        from PySide6.QtWidgets import QMessageBox

        QMessageBox.information(win, APP_NAME, translocation_text(TRANSLOCATION))
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
