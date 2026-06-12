"""通用 UI 组件：带标题的配置卡片 SectionCard，提供统一的字段排版辅助。"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)


class SectionCard(QFrame):
    """带标题栏的配置卡片；支持紧凑模式，并提供多种字段添加布局。"""

    def __init__(
        self,
        title: str,
        badge: str = "",
        accent: str = "#38bdf8",
        *,
        compact: bool = False,
        parent=None,
    ):
        super().__init__(parent)
        self.setObjectName("sectionCard")
        self.setProperty("accent", accent)
        self.setProperty("compact", "true" if compact else "false")
        self._compact = compact
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

        root = QVBoxLayout(self)
        pad = 6 if compact else 16
        root.setContentsMargins(pad, 4 if compact else 14, pad, 6 if compact else 16)
        root.setSpacing(3 if compact else 10)

        header = QHBoxLayout()
        title_wrap = QVBoxLayout()
        title_wrap.setSpacing(2)
        self.title_label = QLabel(title)
        self.title_label.setObjectName("cardTitle")
        title_wrap.addWidget(self.title_label)
        header.addLayout(title_wrap)
        header.addStretch()
        if badge:
            self.badge = QLabel(badge)
            self.badge.setObjectName("cardBadge")
            header.addWidget(self.badge)
        else:
            self.badge = None
        root.addLayout(header)

        self.body = QVBoxLayout()
        self.body.setSpacing(3 if compact else 10)
        root.addLayout(self.body)

    def _cell_spacing(self) -> tuple[int, int]:
        if self._compact:
            return 1, 4
        return 4, 10

    def _wrap_layout(self, layout: QVBoxLayout) -> QWidget:
        wrap = QWidget()
        wrap.setLayout(layout)
        wrap.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        return wrap

    def _field_cell(self, label: str, widget: QWidget, hint: str = "") -> QWidget:
        v_gap, _ = self._cell_spacing()
        wrap = QVBoxLayout()
        wrap.setSpacing(v_gap)
        wrap.setContentsMargins(0, 0, 0, 0)
        lbl = QLabel(label)
        lbl.setObjectName("fieldLabel")
        wrap.addWidget(lbl)
        widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        wrap.addWidget(widget)
        if hint:
            hint_lbl = QLabel(hint)
            hint_lbl.setObjectName("fieldHint")
            hint_lbl.setWordWrap(True)
            wrap.addWidget(hint_lbl)
        return self._wrap_layout(wrap)

    def add_field(self, label: str, widget: QWidget, hint: str = "") -> None:
        """竖排添加一个字段（标签在上、控件在下，可选提示）。"""
        self.body.addWidget(self._field_cell(label, widget, hint))

    def add_inline_field(
        self,
        label: str,
        widget: QWidget,
        hint: str = "",
        *,
        label_width: int | None = None,
    ) -> None:
        """横排添加一个字段（标签居左、控件占满剩余宽度）。"""
        if label_width is None:
            label_width = 56 if self._compact else 76
        v_gap, h_gap = self._cell_spacing()
        block = QVBoxLayout()
        block.setSpacing(v_gap)
        block.setContentsMargins(0, 0, 0, 0)
        row = QHBoxLayout()
        row.setSpacing(h_gap)
        lbl = QLabel(label)
        lbl.setObjectName("fieldLabel")
        lbl.setMinimumWidth(label_width)
        lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(lbl)
        widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        row.addWidget(widget, stretch=1)
        block.addLayout(row)
        if hint:
            hint_lbl = QLabel(hint)
            hint_lbl.setObjectName("fieldHint")
            hint_lbl.setWordWrap(True)
            block.addWidget(hint_lbl)
        self.body.addWidget(self._wrap_layout(block))

    def add_grid_fields(
        self,
        fields: list[tuple[str, QWidget, str]],
        *,
        columns: int = 2,
    ) -> None:
        """以网格（默认两列）批量添加字段。"""
        _, h_gap = self._cell_spacing()
        v_gap = h_gap
        grid = QGridLayout()
        grid.setHorizontalSpacing(h_gap)
        grid.setVerticalSpacing(v_gap)
        grid.setContentsMargins(0, 0, 0, 0)
        for idx, (label, widget, hint) in enumerate(fields):
            row, col = divmod(idx, columns)
            grid.addWidget(self._field_cell(label, widget, hint), row, col)
        for col in range(columns):
            grid.setColumnStretch(col, 1)
        self.body.addLayout(grid)
