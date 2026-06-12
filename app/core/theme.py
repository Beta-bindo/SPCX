"""Theme loading and switching."""


from __future__ import annotations
import sys
from pathlib import Path

from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication, QWidget

THEMES = {
    "dark": "dark.qss",
    "light": "light.qss",
}

UI_FONT_FAMILY = "SimSun"
UI_FONT_FAMILY_FALLBACK = "宋体"
UI_FONT_FAMILY_QSS = '"SimSun", "宋体", serif'


def ui_font(
    point_size: int | None = None,
    *,
    pixel_size: int | None = None,
    weight: QFont.Weight | None = None,
) -> QFont:
    font = QFont(UI_FONT_FAMILY)
    if not font.exactMatch():
        font = QFont(UI_FONT_FAMILY_FALLBACK)
    if point_size is not None:
        font.setPointSize(point_size)
    if pixel_size is not None:
        font.setPixelSize(pixel_size)
    if weight is not None:
        font.setWeight(weight)
    return font


def apply_app_font(app: QApplication) -> None:
    app.setFont(ui_font(9))


def project_root() -> Path:
    if hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parent.parent.parent


def stylesheet_path(theme: str) -> Path:
    filename = THEMES.get(theme, THEMES["light"])
    return project_root() / "app" / "styles" / filename


def polish_widget(widget: QWidget) -> None:
    widget.style().unpolish(widget)
    widget.style().polish(widget)


def repolish_tree(root: QWidget) -> None:
    polish_widget(root)
    for child in root.findChildren(QWidget):
        polish_widget(child)


def ui_mono_font(
    point_size: int | None = None,
    *,
    pixel_size: int | None = None,
    weight: QFont.Weight | None = None,
) -> QFont:
    font = QFont("Menlo")
    if not font.exactMatch():
        font = QFont("Consolas")
    if not font.exactMatch():
        font = QFont("Courier New")
    font.setStyleHint(QFont.StyleHint.Monospace)
    if point_size is not None:
        font.setPointSize(point_size)
    if pixel_size is not None:
        font.setPixelSize(pixel_size)
    if weight is not None:
        font.setWeight(weight)
    return font


def set_flag(widget: QWidget, name: str, value: bool) -> None:
    new = "true" if value else "false"
    if widget.property(name) == new:
        return
    widget.setProperty(name, new)
    polish_widget(widget)


def load_stylesheet(app: QApplication, theme: str = "light") -> None:
    path = stylesheet_path(theme)
    if not path.exists():
        raise FileNotFoundError(f"主题文件不存在: {path}")
    icons = (path.parent / "icons").as_posix()
    text = path.read_text(encoding="utf-8").replace("@ICON_DIR@", icons)
    unchecked = (
        "checkbox-unchecked-dark.svg" if theme == "dark" else "checkbox-unchecked-light.svg"
    )
    text = text.replace("@CHECKBOX_UNCHECKED@", f"{icons}/{unchecked}")
    text = text.replace("@CHECKBOX_CHECKED@", f"{icons}/checkbox-checked.svg")
    app.setStyle("Fusion")
    apply_app_font(app)
    app.setStyleSheet(text)
