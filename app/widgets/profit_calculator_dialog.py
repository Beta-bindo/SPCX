"""利润计算器对话框：按日期/品种查询已结算平仓记录，分页展示并支持导出 xlsx。"""

from __future__ import annotations

import threading
from datetime import date

from PySide6.QtCore import Qt, QTimer, Signal
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

from app.core.hedge_trade_report import (
    FIELD_LABELS,
    FIELD_ORDER,
    HedgeTradeReport,
    HedgeTradeRow,
)
from app.core.profit_export import export_profit_xlsx
from app.widgets.date_range_picker import DateRangePicker
from app.widgets.table_pagination import TablePagination


class ProfitCalculatorDialog(QDialog):
    """利润计算器：筛选条件 + 汇总卡 + 分页明细表 + 导出。"""

    # 后台线程查询完成后，跨线程把报表投递回主线程渲染（QueuedConnection）。
    _report_ready = Signal(object)
    _DEFAULT_COL_WIDTHS = {
        "ba_order_no": 104,
        "ex_order_no": 104,
        "product": 58,
        "direction": 58,
        "ba_qty": 78,
        "ex_qty": 78,
        "ba_open_price": 132,
        "ba_close_price": 132,
        "ba_pnl": 86,
        "ex_open_price": 132,
        "ex_close_price": 132,
        "ba_charges": 86,
        "ba_commission": 92,
        "order_time": 150,
        "net_profit": 86,
    }

    def __init__(self, parent=None, *, engine=None, trade_recorded_signal=None):
        super().__init__(parent)
        self._engine = engine
        self._querying = False
        self._query_seq = 0
        self._refresh_scheduled = False
        self.setWindowTitle("利润计算器")
        self.setWindowFlag(Qt.WindowType.WindowMinimizeButtonHint, True)
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
        self.symbol_combo.addItem("SPCXUSDT", "xag")
        self.symbol_combo.setCurrentIndex(0)

        self.calc_btn = QPushButton("查询")
        self.calc_btn.setObjectName("primaryButton")
        self.calc_btn.setMinimumSize(88, 36)
        self.calc_btn.clicked.connect(self._calculate)
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
        filter_bar.addWidget(self.calc_btn)
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
        self.ba_charges_lbl = QLabel("BA资费 —")
        self.total_lbl = QLabel("总利润 —")
        for lbl in (
            self.count_lbl,
            self.ba_pnl_lbl,
            self.mt5_pnl_lbl,
            self.fee_lbl,
            self.ba_charges_lbl,
            self.total_lbl,
        ):
            lbl.setObjectName("fieldLabel")
            summary_row.addWidget(lbl)
        summary_row.addStretch()
        root.addWidget(summary)

        self._headers = list(FIELD_ORDER)
        self.table = QTableWidget(0, len(self._headers))
        self.table.setObjectName("profitTable")
        self.table.setHorizontalHeaderLabels(self._header_labels(self._headers))
        self.table.verticalHeader().setVisible(False)
        self.table.setShowGrid(True)
        self.table.setAlternatingRowColors(True)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        hdr = self.table.horizontalHeader()
        hdr.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
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

        self._last_report: HedgeTradeReport | None = None
        self._all_rows: list[HedgeTradeRow] = []
        self._empty_message = "该时段无官方历史成交"
        self._report_ready.connect(self._apply_report)
        if trade_recorded_signal is not None:
            trade_recorded_signal.connect(self._on_trade_recorded)
        self.refresh_soon()

    def _date_range(self) -> tuple[date, date]:
        return self.date_range.get_range()

    def _on_page_size_changed(self) -> None:
        self.pagination.reset_page()
        self._render_page()

    @staticmethod
    def _header_labels(headers: list[str]) -> list[str]:
        return [FIELD_LABELS.get(h, h) for h in headers]

    def _set_headers(self, headers: list[str]) -> None:
        self._headers = headers or list(FIELD_ORDER)
        self.table.setColumnCount(len(self._headers))
        self.table.setHorizontalHeaderLabels(self._header_labels(self._headers))
        for idx, field in enumerate(self._headers):
            width = self._DEFAULT_COL_WIDTHS.get(field)
            if width:
                self.table.setColumnWidth(idx, width)

    def refresh_soon(self) -> None:
        """让窗口先显示出来，再进入官方历史查询，减少打开瞬间卡顿。"""
        if self._refresh_scheduled:
            return
        self._refresh_scheduled = True

        def _run() -> None:
            self._refresh_scheduled = False
            self._calculate()

        QTimer.singleShot(0, _run)

    def _calculate(self) -> None:
        """后台线程查询 BA/EX 官方历史成交，避免同步 HTTP 卡死 UI 主线程。"""
        if self._engine is None:
            report = HedgeTradeReport()
            report.errors.append("未连接交易引擎，无法查询官方历史成交")
            self._apply_report(report)
            return
        if self._querying:
            return
        self._querying = True
        self._query_seq += 1
        seq = self._query_seq
        start, end = self._date_range()
        symbol = self.symbol_combo.currentData()
        engine = self._engine

        self.calc_btn.setEnabled(False)
        self.calc_btn.setText("查询中…")
        if not self._all_rows:
            self._empty_message = "正在查询官方历史成交…"
            self._set_headers(list(FIELD_ORDER))
            self.pagination.set_total(0)
            self._render_page()

        def _work() -> None:
            try:
                report = engine.fetch_hedge_trade_report(start, end, symbol)
            except Exception as exc:  # noqa: BLE001
                report = HedgeTradeReport()
                report.errors.append(f"查询失败: {exc}")
            # 仅最近一次查询的结果才生效，避免过期响应覆盖新查询
            if seq == self._query_seq:
                self._report_ready.emit(report)

        threading.Thread(target=_work, daemon=True, name="profit-calc").start()

    def _apply_report(self, report: HedgeTradeReport) -> None:
        """主线程渲染查询结果（由 _report_ready 投递）。"""
        self._querying = False
        self.calc_btn.setEnabled(True)
        self.calc_btn.setText("查询")
        self._last_report = report
        count = report.row_count
        self.count_lbl.setText(f"笔数 {count}")
        self.ba_pnl_lbl.setText(f"BA盈亏 ${report.ba_pnl:+.2f}")
        self.mt5_pnl_lbl.setText(f"EX盈亏 ${report.ex_pnl:+.2f}")
        self.fee_lbl.setText(f"BA手续费 ${report.ba_commission:.4f}")
        self.ba_charges_lbl.setText(f"BA资费 ${report.ba_charges_total:+.4f}")
        self.total_lbl.setText(f"净利润 ${report.total_pnl:+.2f}")

        self._all_rows = report.rows
        self._empty_message = "；".join(report.errors) if report.errors else "该时段无官方历史成交"
        self._set_headers(report.headers)
        self.pagination.reset_page()
        self.pagination.set_total(len(self._all_rows))
        self._render_page()

    def _on_trade_recorded(self, record) -> None:
        """有新的成交上报行时，自动刷新已打开的计算器。"""
        if isinstance(record, HedgeTradeRow):
            self._calculate()

    def _render_page(self) -> None:
        """渲染当前分页的明细行；无数据时显示占位提示。"""
        self.table.clearSpans()
        page_rows = self.pagination.slice(self._all_rows)
        self.table.setRowCount(len(page_rows))

        if not self._all_rows:
            self.table.setRowCount(1)
            empty = QTableWidgetItem(self._empty_message)
            empty.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(0, 0, empty)
            self.table.setSpan(0, 0, 1, len(self._headers))
            return

        for row_idx, row in enumerate(page_rows):
            values = row.values(self._headers)
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
