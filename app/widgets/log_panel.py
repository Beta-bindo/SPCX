"""运行日志面板：只读文本框，最多保留 500 行，带时间戳追加。"""

from __future__ import annotations

import html
import re
from datetime import datetime

from PySide6.QtWidgets import QFrame, QLabel, QPlainTextEdit, QSizePolicy, QVBoxLayout

# 下单/结算日志中需要红色突出显示的片段：点差、BA/EX 成交价
_HILITE_LABEL = re.compile(
    r"(入场点差指数|点差指数|点差|@价)\s*([+-]?\d+(?:\.\d+)?)"
)
_HILITE_PRICE = re.compile(r"\b(BA|Ex|EX)\s+([+-]?\d+\.\d+)")
_HILITE_STYLE = "color:#e5484d;font-weight:bold"


class LogPanel(QFrame):
    """滚动展示运行日志的卡片。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("card")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 16)
        layout.setSpacing(8)

        title = QLabel("运行日志")
        title.setObjectName("cardTitle")
        layout.addWidget(title)

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
