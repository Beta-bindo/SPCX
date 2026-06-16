"""运行日志面板：只读文本框，最多保留 500 行，带时间戳追加。"""

from __future__ import annotations

import html
import re
from datetime import datetime

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QFrame,
    QPlainTextEdit,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

# 下单/结算日志中需要红色突出显示的片段：点差、BA/EX 成交价
_HILITE_LABEL = re.compile(
    r"(入场点差指数|点差指数|点差|@价)\s*([+-]?\d+(?:\.\d+)?)"
)
_HILITE_PRICE = re.compile(r"\b(BA|Ex|EX)\s+([+-]?\d+\.\d+)")
_HILITE_STYLE = "color:#e5484d;font-weight:bold"


class _DragGrip(QWidget):
    """日志面板顶部的拖拽条：上下拖动改变日志面板高度（覆盖式，不挤压上方）。"""

    dragged = Signal(int)  # 相对上次的位移 dy（向上为负、向下为正）

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("logGrip")
        self.setFixedHeight(7)
        self.setCursor(Qt.CursorShape.SizeVerCursor)
        self._last_y: int | None = None

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._last_y = event.globalPosition().toPoint().y()
            event.accept()

    def mouseMoveEvent(self, event):
        if self._last_y is None:
            return
        y = event.globalPosition().toPoint().y()
        dy = y - self._last_y
        self._last_y = y
        if dy:
            self.dragged.emit(dy)
        event.accept()

    def mouseReleaseEvent(self, event):
        self._last_y = None
        event.accept()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(150, 150, 150, 150))
        bar_w = 48
        x = (self.width() - bar_w) // 2
        y = (self.height() - 3) // 2
        painter.drawRoundedRect(x, y, bar_w, 3, 1, 1)
        painter.end()


class LogPanel(QFrame):
    """滚动展示运行日志的卡片。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("card")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 2, 12, 12)
        layout.setSpacing(4)

        # 顶部拖拽条：向上拖动日志面板以遮住上方交易区（不压缩交易区）
        self.grip = _DragGrip(self)
        layout.addWidget(self.grip)

        self.text = QPlainTextEdit()
        self.text.setObjectName("logPanel")
        self.text.setReadOnly(True)
        self.text.setMaximumBlockCount(500)
        self.text.setMinimumHeight(48)
        layout.addWidget(self.text, stretch=1)

    def append(self, message: str) -> None:
        """追加一行带 HH:MM:SS 时间戳的日志；下单点数/点差红色突出。"""
        ts = datetime.now().strftime("%H:%M:%S")
        body = html.escape(message)
        body = _HILITE_LABEL.sub(
            rf'\1 <span style="{_HILITE_STYLE}">\2</span>', body
        )
        body = _HILITE_PRICE.sub(
            rf'<span style="{_HILITE_STYLE}">\1 \2</span>', body
        )
        self.text.appendHtml(f'<span>[{ts}] {body}</span>')
