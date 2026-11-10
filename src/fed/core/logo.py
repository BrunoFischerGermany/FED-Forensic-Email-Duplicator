"""Agentur-Logo: Format erkennen und SVG in PNG umwandeln.

Der PDF-Bericht bettet nur Rasterbilder ein (PNG/JPG). Ein SVG-Logo wird beim Anlegen
eines Falls mit Qt (QtSvg) in ein PNG umgerechnet; das Original-SVG bleibt unverändert,
sein SHA-256 wird zusätzlich protokolliert.
"""

from __future__ import annotations

import os
import re

# Längste Seite des umgewandelten PNG – im PDF max. 40 × 20 mm, das sind bei 1200 px > 750 dpi
SVG_PIXELS = 1200

_SVG_HEAD = re.compile(rb"^\s*(?:<\?xml[^>]*>\s*)?(?:<!--.*?-->\s*|<!DOCTYPE[^>]*>\s*)*<svg[\s>]", re.S | re.I)


def detect(data: bytes) -> str | None:
    """MIME-Typ des Logos oder ``None`` (nicht unterstützt)."""
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    head = data[3:4096] if data[:3] == b"\xef\xbb\xbf" else data[:4096]  # UTF-8-BOM
    if _SVG_HEAD.match(head):
        return "image/svg+xml"
    return None


def _ensure_gui() -> None:
    """QPainter braucht für Schriften eine QGuiApplication – in der Kommandozeile ohne Fenster."""
    from PySide6.QtCore import QCoreApplication

    if QCoreApplication.instance() is None:
        # QApplication (nicht nur QGuiApplication), damit ein später gestartetes Fenster sie nutzen kann
        from PySide6.QtWidgets import QApplication

        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        global _APP
        _APP = QApplication([])


_APP = None


def svg_to_png(data: bytes, longest: int = SVG_PIXELS) -> bytes:
    """Rendert ein SVG mit transparentem Hintergrund als PNG (längste Seite ``longest`` px)."""
    _ensure_gui()
    from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QRectF
    from PySide6.QtGui import QImage, QPainter
    from PySide6.QtSvg import QSvgRenderer

    r = QSvgRenderer(QByteArray(data))
    if not r.isValid():
        raise ValueError("SVG ungültig")
    vb = r.viewBoxF()
    w, h = (vb.width(), vb.height()) if vb.width() > 0 and vb.height() > 0 else (
        r.defaultSize().width(), r.defaultSize().height())
    if w <= 0 or h <= 0:
        raise ValueError("SVG ohne Größe")
    scale = longest / max(w, h)
    img = QImage(max(1, round(w * scale)), max(1, round(h * scale)), QImage.Format.Format_ARGB32)
    img.fill(0)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    r.render(p, QRectF(0, 0, img.width(), img.height()))
    p.end()
    buf = QBuffer()
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    if not img.save(buf, "PNG"):
        raise ValueError("PNG konnte nicht erzeugt werden")
    return bytes(buf.data())
