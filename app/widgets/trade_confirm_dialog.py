from __future__ import annotations

from typing import Callable

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QCloseEvent, QMouseEvent, QMoveEvent, QShowEvent
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QRadioButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from app.core.models import AppConfig, GoldOrderMode, HedgeMode, Position, Quote
from app.widgets.symbol_alert_settings import ClickToEditDoubleSpinBox
from app.widgets.symbol_ratio_fields import SymbolRatioFields
from app.widgets.symbol_trade_panel import SYMBOL_ICON


class TradeConfirmDialog(QWidget):
    class DialogCode:
        Accepted = 1
        Rejected = 0

    trade_requested = Signal(str, str)
    closed = Signal(int)

    def __init__(
        self,
        preset_id: str,
        config: AppConfig,
        active_mode: str | None = None,
        parent=None,
    ):
        super().__init__(
            parent,
            Qt.WindowType.Tool
            | Qt.WindowType.WindowTitleHint
            | Qt.WindowType.WindowCloseButtonHint,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        self.setObjectName(f"tradeConfirmDialog_{preset_id}")

        self._preset_id = preset_id
        self._config = config
        self._active_mode = active_mode
        self._action: str | None = None
        self._mode: str | None = None
        self._action_buttons: list[QPushButton] = []
        self._position_callback: Callable[[], None] | None = None
        self._user_positioned = False
        self._auto_positioning = False

        label = "黄金" if preset_id == "xau" else "白银"
        self.setWindowTitle(f"{label}对冲交易")
        self.setMinimumWidth(380)

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 12, 16, 16)
        root.setSpacing(8)

        self._ratio_fields = SymbolRatioFields(preset_id, config)
        root.addWidget(self._ratio_fields)

        root.addWidget(QLabel("收缩：BA空+Ex多 · 扩张：BA多+Ex空", objectName="fieldHint"))

        self._order_mode_maker: QRadioButton | None = None
        self._order_mode_market: QRadioButton | None = None
        mode_row = QHBoxLayout()
        mode_row.setContentsMargins(0, 0, 0, 0)
        mode_row.setSpacing(8)
        mode_lbl = QLabel("下单模式")
        mode_lbl.setObjectName("fieldLabel")
        self._order_mode_maker = QRadioButton("限价·只做Maker")
        self._order_mode_maker.setObjectName("settingsCheck")
        self._order_mode_market = QRadioButton("市价")
        self._order_mode_market.setObjectName("settingsCheck")
        if preset_id == "xag":
            self._order_mode_market.setChecked(True)
        else:
            self._order_mode_maker.setChecked(True)
        mode_row.addWidget(mode_lbl)
        mode_row.addWidget(self._order_mode_maker)
        mode_row.addWidget(self._order_mode_market)
        mode_row.addStretch()
        root.addLayout(mode_row)

        self._actions_host = QWidget()
        self._actions_host_layout = QVBoxLayout(self._actions_host)
        self._actions_host_layout.setContentsMargins(0, 0, 0, 0)
        self._actions_host_layout.setSpacing(0)
        root.addWidget(self._actions_host)
        self._rebuild_actions()

    def set_position_callback(self, callback: Callable[[], None] | None) -> None:
        self._position_callback = callback

    def closeEvent(self, event: QCloseEvent) -> None:
        self.closed.emit(0)
        super().closeEvent(event)

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        self._ratio_fields.lock_all_spins()
        self._fit_size()

    def moveEvent(self, event: QMoveEvent) -> None:
        super().moveEvent(event)
        if self.isVisible() and not self._auto_positioning:
            self._user_positioned = True

    def set_auto_positioning(self, active: bool) -> None:
        self._auto_positioning = active

    def user_positioned(self) -> bool:
        return self._user_positioned

    def mousePressEvent(self, event: QMouseEvent) -> None:
        target = self.childAt(event.pos())
        while target is not None and target is not self:
            if isinstance(target, ClickToEditDoubleSpinBox):
                break
            target = target.parentWidget()
        else:
            self._ratio_fields.lock_all_spins()
        super().mousePressEvent(event)

    def _fit_size(self) -> None:
        """按内容高度展开，避免底部交易按钮被裁切。"""
        lay = self.layout()
        if lay is None:
            return
        lay.activate()
        content = lay.sizeHint()
        width = max(400, content.width())
        height = max(320, content.height() + 12)
        self.setMinimumSize(width, height)
        if self.width() < width or self.height() < height:
            self.resize(width, height)

    @property
    def preset_id(self) -> str:
        return self._preset_id

    def update_pnl(
        self,
        positions: list[Position],
        ba_quotes: dict[str, Quote],
        mt5_quotes: dict[str, Quote],
        config: AppConfig,
    ) -> None:
        """弹窗不再展示盈亏明细，保留接口兼容主窗口调用。"""
        return

    def _clear_layout(self, layout: QVBoxLayout | QHBoxLayout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
                continue
            nested = item.layout()
            if nested is not None:
                self._clear_layout(nested)

    def _rebuild_actions(self) -> None:
        self._clear_layout(self._actions_host_layout)
        self._action_buttons.clear()
        icon = SYMBOL_ICON.get(self._preset_id, "")
        actions = self._visible_actions(icon)
        has_position = self._active_mode in (
            HedgeMode.CONTRACTION.value,
            HedgeMode.EXPANSION.value,
        )
        self._actions_host_layout.addLayout(self._build_actions_layout(actions, has_position))

    def set_active_mode(self, active_mode: str | None) -> None:
        if active_mode == self._active_mode:
            return
        self._active_mode = active_mode
        self._rebuild_actions()
        if self.isVisible():
            self._fit_size()

    def set_actions_enabled(self, enabled: bool) -> None:
        for btn in self._action_buttons:
            btn.setEnabled(enabled)

    def _build_actions_layout(
        self,
        actions: list[tuple[str, str, str, str]],
        vertical: bool,
    ) -> QVBoxLayout | QHBoxLayout:
        if vertical:
            layout: QVBoxLayout | QHBoxLayout = QVBoxLayout()
            layout.setSpacing(10)
            for idx, (text, style, action, mode) in enumerate(actions):
                layout.addWidget(self._make_action_button(text, style, action, mode))
                if idx == 0 and len(actions) > 1:
                    layout.addSpacing(12)
            return layout

        row = QHBoxLayout()
        row.setSpacing(12)
        for text, style, action, mode in actions:
            row.addWidget(self._make_action_button(text, style, action, mode))
        wrapper = QVBoxLayout()
        wrapper.addLayout(row)
        return wrapper

    def _make_action_button(
        self, text: str, style: str, action: str, mode: str
    ) -> QPushButton:
        btn = QPushButton(text)
        btn.setObjectName(style)
        btn.setMinimumHeight(48)
        btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        btn.clicked.connect(lambda _=False, a=action, m=mode: self._execute_trade(a, m))
        self._action_buttons.append(btn)
        return btn

    def _visible_actions(self, icon: str) -> list[tuple[str, str, str, str]]:
        all_actions = (
            (f"{icon} 开仓收缩", "tradeShortOpen", "开仓", HedgeMode.CONTRACTION.value),
            (f"{icon} 平仓收缩", "tradeShortClose", "平仓", HedgeMode.CONTRACTION.value),
            (f"{icon} 开仓扩张", "tradeLongOpen", "开仓", HedgeMode.EXPANSION.value),
            (f"{icon} 平仓扩张", "tradeLongClose", "平仓", HedgeMode.EXPANSION.value),
        )
        if self._active_mode is None:
            return [all_actions[0], all_actions[2]]
        if self._active_mode == HedgeMode.CONTRACTION.value:
            return [all_actions[0], all_actions[1]]
        if self._active_mode == HedgeMode.EXPANSION.value:
            return [all_actions[2], all_actions[3]]
        return [all_actions[0], all_actions[2]]

    def _execute_trade(self, action: str, mode: str) -> None:
        self._action = action
        self._mode = mode
        self._ratio_fields.apply_to(self._config)
        self.set_actions_enabled(False)
        self.trade_requested.emit(action, mode)

    def _apply_action(self, action: str, mode: str) -> None:
        """兼容测试：等同于点击交易按钮。"""
        self._execute_trade(action, mode)

    def selected_trade(self) -> tuple[str, str] | None:
        if self._action is None or self._mode is None:
            return None
        return self._action, self._mode

    def gold_order_mode(self) -> str:
        if self._order_mode_market is not None and self._order_mode_market.isChecked():
            return GoldOrderMode.MARKET.value
        return GoldOrderMode.MAKER.value

    def set_order_mode(self, order_mode: str) -> None:
        if self._order_mode_market is None or self._order_mode_maker is None:
            return
        if order_mode == GoldOrderMode.MARKET.value:
            self._order_mode_market.setChecked(True)
        else:
            self._order_mode_maker.setChecked(True)

    def apply_ratio_to(self, config: AppConfig) -> None:
        self._ratio_fields.apply_to(config)
