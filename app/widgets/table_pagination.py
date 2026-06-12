from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QComboBox, QFrame, QHBoxLayout, QLabel, QPushButton


class TablePagination(QFrame):
    page_changed = Signal(int)
    page_size_changed = Signal(int)

    PAGE_SIZES = (10, 20, 50, 100)

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 0)
        layout.setSpacing(10)

        size_label = QLabel("每页")
        size_label.setObjectName("fieldHint")
        self.page_size = QComboBox()
        self.page_size.setFixedWidth(72)
        for n in self.PAGE_SIZES:
            self.page_size.addItem(str(n), n)
        self.page_size.setCurrentIndex(0)

        self.prev_btn = QPushButton("上一页")
        self.prev_btn.setObjectName("ghostButton")
        self.prev_btn.setMinimumSize(72, 32)
        self.next_btn = QPushButton("下一页")
        self.next_btn.setObjectName("ghostButton")
        self.next_btn.setMinimumSize(72, 32)
        self.page_info = QLabel("第 1/1 页 · 共 0 条")
        self.page_info.setObjectName("fieldHint")

        layout.addWidget(size_label)
        layout.addWidget(self.page_size)
        layout.addStretch()
        layout.addWidget(self.prev_btn)
        layout.addWidget(self.page_info)
        layout.addWidget(self.next_btn)

        self._total = 0
        self._page = 1
        self.page_size.currentIndexChanged.connect(self._on_size_changed)
        self.prev_btn.clicked.connect(self._prev)
        self.next_btn.clicked.connect(self._next)

    @property
    def current_page(self) -> int:
        return self._page

    @property
    def page_size_value(self) -> int:
        return int(self.page_size.currentData())

    def set_total(self, total: int) -> None:
        self._total = total
        max_page = max(1, (total + self.page_size_value - 1) // self.page_size_value)
        if self._page > max_page:
            self._page = max_page
        self._refresh_info()

    def reset_page(self) -> None:
        self._page = 1
        self._refresh_info()

    def _on_size_changed(self) -> None:
        self._page = 1
        self.page_size_changed.emit(self.page_size_value)
        self._refresh_info()

    def _prev(self) -> None:
        if self._page > 1:
            self._page -= 1
            self.page_changed.emit(self._page)
            self._refresh_info()

    def _next(self) -> None:
        max_page = max(1, (self._total + self.page_size_value - 1) // self.page_size_value)
        if self._page < max_page:
            self._page += 1
            self.page_changed.emit(self._page)
            self._refresh_info()

    def _refresh_info(self) -> None:
        max_page = max(1, (self._total + self.page_size_value - 1) // self.page_size_value)
        self.page_info.setText(f"第 {self._page}/{max_page} 页 · 共 {self._total} 条")
        self.prev_btn.setEnabled(self._page > 1)
        self.next_btn.setEnabled(self._page < max_page)

    def slice(self, items: list) -> list:
        size = self.page_size_value
        start = (self._page - 1) * size
        return items[start : start + size]
