"""Hell/Dunkel nach dem Mockup: Farb-Tokens, Palette und Stylesheet."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

LIGHT = {
    "canvas": "#E9ECF1", "bg": "#F6F7F9", "surface": "#FFFFFF", "text": "#16181D", "text2": "#3A3F4A",
    "muted": "#5B6270", "line": "#E1E5EB", "line2": "#EEF0F3", "line_strong": "#C9CED8", "accent": "#2457C5",
    "accent_text": "#2457C5", "accent_soft": "#EAF0FC", "ok": "#1F6E43", "ok_soft": "#E3F1E9",
    "warn": "#7A4A0E", "warn_soft": "#FBEAD2", "danger": "#B3261E", "header": "#16181D",
}
DARK = {
    "canvas": "#0E1014", "bg": "#14171C", "surface": "#1C2027", "text": "#E6E8EC", "text2": "#C3C8D1",
    "muted": "#9AA1AD", "line": "#2B3039", "line2": "#252A32", "line_strong": "#3A404B", "accent": "#2F62D0",
    "accent_text": "#8AB0FF", "accent_soft": "#1E2A44", "ok": "#5CC48A", "ok_soft": "#143324",
    "warn": "#E9B46A", "warn_soft": "#3A2A12", "danger": "#FF8A80", "header": "#0A0C0F",
}

_current = dict(LIGHT)


def tokens() -> dict:
    return _current


def is_dark(mode: str) -> bool:
    if mode == "dunkel":
        return True
    if mode == "hell":
        return False
    try:
        return QApplication.styleHints().colorScheme() == Qt.ColorScheme.Dark
    except AttributeError:
        return False


def _check_icon() -> str:
    import tempfile
    from pathlib import Path

    p = Path(tempfile.gettempdir()) / "fed-check.svg"
    if not p.exists():
        p.write_text('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16"><path d="M3.5 8.5l3 3 6-7" '
                     'fill="none" stroke="#fff" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/></svg>',
                     encoding="utf-8")
    return p.as_posix()


def _fonts() -> tuple[str, str]:
    """Systemschriften (Oberfläche, Festbreite) – immer vorhanden, kein Suchen nach Ersatzschriften."""
    from PySide6.QtGui import QFontDatabase

    ui = QFontDatabase.systemFont(QFontDatabase.SystemFont.GeneralFont).family()
    mono = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont).family()
    return ui, mono


def stylesheet(t: dict) -> str:
    ui_font, mono_font = _fonts()
    check = _check_icon()
    return f"""
* {{ font-family: "{ui_font}"; font-size: 14px; color: {t['text']}; }}
QMainWindow, QWidget#Page, QStackedWidget, QScrollArea, QScrollArea > QWidget > QWidget {{ background: {t['bg']}; }}
QWidget#Header {{ background: {t['header']}; }}
QWidget#Header QLabel {{ color: #FFFFFF; background: transparent; }}
QLabel#HeaderSub {{ color: #A9B0BD; font-size: 13px; }}
QPushButton#NavButton {{ background: transparent; color: #C9CED8; border: none; border-radius: 6px; padding: 8px 14px; }}
QPushButton#NavButton:checked {{ background: #2C313B; color: #FFFFFF; font-weight: 600; }}
QPushButton#ThemeButton {{ background: transparent; color: #E6E8EC; border: 1px solid #3A404B; border-radius: 6px;
    padding: 0; }}
QWidget#Rail {{ background: {t['surface']}; border-right: 1px solid {t['line']}; }}
QLabel#RailTitle {{ color: {t['muted']}; font-size: 12px; font-weight: 600; letter-spacing: 1px; }}
QPushButton#Step {{ text-align: left; background: transparent; border: none; border-radius: 6px; padding: 9px 12px;
    color: {t['text2']}; }}
QPushButton#Step:checked {{ background: {t['accent_soft']}; color: {t['text']}; font-weight: 600; }}
QPushButton#Step:disabled {{ color: {t['muted']}; }}
QFrame#Card {{ background: {t['surface']}; border: 1px solid {t['line']}; border-radius: 10px; }}
QFrame#Card QLabel {{ background: transparent; }}
QLabel#H1 {{ font-size: 24px; font-weight: 700; }}
QLabel#H2 {{ font-size: 16px; font-weight: 600; }}
QLabel#Muted, QLabel#Hint {{ color: {t['muted']}; }}
QLabel#Hint {{ font-size: 12px; }}
QLabel#Ok {{ color: {t['ok']}; font-weight: 600; }}
QLabel#Warn {{ color: {t['warn']}; font-weight: 600; }}
QLabel#Error {{ color: {t['danger']}; font-weight: 600; }}
QLabel#Accent {{ color: {t['accent']}; font-weight: 600; }}
QFrame#RailItem {{ border-radius: 6px; }}
QFrame#RailItem:hover {{ background: {t['accent_soft']}; }}
QLabel#Mono, QPlainTextEdit#Mono, QLineEdit#Mono {{ font-family: "{mono_font}"; font-size: 12px; }}
QLabel#Big {{ font-size: 22px; font-weight: 700; }}
QLabel#Huge {{ font-size: 28px; font-weight: 700; }}
QLineEdit, QPlainTextEdit, QTextEdit, QComboBox, QDateEdit, QSpinBox {{ background: {t['surface']};
    border: 1px solid {t['line_strong']}; border-radius: 6px; padding: 7px 10px; selection-background-color: {t['accent']}; }}
QLineEdit:focus, QPlainTextEdit:focus, QComboBox:focus {{ border-color: {t['accent']}; }}
QLineEdit, QComboBox, QDateEdit, QSpinBox {{ min-height: 22px; }}
QWidget#ButtonBar {{ border-top: 1px solid {t['line']}; }}
QLineEdit[invalid="true"], QLineEdit[invalid="true"]:focus {{ border: 2px solid {t['danger']}; }}
QPushButton {{ background: {t['surface']}; border: 1px solid {t['line_strong']}; border-radius: 6px;
    padding: 9px 16px; font-weight: 500; }}
QPushButton:hover {{ border-color: {t['accent']}; }}
QPushButton:disabled {{ color: {t['muted']}; border-color: {t['line']}; }}
QPushButton#Primary {{ background: {t['accent']}; border-color: {t['accent']}; color: #FFFFFF; font-weight: 600; }}
QPushButton#Primary:disabled {{ background: {t['line_strong']}; border-color: {t['line_strong']}; color: {t['muted']}; }}
QPushButton#Danger {{ color: {t['danger']}; border-color: {t['danger']}; font-weight: 600; }}
QPushButton#Seg {{ border-radius: 0; padding: 8px 12px; }}
QPushButton#Seg:checked {{ background: {t['accent_soft']}; border-color: {t['accent']}; font-weight: 600; }}
QProgressBar {{ background: {t['line2']}; border: none; border-radius: 6px; height: 12px; text-align: center; color: transparent; }}
QProgressBar::chunk {{ background: {t['accent']}; border-radius: 6px; }}
QTreeWidget, QTableView, QTableWidget {{ background: {t['surface']}; border: 1px solid {t['line']}; border-radius: 8px;
    gridline-color: {t['line2']}; alternate-background-color: {t['bg']}; selection-background-color: {t['accent_soft']};
    selection-color: {t['text']}; }}
QHeaderView::section {{ background: {t['bg']}; color: {t['muted']}; border: none; border-bottom: 1px solid {t['line']};
    padding: 8px; font-size: 12px; font-weight: 600; }}
QStatusBar {{ background: {t['surface']}; border-top: 1px solid {t['line']}; color: {t['muted']}; }}
QStatusBar QLabel {{ color: {t['muted']}; font-size: 12px; }}
QTabWidget::pane {{ border: none; }}
QTabBar::tab {{ background: transparent; padding: 10px 16px; border-bottom: 2px solid transparent; color: {t['text2']}; }}
QTabBar::tab:selected {{ border-bottom-color: {t['accent_text']}; color: {t['text']}; font-weight: 600; }}
QCheckBox, QRadioButton {{ spacing: 8px; background: transparent; }}
QCheckBox::indicator, QTreeView::indicator {{ width: 16px; height: 16px; border: 1px solid {t['line_strong']};
    border-radius: 4px; background: {t['surface']}; }}
QCheckBox::indicator:checked, QTreeView::indicator:checked {{ background: {t['accent']}; border-color: {t['accent']};
    image: url({check}); }}
QRadioButton::indicator {{ width: 16px; height: 16px; border: 1px solid {t['line_strong']}; border-radius: 8px;
    background: {t['surface']}; }}
QRadioButton::indicator:checked {{ background: {t['surface']}; border: 5px solid {t['accent']}; width: 8px; height: 8px; }}
QComboBox::drop-down {{ border: none; width: 26px; }}
QAbstractSpinBox {{ padding-right: 24px; }}
QToolTip {{ background: {t['surface']}; color: {t['text']}; border: 1px solid {t['line']}; }}
"""


def apply(app: QApplication, mode: str) -> dict:
    """Wendet ``system`` | ``hell`` | ``dunkel`` an und gibt die aktiven Tokens zurück."""
    t = DARK if is_dark(mode) else LIGHT
    _current.clear()
    _current.update(t)
    pal = QPalette()
    for role, key in ((QPalette.ColorRole.Window, "bg"), (QPalette.ColorRole.Base, "surface"),
                      (QPalette.ColorRole.AlternateBase, "bg"), (QPalette.ColorRole.Text, "text"),
                      (QPalette.ColorRole.WindowText, "text"), (QPalette.ColorRole.Button, "surface"),
                      (QPalette.ColorRole.ButtonText, "text"), (QPalette.ColorRole.Highlight, "accent"),
                      (QPalette.ColorRole.PlaceholderText, "muted"), (QPalette.ColorRole.ToolTipBase, "surface"),
                      (QPalette.ColorRole.ToolTipText, "text")):
        pal.setColor(role, QColor(t[key]))
    pal.setColor(QPalette.ColorRole.HighlightedText, QColor("#FFFFFF"))
    # ausstehende deleteLater() zuerst abarbeiten: das Neu-Polieren aller Widgets
    # darf keine bereits zum Löschen vorgemerkten Widgets mehr anfassen
    from PySide6.QtCore import QCoreApplication, QEvent

    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.setPalette(pal)
    app.setStyleSheet(stylesheet(t))
    return t
