"""Einstieg der GUI."""

from __future__ import annotations

import sys
from pathlib import Path


def program_dir() -> Path:
    """Ordner des Programms (auch im Nuitka-Standalone-Build)."""
    if getattr(sys, "frozen", False) or "__compiled__" in globals():
        return Path(sys.argv[0]).resolve().parent
    return Path.cwd()


def main() -> int:
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication

    from fed import APP_NAME
    from fed.core import config as cfgmod
    from fed.gui.main_window import MainWindow

    # Portabler Modus: Qt-Einstellungen als INI im Programmordner, nichts in Registry/Profil
    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(program_dir()))

    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    cfg = cfgmod.load_default(program_dir())
    win = MainWindow(cfg)
    win.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
