"""日期区间选择器：起止两个日期框，用于收益统计筛选。"""

from __future__ import annotations

from datetime import date

from PySide6.QtCore import QDate, Signal
from PySide6.QtWidgets import QDateEdit, QFrame, QHBoxLayout, QLabel


class DateRangePicker(QFrame):
    """开始/结束日期选择控件，变更时发出 range_changed。"""

    range_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        today = QDate.currentDate()
        self.start_edit = QDateEdit(today)
        self.end_edit = QDateEdit(today)
        for edit in (self.start_edit, self.end_edit):
            edit.setCalendarPopup(True)
            edit.setDisplayFormat("yyyy-MM-dd")
            edit.setFixedWidth(118)
            edit.dateChanged.connect(lambda _d: self.range_changed.emit())

        arrow = QLabel("→")
        arrow.setObjectName("fieldHint")
        layout.addWidget(self.start_edit)
        layout.addWidget(arrow)
        layout.addWidget(self.end_edit)

    def set_range(self, start: date, end: date) -> None:
        """设置起止日期。"""
        self.start_edit.setDate(QDate(start.year, start.month, start.day))
        self.end_edit.setDate(QDate(end.year, end.month, end.day))

    def get_range(self) -> tuple[date, date]:
        """返回 (起, 止)，自动纠正颠倒的区间。"""
        start = self.start_edit.date().toPython()
        end = self.end_edit.date().toPython()
        if end < start:
            start, end = end, start
        return start, end
