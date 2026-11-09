"""Einstieg der GUI."""

from __future__ import annotations

import sys
from pathlib import Path


def program_file() -> Path | None:
    """Die gestartete Programmdatei im gebauten Programm (bei der Windows-Einzeldatei die .exe selbst,
    nicht die entpackte Kopie im Temp-Ordner), sonst ``None``."""
    if getattr(sys, "frozen", False) or "__compiled__" in globals():
        return Path(sys.argv[0]).resolve()
    return None


def program_dir() -> Path:
    """Ordner des Programms (auch im Nuitka-Build; dort liegen config.toml und FED.ini)."""
    exe = program_file()
    if exe is not None:
        exe_dir = exe.parent
        # macOS: FED.app/Contents/MacOS/fed -> Ordner, in dem FED.app liegt
        if exe_dir.name == "MacOS" and exe_dir.parent.name == "Contents" and exe_dir.parent.parent.suffix == ".app":
            return exe_dir.parent.parent.parent
        return exe_dir
    return Path.cwd()


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
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
