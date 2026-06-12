"""利润计算器对话框：按日期/品种查询已结算平仓记录，分页展示并支持导出 xlsx。"""

from __future__ import annotations

from datetime import date

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from app.core.profit_calculator import ProfitRow, calculate_profit
from app.core.profit_export import export_profit_xlsx
from app.core.trade_ledger import load_ledger
from app.widgets.date_range_picker import DateRangePicker
from app.widgets.table_pagination import TablePagination


class ProfitCalculatorDialog(QDialog):
    """利润计算器：筛选条件 + 汇总卡 + 分页明细表 + 导出。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("利润计算器")
        self.resize(1080, 640)
        self.setMinimumSize(860, 540)

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 16)
        root.setSpacing(14)

        title = QLabel("利润计算器")
        title.setObjectName("dialogTitle")
        root.addWidget(title)

        filter_bar = QHBoxLayout()
        filter_bar.setSpacing(16)
        date_label = QLabel("结算日期")
        date_label.setObjectName("fieldLabel")
        self.date_range = DateRangePicker()
        product_label = QLabel("品种")
        product_label.setObjectName("fieldLabel")
        self.symbol_combo = QComboBox()
        self.symbol_combo.setFixedWidth(140)
        self.symbol_combo.addItem("全部", "all")
        self.symbol_combo.addItem("黄金", "xau")
        self.symbol_combo.addItem("白银", "xag")
        self.symbol_combo.setCurrentIndex(0)

        calc_btn = QPushButton("查询")
        calc_btn.setObjectName("primaryButton")
        calc_btn.setMinimumSize(88, 36)
        calc_btn.clicked.connect(self._calculate)
        export_btn = QPushButton("导出记录")
        export_btn.setObjectName("ghostButton")
        export_btn.setMinimumSize(96, 36)
        export_btn.clicked.connect(self._export)

        filter_bar.addWidget(date_label)
        filter_bar.addWidget(self.date_range)
        filter_bar.addSpacing(8)
        filter_bar.addWidget(product_label)
        filter_bar.addWidget(self.symbol_combo)
        filter_bar.addStretch()
        filter_bar.addWidget(calc_btn)
        filter_bar.addWidget(export_btn)
        root.addLayout(filter_bar)

        summary = QFrame()
        summary.setObjectName("card")
        summary_row = QHBoxLayout(summary)
        summary_row.setContentsMargins(14, 10, 14, 10)
        summary_row.setSpacing(18)
        self.count_lbl = QLabel("笔数 —")
        self.ba_pnl_lbl = QLabel("BA利润 —")
        self.mt5_pnl_lbl = QLabel("Exness利润 —")
        self.fee_lbl = QLabel("总手续费 —")
        self.total_lbl = QLabel("总利润 —")
        for lbl in (
            self.count_lbl,
            self.ba_pnl_lbl,
            self.mt5_pnl_lbl,
            self.fee_lbl,
            self.total_lbl,
        ):
            lbl.setObjectName("fieldLabel")
            summary_row.addWidget(lbl)
        summary_row.addStretch()
        root.addWidget(summary)

        self._headers = [
            "结算时间",
            "产品",
            "方向",
            "BA数量",
            "EX手数",
            "点差",
            "BA盈亏",
            "EX盈亏",
            "手续费",
            "净利润",
        ]
        self.table = QTableWidget(0, len(self._headers))
        self.table.setObjectName("profitTable")
        self.table.setHorizontalHeaderLabels(self._headers)
        self.table.verticalHeader().setVisible(False)
        self.table.setShowGrid(True)
        self.table.setAlternatingRowColors(True)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        hdr = self.table.horizontalHeader()
        hdr.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        hdr.setStretchLastSection(True)
        hdr.setDefaultAlignment(Qt.AlignmentFlag.AlignCenter)
        hdr.setMinimumHeight(36)
        hdr.setFixedHeight(36)
        self.table.verticalHeader().setDefaultSectionSize(34)
        self.table.setMinimumHeight(360)
        root.addWidget(self.table, stretch=1)

        self.pagination = TablePagination()
        self.pagination.page_changed.connect(lambda _: self._render_page())
        self.pagination.page_size_changed.connect(lambda _: self._on_page_size_changed())
        root.addWidget(self.pagination)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        close_btn = buttons.button(QDialogButtonBox.StandardButton.Close)
        if close_btn:
            close_btn.setText("关闭")
        root.addWidget(buttons)

        self._last_report = None
        self._all_rows: list[ProfitRow] = []
        self._calculate()

    def _date_range(self) -> tuple[date, date]:
        return self.date_range.get_range()

    def _on_page_size_changed(self) -> None:
        self.pagination.reset_page()
        self._render_page()

    def _calculate(self) -> None:
        """按当前筛选条件统计利润并刷新汇总与表格。"""
        start, end = self._date_range()
        symbol = self.symbol_combo.currentData()
        report = calculate_profit(load_ledger(), start, end, symbol)
        self._last_report = report
        count = len(report.records or [])
        self.count_lbl.setText(f"笔数 {count}")
        self.ba_pnl_lbl.setText(f"BA利润 ${report.ba_pnl:+.2f}")
        self.mt5_pnl_lbl.setText(f"Exness利润 ${report.mt5_pnl:+.2f}")
        self.fee_lbl.setText(f"总手续费 ${report.ba_fee + report.mt5_fee:.4f}")
        self.total_lbl.setText(f"总利润 ${report.total_pnl:+.2f}")

        self._all_rows = report.rows
        self.pagination.reset_page()
        self.pagination.set_total(len(self._all_rows))
        self._render_page()

    def _render_page(self) -> None:
        """渲染当前分页的明细行；无数据时显示占位提示。"""
        self.table.clearSpans()
        page_rows = self.pagination.slice(self._all_rows)
        self.table.setRowCount(len(page_rows))

        if not self._all_rows:
            self.table.setRowCount(1)
            empty = QTableWidgetItem("该时段无已结算平仓记录")
            empty.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(0, 0, empty)
            self.table.setSpan(0, 0, 1, len(self._headers))
            return

        for row_idx, row in enumerate(page_rows):
            values = [
                row.settled_at,
                row.product,
                row.direction,
                f"{row.ba_qty:g}",
                f"{row.mt5_qty:g}",
                f"{row.spread:+.3f}",
                f"${row.ba_pnl:+.2f}",
                f"${row.ex_pnl:+.2f}",
                f"${row.fee:.4f}",
                f"${row.profit:+.2f}",
            ]
            for col, text in enumerate(values):
                item = QTableWidgetItem(text)
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.table.setItem(row_idx, col, item)

        self.pagination.set_total(len(self._all_rows))

    def _export(self) -> None:
        """把当前报表导出为 xlsx 文件，并提示导出路径。"""
        if self._last_report is None:
            self._calculate()
        if self._last_report is None:
            return
        start, end = self._date_range()
        symbol = self.symbol_combo.currentData()
        try:
            out = export_profit_xlsx(self._last_report, symbol, start, end)
        except OSError as exc:
            QMessageBox.warning(self, "导出失败", str(exc))
            return
        QMessageBox.information(self, "导出成功", f"已导出至\n{out}")
