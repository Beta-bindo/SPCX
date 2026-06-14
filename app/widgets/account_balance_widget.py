"""账户资金展示徽标与 BA 现货↔合约划转对话框。

- PlatformAccountRow：平台连接状态 + 资金四字段 + 划转，合并为单行徽标。
- BalanceTransferDialog：BA 现货钱包 ↔ U 本位合约钱包划转（USDT）。

EX（MT5/Exness）无法通过 API 划转资金，其「划转」按钮改为打开 Exness 官网。
"""

from __future__ import annotations

import threading

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
)

from app.core.models import AccountSnapshot
from app.core.theme import polish_widget


def _fmt(value: float) -> str:
    return f"{value:,.2f}"


class PlatformAccountRow(QFrame):
    """平台单行徽标：连接状态（含延迟/WS）+ 资金四字段 + 划转按钮。"""

    transfer_clicked = Signal()

    _FIELDS = (
        ("cash", "现金余额"),
        ("contract", "合约余额"),
        ("equity", "保证金余额"),
        ("used", "已用保证金"),
    )

    def __init__(
        self,
        name: str,
        *,
        is_ba: bool = False,
        currency_hint: str = "",
        parent=None,
    ):
        super().__init__(parent)
        self._platform_name = name
        self._is_ba = is_ba
        self._currency = currency_hint
        self.setObjectName("statusBadge")
        self.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 3, 8, 3)
        layout.setSpacing(6)
        layout.setSizeConstraint(QHBoxLayout.SizeConstraint.SetMinimumSize)

        self.dot = QFrame()
        self.dot.setFixedSize(8, 8)
        self.dot.setObjectName("statusDotDisconnected")
        layout.addWidget(self.dot, 0, Qt.AlignmentFlag.AlignVCenter)

        self.status_label = QLabel(f"{name} - 未连接")
        self.status_label.setObjectName("statusText")
        self.status_label.setSizePolicy(
            QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Preferred
        )
        layout.addWidget(self.status_label, 0, Qt.AlignmentFlag.AlignVCenter)
        layout.addSpacing(4)

        self._dot_name = "statusDotDisconnected"
        self._status_text = self.status_label.text()
        self._balance_labels: dict[str, QLabel] = {}
        for key, title in self._FIELDS:
            lbl = QLabel(f"{title} --")
            lbl.setObjectName("statusText")
            lbl.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Preferred)
            self._balance_labels[key] = lbl
            layout.addWidget(lbl, 0, Qt.AlignmentFlag.AlignVCenter)

        self.transfer_btn = QPushButton("划转")
        self.transfer_btn.setObjectName("ghostButton")
        self.transfer_btn.setProperty("compact", "true")
        self.transfer_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.transfer_btn.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        btn_w = self.transfer_btn.fontMetrics().horizontalAdvance("划转") + 18
        self.transfer_btn.setMinimumWidth(btn_w)
        self.transfer_btn.clicked.connect(self.transfer_clicked.emit)
        layout.addWidget(self.transfer_btn, 0, Qt.AlignmentFlag.AlignVCenter)

    @staticmethod
    def _latency_suffix(ms: float | None) -> str:
        if ms is None:
            return " ----ms"
        value = min(max(int(round(ms)), 0), 9999)
        return f" {value:4d}ms"

    def update_status(
        self,
        *,
        conn_state: str,
        live: bool,
        running: bool,
        latency_ms: float | None,
        slow: bool = False,
        ws_mode: str = "off",
    ) -> None:
        """刷新连接状态文案与圆点颜色。"""
        name = self._platform_name
        if not running:
            label_text = f"{name} - 未启动"
            dot_name = "statusDotDisconnected"
        elif live and conn_state == "error":
            label_text = f"{name} - 异常"
            dot_name = "statusDotError"
        elif live and conn_state == "disconnected":
            label_text = f"{name} - 断网"
            dot_name = "statusDotError"
        elif conn_state == "simulated" or not live:
            suffix = self._latency_suffix(latency_ms)
            label_text = f"{name} - 模拟数据{suffix}"
            dot_name = "statusDotSimulated"
        elif self._is_ba and conn_state == "connected":
            if ws_mode == "rest":
                label_text = f"{name}-rest"
                dot_name = "statusDotSlow" if slow else "statusDotConnected"
            elif ws_mode in ("streaming", "connecting"):
                label_text = f"{name}-websocket"
                dot_name = (
                    "statusDotDisconnected"
                    if ws_mode == "connecting"
                    else ("statusDotSlow" if slow else "statusDotConnected")
                )
            else:
                label_text = f"{name}-关闭"
                dot_name = "statusDotDisconnected"
        elif conn_state == "connected":
            suffix = self._latency_suffix(latency_ms)
            label_text = f"{name} - 真实连接{suffix}"
            dot_name = "statusDotSlow" if slow else "statusDotConnected"
        else:
            state_text = {
                "connecting": "连接中",
                "disconnected": "未连接",
                "error": "异常",
            }.get(conn_state, "未连接")
            label_text = f"{name} - {state_text}"
            dot_name = {
                "connecting": "statusDotDisconnected",
                "error": "statusDotError",
            }.get(conn_state, "statusDotDisconnected")

        if label_text != self._status_text:
            self.status_label.setText(label_text)
            self._status_text = label_text
        if dot_name != self._dot_name:
            self._dot_name = dot_name
            self.dot.setObjectName(dot_name)
            polish_widget(self.dot)

    def set_snapshot(self, snap: AccountSnapshot) -> None:
        """根据账户快照刷新资金字段；非实盘或缺失字段显示 '--'。"""
        if snap is None or not snap.is_live:
            for key, title in self._FIELDS:
                self._balance_labels[key].setText(f"{title} --")
            self.setToolTip("")
            return
        cur = snap.currency or self._currency
        values = {
            "cash": snap.cash_balance,
            "contract": snap.balance,
            "equity": snap.equity,
            "used": snap.used_margin,
        }
        for key, title in self._FIELDS:
            v = values[key]
            if key == "cash" and snap.platform == "MT5":
                self._balance_labels[key].setText(f"{title} --")
            else:
                self._balance_labels[key].setText(f"{title} {_fmt(v)}")
        suffix = f" {cur}" if cur else ""
        self.setToolTip(
            f"现金钱包余额 {_fmt(snap.cash_balance)}{suffix}\n"
            f"合约钱包余额 {_fmt(snap.balance)}{suffix}\n"
            f"保证金余额(净值) {_fmt(snap.equity)}{suffix}\n"
            f"已用保证金 {_fmt(snap.used_margin)}{suffix}\n"
            f"可用保证金 {_fmt(snap.free_margin)}{suffix}"
        )


# 兼容旧名
AccountBalanceBadge = PlatformAccountRow


class BalanceTransferDialog(QDialog):
    """币安资金划转 / 逐仓持仓保证金调整（USDT）。

    支持 4 种操作：
    - 现货 → 合约钱包（余额划入合约）
    - 合约钱包 → 现货（合约转出到余额）
    - 合约钱包 → 逐仓持仓（给持仓添加保证金）
    - 逐仓持仓 → 合约钱包（持仓保证金转出）

    后两种需选择品种，且仅对「逐仓」持仓有效。

    wallet_transfer_fn(amount, to_futures) -> (ok, msg)
    position_margin_fn(symbol, amount, add) -> (ok, msg)
    symbol_options: [(label, symbol)]，用于持仓保证金操作的品种选择。
    所有回调均在后台线程执行，避免阻塞 UI。
    """

    _result_ready = Signal(bool, str)

    # operation userData: (kind, flag)  kind: "wallet"/"position"
    _OPERATIONS = (
        ("余额划入合约（现货 → 合约钱包）", ("wallet", True)),
        ("合约转出余额（合约钱包 → 现货）", ("wallet", False)),
        ("给持仓添加保证金（合约钱包 → 逐仓持仓）", ("position", True)),
        ("持仓保证金转出（逐仓持仓 → 合约钱包）", ("position", False)),
    )

    def __init__(
        self,
        wallet_transfer_fn,
        position_margin_fn=None,
        symbol_options=None,
        available_futures: float | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self._wallet_transfer_fn = wallet_transfer_fn
        self._position_margin_fn = position_margin_fn
        self.setWindowTitle("币安资金划转 / 逐仓保证金")
        self.setModal(True)
        self.setMinimumWidth(380)

        root = QVBoxLayout(self)
        root.setSpacing(10)

        hint = QLabel(
            "现货钱包 ↔ U 本位合约钱包划转，或给某个「逐仓」持仓加/减保证金（USDT）。\n"
            "持仓加/减保证金仅对逐仓持仓有效；全仓无需手动调整。"
        )
        hint.setWordWrap(True)
        hint.setObjectName("fieldHint")
        root.addWidget(hint)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        self.operation = QComboBox()
        for label, data in self._OPERATIONS:
            self.operation.addItem(label, userData=data)
        self.operation.currentIndexChanged.connect(self._on_operation_changed)
        form.addRow("操作", self.operation)

        self.symbol = QComboBox()
        for label, sym in (symbol_options or []):
            self.symbol.addItem(label, userData=sym)
        self.symbol_row_label = QLabel("品种")
        form.addRow(self.symbol_row_label, self.symbol)

        self.amount = QDoubleSpinBox()
        self.amount.setDecimals(2)
        self.amount.setRange(0.0, 10_000_000.0)
        self.amount.setSingleStep(10.0)
        self.amount.setSuffix(" USDT")
        form.addRow("金额", self.amount)
        root.addLayout(form)

        if available_futures is not None:
            self.avail_label = QLabel(f"合约可用余额：约 {_fmt(available_futures)} USDT")
            self.avail_label.setObjectName("fieldHint")
            root.addWidget(self.avail_label)

        self.status_label = QLabel("")
        self.status_label.setObjectName("fieldHint")
        self.status_label.setWordWrap(True)
        self.status_label.setVisible(False)
        root.addWidget(self.status_label)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText("确认")
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        self.buttons.accepted.connect(self._on_submit)
        self.buttons.rejected.connect(self.reject)
        root.addWidget(self.buttons)

        self._result_ready.connect(self._on_result)
        self._on_operation_changed()

    def _current_op(self) -> tuple[str, bool]:
        data = self.operation.currentData()
        return data if data else ("wallet", True)

    def _on_operation_changed(self) -> None:
        kind, _flag = self._current_op()
        is_position = kind == "position"
        self.symbol.setVisible(is_position)
        self.symbol_row_label.setVisible(is_position)

    def _set_busy(self, busy: bool) -> None:
        self.buttons.setEnabled(not busy)
        self.operation.setEnabled(not busy)
        self.symbol.setEnabled(not busy)
        self.amount.setEnabled(not busy)

    def _on_submit(self) -> None:
        amount = round(float(self.amount.value()), 2)
        if amount <= 0:
            self._show_status("请输入大于 0 的金额")
            return
        kind, flag = self._current_op()
        if kind == "position":
            if self._position_margin_fn is None or self.symbol.count() == 0:
                self._show_status("当前无可调整保证金的品种")
                return
            symbol = str(self.symbol.currentData() or "")
            fn = lambda: self._position_margin_fn(symbol, amount, flag)
            busy_text = "调整持仓保证金中，请稍候…"
        else:
            fn = lambda: self._wallet_transfer_fn(amount, flag)
            busy_text = "划转中，请稍候…"
        self._set_busy(True)
        self._show_status(busy_text)

        def _worker() -> None:
            try:
                ok, msg = fn()
            except Exception as exc:  # noqa: BLE001 - 兜底，避免线程内异常吞掉
                ok, msg = False, str(exc)
            self._result_ready.emit(bool(ok), str(msg))

        threading.Thread(target=_worker, daemon=True, name="ba-fund-op").start()

    def _on_result(self, ok: bool, msg: str) -> None:
        self._set_busy(False)
        if ok:
            self._show_status(msg)
            self.accept()
        else:
            self._show_status(f"操作失败：{msg}")

    def _show_status(self, text: str) -> None:
        self.status_label.setText(text)
        self.status_label.setVisible(bool(text))
