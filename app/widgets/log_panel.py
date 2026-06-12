from __future__ import annotations

from datetime import datetime

from PySide6.QtWidgets import QFrame, QLabel, QPlainTextEdit, QSizePolicy, QVBoxLayout


class LogPanel(QFrame):
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
        ts = datetime.now().strftime("%H:%M:%S")
        self.text.appendPlainText(f"[{ts}] {message}")
