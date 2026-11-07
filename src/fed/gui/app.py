"""Einstieg der GUI."""

from __future__ import annotations

import sys
from pathlib import Path


def program_dir() -> Path:
    """Ordner des Programms (auch im Nuitka-Standalone-Build)."""
    if getattr(sys, "frozen", False) or "__compiled__" in globals():
        exe_dir = Path(sys.argv[0]).resolve().parent
        # macOS: FED.app/Contents/MacOS/fed -> Ordner, in dem FED.app liegt
        if exe_dir.name == "MacOS" and exe_dir.parent.name == "Contents" and exe_dir.parent.parent.suffix == ".app":
            return exe_dir.parent.parent.parent
        return exe_dir
    return Path.cwd()


def main() -> int:
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

        QMessageBox.critical(None, APP_NAME, f"config.toml ungültig: {e}")
        cfg = cfgmod.Config()
        config_broken = True  # ungültige Datei nicht durch die Ersteinrichtung überschreiben

    from fed.gui import theme
    from fed.gui.setup_dialog import SetupDialog, needs_setup

    if not config_broken and needs_setup(cfg):
        theme.apply(app, "dunkel" if theme.is_dark(cfg.programm.design) else "hell")
        dlg = SetupDialog(cfg, program_dir() / cfgmod.CONFIG_FILENAME)
        dlg.exec()
        dlg.deleteLater()
    win = MainWindow(cfg, program_dir())
    win.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
