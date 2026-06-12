"""应用名称与图标（任务栏 / 窗口 / 打包资源）。"""


from __future__ import annotations
import sys
from pathlib import Path

APP_NAME = "交易助手"
APP_ORG = "TradeAssistant"


def bundle_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS) / "app"
    return Path(__file__).resolve().parent.parent


def app_icon_path() -> Path | None:
    res = bundle_root() / "resources"
    if sys.platform == "darwin":
        icns = res / "icon.icns"
        if icns.is_file():
            return icns
    if sys.platform == "win32":
        ico = res / "icon.ico"
        if ico.is_file():
            return ico
    png = res / "icon.png"
    return png if png.is_file() else None


def apply_app_branding(app) -> None:
    """设置 QApplication 显示名称与图标。"""
    from PySide6.QtGui import QIcon

    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName(APP_NAME)
    app.setOrganizationName(APP_ORG)

    path = app_icon_path()
    if path is None:
        return
    icon = QIcon(str(path))
    if not icon.isNull():
        app.setWindowIcon(icon)
