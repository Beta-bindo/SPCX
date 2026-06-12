"""运行日志面板：只读文本框，最多保留 500 行，带时间戳追加。"""

from __future__ import annotations

from datetime import datetime

from PySide6.QtWidgets import QFrame, QLabel, QPlainTextEdit, QSizePolicy, QVBoxLayout


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
        """追加一行带 HH:MM:SS 时间戳的日志。"""
        ts = datetime.now().strftime("%H:%M:%S")
        self.text.appendPlainText(f"[{ts}] {message}")
