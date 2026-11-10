"""Logo-Auswahl und -Vorschau (Einstellungen, Ersteinrichtung): PNG, JPG und SVG."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QCoreApplication
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QMessageBox, QWidget

from fed.core import logo as logomod

MAX_BYTES = 2 * 1024 * 1024
FILTER_EXT = "*.png *.jpg *.jpeg *.svg"


def file_filter() -> str:
    return QCoreApplication.translate("LogoPicker", "Bilder ({0})").format(FILTER_EXT)


def pixmap(path: Path) -> QPixmap:
    """Vorschau; SVG wird dafür gerendert. Leeres Pixmap, wenn nicht lesbar."""
    pm = QPixmap()
    try:
        data = path.read_bytes()
        pm.loadFromData(logomod.svg_to_png(data, 600) if logomod.detect(data) == "image/svg+xml" else data)
    except (OSError, ValueError):
        return QPixmap()
    return pm


def accept(parent: QWidget, path: Path) -> bool:
    """Prüft eine gewählte Datei (Größe, Format, SVG umwandelbar) und meldet Probleme."""
    if path.stat().st_size > MAX_BYTES:
        QMessageBox.warning(parent, "FED", QCoreApplication.translate("LogoPicker", "Das Logo ist größer als 2 MB."))
        return False
    data = path.read_bytes()
    kind = logomod.detect(data)
    if kind is None:
        QMessageBox.warning(parent, "FED", QCoreApplication.translate("LogoPicker", "Das Logo muss eine PNG-, JPG- oder SVG-Datei sein."))
        return False
    if kind == "image/svg+xml":
        try:
            logomod.svg_to_png(data, 200)
        except ValueError as e:
            QMessageBox.warning(parent, "FED", QCoreApplication.translate("LogoPicker", "Das SVG lässt sich nicht umwandeln: {0}").format(e))
            return False
    return True
