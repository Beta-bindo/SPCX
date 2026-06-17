"""单品种交易面板组件。

包含三块：
- SymbolActionStrip：点差大字、持仓/盈亏状态、对冲入口与告警/自动交易设置（中栏）。
- SymbolTradePanel：盘口深度表（买/卖各若干档 + BA 买价）。
- PanelSectionDialog：自定义中栏各区块的显示与顺序。
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal, QSize
from PySide6.QtGui import QColor, QFont, QMouseEvent
from PySide6.QtWidgets import (
    QAbstractScrollArea,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.core.models import (
    AppConfig,
    OpenOrder,
    OrderBook,
    Position,
    Quote,
    Side,
    SpreadSnapshot,
    DEFAULT_PANEL_SECTIONS,
    PANEL_SECTION_LABELS,
    parse_panel_sections,
    serialize_panel_sections,
)
from app.core.pnl_calculator import PnlSummary, build_spread_snapshot, calculate_pnl
from app.core.symbols import find_preset
from app.core.theme import set_flag, ui_font, ui_mono_font
from app.core.hedge_health import (
    analyze_hedge_health,
    format_position_status,
    suggest_hedge_repair,
)
from app.core.trading_service import (
    detect_hedge_mode,
    hedge_mode_strategy_label,
    position_entry_spread,
)
from app.widgets.spread_value_label import SpreadValueLabel
from app.widgets.symbol_alert_settings import ClickToEditDoubleSpinBox, ClickToEditSpinBox
from app.widgets.symbol_alert_settings import SymbolAlertSettings
from app.widgets.symbol_auto_trade_settings import SymbolAutoTradeSettings
from app.widgets.pnl_detail_panel import PnlDetailPanel
from app.widgets.panel_ui_scale import (
    DEFAULT_PANEL_CHECK_PX,
    DEFAULT_PANEL_FONT_PT,
    MAX_PANEL_CHECK_PX,
    MAX_PANEL_FONT_PT,
    MIN_PANEL_CHECK_PX,
    MIN_PANEL_FONT_PT,
    build_panel_section_qss,
    clamp_check_px,
    clamp_font_pt,
)

BOOK_ROWS_COMPACT = 10
BOOK_ROWS_FULL = 20
BOOK_PANEL_WIDTH = 200
ROW_HEIGHT = 22
SYMBOL_ICON = {"xau": "🥇", "xag": "🥈"}


class BookMidLabel(QLabel):
    """订单簿买卖盘之间的 BA 买价，随可用宽度缩放字号。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("baMidTag")
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)

    def _sync_font(self) -> None:
        if not self.text():
            self.setMinimumHeight(0)
            return
        w = max(self.width(), 72)
        px = max(16, min(26, int(w * 0.13)))
        font = ui_font(pixel_size=px, weight=QFont.Weight.Bold)
        self.setFont(font)
        self.setMinimumHeight(px + 8)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._sync_font()

    def setText(self, text: str) -> None:
        if text == self.text():
            return
        super().setText(text)
        self._sync_font()


class SymbolActionStrip(QFrame):
    """点差、持仓与对冲入口（置于中间栏盈利告警上方）."""

    position_refresh_requested = Signal()          # 请求刷新持仓
    hedge_repair_requested = Signal(str, object)   # (品种, HedgeRepair) 请求补对冲
    section_layout_changed = Signal()              # 中栏区块布局变更

    def __init__(self, preset_id: str, parent=None):
        # 构建中栏：点差大字、两端买价、持仓状态、对冲入口按钮，
        # 以及可配置显隐/顺序的告警与自动交易设置区块。
        super().__init__(parent)
        self.preset_id = preset_id
        self._icon = SYMBOL_ICON.get(preset_id, "")
        self._sections = parse_panel_sections(DEFAULT_PANEL_SECTIONS)
        self.setObjectName("symbolActionStrip")
        self._last_spread: SpreadSnapshot | None = None
        self._last_ba_text = ""
        self._last_ex_text = ""
        self._last_position_text = ""
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(12)
        root.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)

        self._left_col = QWidget(self)
        self._left_col.setObjectName("symbolActionLeft")
        self._left_col.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred
        )
        self._left_col.setMinimumWidth(268)
        self._stack_layout = QVBoxLayout(self._left_col)
        self._stack_layout.setContentsMargins(0, 0, 0, 0)
        self._stack_layout.setSpacing(6)
        self._stack_layout.setAlignment(
            Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft
        )

        root.addWidget(self._left_col, 0)
        root.addStretch(1)

        label = "黄金" if preset_id == "xau" else "白银"
        self._title_row = QWidget(self)
        title_layout = QHBoxLayout(self._title_row)
        title_layout.setContentsMargins(0, 0, 0, 0)
        title_layout.setSpacing(6)
        title = QLabel(f"{self._icon} {label}")
        title.setObjectName("fieldLabel")
        title_layout.addWidget(title)
        title_layout.addStretch(1)
        self.sections_btn = QPushButton("板块")
        self.sections_btn.setObjectName("ghostButton")
        self.sections_btn.setProperty("compact", True)
        self.sections_btn.setFixedHeight(24)
        self.sections_btn.setToolTip("设置中间栏板块的显示与上下顺序")
        self.sections_btn.setSizePolicy(
            QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed
        )
        self.sections_btn.clicked.connect(self._open_section_dialog)
        title_layout.addWidget(self.sections_btn)
        self._stack_layout.addWidget(self._title_row)

        self.spread_frame = QFrame(self)
        self.spread_frame.setObjectName("spreadStrip")
        self.spread_frame.setFixedHeight(80)
        spread_layout = QGridLayout(self.spread_frame)
        spread_layout.setContentsMargins(6, 4, 6, 4)
        spread_layout.setHorizontalSpacing(8)
        spread_layout.setVerticalSpacing(2)
        spread_title = QLabel("跨平台点差 · BA − MT5")
        spread_title.setObjectName("fieldLabel")
        spread_layout.addWidget(spread_title, 0, 0, 1, 2)
        self.spread_value = SpreadValueLabel()
        self.spread_ba = QLabel("--")
        self.spread_ba.setObjectName("baPriceTag")
        self.spread_ba.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.spread_ba.setFixedWidth(72)
        self.spread_ex = QLabel("--")
        self.spread_ex.setObjectName("mt5PriceTag")
        self.spread_ex.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.spread_ex.setFixedWidth(72)
        for lbl in (self.spread_ba, self.spread_ex):
            lbl.setFont(ui_mono_font(point_size=10, weight=QFont.Weight.Bold))
        ba_tag = QLabel("BA")
        ba_tag.setObjectName("fieldLabel")
        ba_tag.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        mt5_tag = QLabel("MT5")
        mt5_tag.setObjectName("mt5PlatformTag")
        mt5_tag.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        price_col = QVBoxLayout()
        price_col.setContentsMargins(0, 0, 0, 0)
        price_col.setSpacing(2)
        ba_row = QHBoxLayout()
        ba_row.setContentsMargins(0, 0, 0, 0)
        ba_row.setSpacing(4)
        ba_row.addWidget(ba_tag)
        ba_row.addWidget(self.spread_ba)
        ba_row.addStretch()
        mt5_row = QHBoxLayout()
        mt5_row.setContentsMargins(0, 0, 0, 0)
        mt5_row.setSpacing(4)
        mt5_row.addWidget(mt5_tag)
        mt5_row.addWidget(self.spread_ex)
        mt5_row.addStretch()
        price_col.addLayout(ba_row)
        price_col.addLayout(mt5_row)
        spread_layout.addWidget(
            self.spread_value,
            1,
            0,
            alignment=Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
        )
        spread_layout.addLayout(price_col, 1, 1)
        spread_w = self.spread_value.reserve_width()
        spread_layout.setColumnMinimumWidth(0, spread_w)
        spread_layout.setColumnMinimumWidth(1, 118)
        spread_layout.setColumnStretch(0, 0)
        spread_layout.setColumnStretch(1, 0)
        self.spread_frame.setMinimumWidth(spread_w + 118 + 8 + 12)

        self.alert_settings = SymbolAlertSettings(preset_id, parent=self)

        position_header = QHBoxLayout()
        position_header.setContentsMargins(0, 0, 0, 0)
        position_header.setSpacing(6)
        position_wrap = QWidget(self)
        position_wrap.setFixedHeight(28)
        position_wrap.setLayout(position_header)
        self.position_status = QLabel("当前持仓：无")
        self.position_status.setObjectName("positionStatus")
        self.position_status.setWordWrap(False)
        self.position_status.setMinimumHeight(28)
        self.position_status.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        position_header.addWidget(self.position_status)
        self.position_repair_btn = QPushButton("补对冲")
        self.position_repair_btn.setObjectName("hedgeRepairButton")
        self.position_repair_btn.setProperty("compact", True)
        self.position_repair_btn.setFixedHeight(28)
        self.position_repair_btn.setVisible(False)
        position_header.addWidget(self.position_repair_btn)
        self.refresh_positions_btn = QPushButton("刷新持仓")
        self.refresh_positions_btn.setObjectName("ghostButton")
        self.refresh_positions_btn.setProperty("compact", True)
        self.refresh_positions_btn.setFixedHeight(28)
        self.refresh_positions_btn.setSizePolicy(
            QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed
        )
        position_header.addWidget(self.refresh_positions_btn)
        self._position_header = position_wrap

        self.pnl_detail = PnlDetailPanel(preset_id, parent=self)
        self.pnl_detail.hedge_repair_requested.connect(
            lambda repair, pid=preset_id: self.hedge_repair_requested.emit(pid, repair)
        )
        self.position_repair_btn.clicked.connect(self.pnl_detail.request_hedge_repair)
        # 委托/爆仓缓冲摘要行已移除，明细表内仍展示爆仓列
        self.pending_label = QLabel()
        self.pending_label.hide()
        self.risk_label = QLabel()
        self.risk_label.hide()
        self._risk_row = QWidget(self)
        self._risk_row.hide()
        self._last_risk_text = ""
        self._last_pending_text = ""

        # 「当前持仓 / 盈利」板块（可配置）
        self._position_block = QWidget(self)
        position_block_layout = QVBoxLayout(self._position_block)
        position_block_layout.setContentsMargins(0, 0, 0, 0)
        position_block_layout.setSpacing(6)
        position_block_layout.addWidget(self._position_header)
        position_block_layout.addWidget(self.pnl_detail)

        # 「自动交易」板块（可配置）
        self.auto_trade_settings = SymbolAutoTradeSettings(preset_id, parent=self)
        self._auto_block = QWidget(self)
        auto_block_layout = QVBoxLayout(self._auto_block)
        auto_block_layout.setContentsMargins(0, 0, 0, 0)
        auto_block_layout.setSpacing(6)
        auto_block_layout.addWidget(self.auto_trade_settings)

        # 对冲交易 / 启动停止按钮（固定常驻，不参与板块配置）
        self._monitor_host = QWidget(self)
        self._monitor_host.setObjectName("monitorButtonHost")
        self._monitor_host.setMinimumHeight(36)
        monitor_layout = QHBoxLayout(self._monitor_host)
        monitor_layout.setContentsMargins(0, 2, 0, 4)
        monitor_layout.setSpacing(6)
        monitor_layout.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )
        self._monitor_layout = monitor_layout
        self.trade_entry_btn = QPushButton(f"{self._icon} 对冲交易")
        self.trade_entry_btn.setObjectName("primaryButton")
        self.trade_entry_btn.setProperty("compact", True)
        self.trade_entry_btn.setFixedHeight(28)
        self.trade_entry_btn.setSizePolicy(
            QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed
        )
        monitor_layout.addWidget(self.trade_entry_btn)

        self._section_widgets = {
            "spread": self.spread_frame,
            "alert": self.alert_settings,
            "auto": self._auto_block,
            "position": self._position_block,
        }
        # 自动下单是否由运营后台开通：未开通则整个「自动交易」板块隐藏
        self._auto_trade_available = True

        self._rebuild_stack()

        self.refresh_positions_btn.clicked.connect(self.position_refresh_requested.emit)
        self.apply_panel_ui_scale()

    def attach_monitor_buttons(self, start_btn, stop_btn) -> None:
        """把外部的启用/停止监控按钮放到对冲入口按钮左侧。"""
        for btn in (start_btn, stop_btn):
            btn.setFixedHeight(28)
            btn.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
            if btn.parent() is not self._monitor_host:
                btn.setParent(self._monitor_host)
            if self._monitor_layout.indexOf(btn) >= 0:
                self._monitor_layout.removeWidget(btn)
            trade_idx = self._monitor_layout.indexOf(self.trade_entry_btn)
            if trade_idx >= 0:
                self._monitor_layout.insertWidget(trade_idx, btn)
            else:
                self._monitor_layout.addWidget(btn)

    def detach_monitor_buttons(self) -> None:
        """移除之前挂入的监控按钮（保留对冲入口按钮）。"""
        to_remove: list[QWidget] = []
        for i in range(self._monitor_layout.count()):
            item = self._monitor_layout.itemAt(i)
            if item is None:
                continue
            widget = item.widget()
            if widget is not None and widget is not self.trade_entry_btn:
                to_remove.append(widget)
        for widget in to_remove:
            self._monitor_layout.removeWidget(widget)
            widget.setParent(None)

    def minimumSizeHint(self) -> QSize:
        """按内容高度计算，避免横向 splitter 撑满后锁死纵向分割条。"""
        margins = self._stack_layout.contentsMargins()
        spacing = self._stack_layout.spacing()
        height = margins.top() + margins.bottom()
        width = max(self._left_col.minimumWidth(), 0) + margins.left() + margins.right()
        first = True
        for i in range(self._stack_layout.count()):
            item = self._stack_layout.itemAt(i)
            if item is None:
                continue
            block_h = 0
            block_w = 0
            if item.widget() is not None:
                hint = item.widget().minimumSizeHint()
                block_h = hint.height()
                block_w = hint.width()
            elif item.layout() is not None:
                block_h = item.layout().minimumSize().height()
                block_w = item.layout().minimumSize().width()
            if block_h <= 0 and block_w <= 0:
                continue
            if not first:
                height += spacing
            height += block_h
            width = max(width, block_w + margins.left() + margins.right())
            first = False
        return QSize(width, max(height, 120))

    def _clear_stack_after_title(self) -> None:
        while self._stack_layout.count() > 1:
            item = self._stack_layout.takeAt(1)
            if item is None:
                break

    def _rebuild_stack(self) -> None:
        """重建中栏纵向堆叠：固定对冲按钮 + 按用户配置顺序与显隐的可选区块。"""
        self._clear_stack_after_title()
        self._stack_layout.addWidget(self._monitor_host)
        for key, visible, _font_pt, _check_px in self._sections:
            widget = self._section_widgets.get(key)
            if widget is None:
                continue
            # 自动下单未经运营后台开通时，整块隐藏且不参与堆叠
            if key == "auto" and not self._auto_trade_available:
                widget.setVisible(False)
                continue
            widget.setVisible(visible)
            self._stack_layout.addWidget(widget)

    def _section_ui_scale(self, key: str) -> tuple[int, int]:
        for section_key, _visible, font_pt, check_px in self._sections:
            if section_key == key:
                return font_pt, check_px
        return DEFAULT_PANEL_FONT_PT, DEFAULT_PANEL_CHECK_PX

    def apply_panel_ui_scale(self) -> None:
        """把各板块的字体/勾选框尺寸分别应用到对应控件。"""
        spread_font, spread_check = self._section_ui_scale("spread")
        alert_font, alert_check = self._section_ui_scale("alert")
        auto_font, auto_check = self._section_ui_scale("auto")
        pos_font, pos_check = self._section_ui_scale("position")

        self.spread_frame.setStyleSheet(
            build_panel_section_qss(spread_font, spread_check)
        )
        self.alert_settings.apply_ui_scale(alert_font, alert_check)
        self.auto_trade_settings.apply_ui_scale(auto_font, auto_check)
        self._position_block.setStyleSheet(build_panel_section_qss(pos_font, pos_check))
        self.pnl_detail.apply_ui_scale(pos_font)
        tag_font = ui_mono_font(point_size=spread_font, weight=QFont.Weight.Bold)
        for lbl in self.spread_frame.findChildren(QLabel):
            name = lbl.objectName()
            if name in ("fieldLabel", "mt5PlatformTag"):
                lbl.setFont(tag_font)

    def set_section_layout(
        self, sections: list[tuple[str, bool] | tuple[str, bool, int, int]]
    ) -> None:
        """设置中栏区块的顺序、显隐与各板块 UI 缩放并重建。"""
        self._sections = parse_panel_sections(serialize_panel_sections(sections))
        self._rebuild_stack()

    def set_auto_trade_available(self, available: bool) -> int:
        """设置自动下单板块是否可用（运营后台开通则显示）。

        关闭时同时取消所有已勾选的自动开/平仓，返回被取消的数量，避免隐藏后仍在后台触发。
        """
        available = bool(available)
        cancelled = 0
        if not available:
            cancelled = self.auto_trade_settings.disable_checked_auto_trades()
        if available == self._auto_trade_available:
            return cancelled
        self._auto_trade_available = available
        self._rebuild_stack()
        return cancelled

    @property
    def auto_trade_available(self) -> bool:
        return self._auto_trade_available

    def current_section_layout(self) -> list[tuple[str, bool, int, int]]:
        """返回当前区块布局（含各板块字体与勾选框尺寸）。"""
        return list(self._sections)

    def _open_section_dialog(self) -> None:
        """打开区块自定义对话框，确认后应用新布局。"""
        dialog = PanelSectionDialog(self._sections, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.set_section_layout(dialog.result_sections())
            self.apply_panel_ui_scale()
            self.section_layout_changed.emit()

    def update_pnl(
        self,
        positions: list[Position],
        ba_quotes: dict[str, Quote],
        mt5_quotes: dict[str, Quote],
        config: AppConfig,
    ) -> None:
        """用最新行情重算本品种盈亏并刷新明细与持仓状态。"""
        preset = find_preset(self.preset_id)
        ba_q = ba_quotes.get(preset.symbol_ba)
        mt5_q = mt5_quotes.get(preset.symbol_mt5)
        snap = (
            build_spread_snapshot(ba_q, mt5_q, self.preset_id)
            if ba_q is not None and mt5_q is not None
            else None
        )
        preset_positions = [
            p for p in positions
            if (p.platform == "BA" and p.symbol == preset.symbol_ba)
            or (p.platform == "MT5" and p.symbol == preset.symbol_mt5)
        ]
        updated, summary = calculate_pnl(preset_positions, ba_quotes, mt5_quotes, config, snap)
        self.pnl_detail.update(updated, ba_quotes, mt5_quotes, config, summary)
        ba = next(
            (p for p in updated if p.platform == "BA" and p.symbol == preset.symbol_ba),
            None,
        )
        mt5 = next(
            (p for p in updated if p.platform == "MT5" and p.symbol == preset.symbol_mt5),
            None,
        )
        self._update_position_status(ba, mt5, updated, config)

    def update_risk(self, ba_liq: float, mt5_liq: float) -> None:
        """爆仓缓冲已改在盈亏明细表内展示，此处保留接口兼容。"""
        return

    def update_open_orders(self, orders: list[OpenOrder]) -> None:
        """委托摘要行已移除，此处保留接口兼容。"""
        return

    def load_settings_from(self, config: AppConfig) -> None:
        """加载告警/自动交易/区块布局设置（加载期间屏蔽信号防误触发自动保存）。"""
        blocked: list = []
        blocked.extend(self.alert_settings.iter_watch_widgets())
        blocked.extend(self.auto_trade_settings.iter_watch_widgets())
        for widget in blocked:
            widget.blockSignals(True)
        try:
            self.alert_settings.load_config(config)
            self.auto_trade_settings.load_config(config)
        finally:
            for widget in blocked:
                widget.blockSignals(False)
        self.set_section_layout(config.panel_sections(self.preset_id))
        self.apply_panel_ui_scale()

    def apply_settings_to(self, config: AppConfig) -> None:
        """把告警/自动交易/区块布局设置写回配置。"""
        self.alert_settings.apply_to(config)
        self.auto_trade_settings.apply_to(config)
        config.set_panel_sections(self.preset_id, self._sections)

    def refresh_theme(self) -> None:
        """主题切换后刷新点差大字着色。"""
        self.spread_value.refresh_theme()
        if self._last_spread is not None:
            self.spread_value.set_spread(self._last_spread.mid_spread)

    def update_spread(self, snap: SpreadSnapshot | None) -> None:
        """刷新点差大字与两端买价（Bid）；None 显示"--"。"""
        if snap is None:
            self.spread_value.set_spread(None)
            if self._last_ba_text != "--":
                self.spread_ba.setText("--")
                self._last_ba_text = "--"
            if self._last_ex_text != "--":
                self.spread_ex.setText("--")
                self._last_ex_text = "--"
            self._last_spread = None
            return
        self._last_spread = snap
        self.spread_value.set_spread(snap.mid_spread)
        # 点差大字与两端价格均用买价（Bid），与 BA/Exness 终端默认显示口径一致。
        ba_text = f"{snap.ba_bid:.3f}"
        ex_text = f"{snap.mt5_bid:.3f}"
        if ba_text != self._last_ba_text:
            self.spread_ba.setText(ba_text)
            self._last_ba_text = ba_text
        if ex_text != self._last_ex_text:
            self.spread_ex.setText(ex_text)
            self._last_ex_text = ex_text

    def update_positions(
        self, positions: list[Position], _summary: PnlSummary, config: AppConfig | None = None
    ) -> None:
        """根据最新持仓刷新本品种的持仓状态行。"""
        preset = find_preset(self.preset_id)
        ba = next(
            (p for p in positions if p.platform == "BA" and p.symbol == preset.symbol_ba),
            None,
        )
        mt5 = next(
            (p for p in positions if p.platform == "MT5" and p.symbol == preset.symbol_mt5),
            None,
        )
        self._update_position_status(ba, mt5, positions, config)

    def _update_position_status(
        self,
        ba: Position | None,
        mt5: Position | None,
        positions: list[Position],
        config: AppConfig | None = None,
    ) -> None:
        """根据对冲健康状态刷新持仓状态文案、告警配色、补对冲按钮与明细面板。"""
        health = analyze_hedge_health(self.preset_id, positions, config)
        if health.is_ok and health.code == "hedged":
            mode = detect_hedge_mode(self.preset_id, positions)
            strategy = hedge_mode_strategy_label(mode)
            # 入场点差均值（BA 均价 − Ex 均价），随加仓加权平均、不随行情浮动
            entry = position_entry_spread(ba, mt5)
            if entry is not None:
                text = f"当前持仓：{strategy}（{entry:+.3f}）"
            else:
                text = f"当前持仓：{strategy}"
        else:
            text = format_position_status(health)
        if text != self._last_position_text:
            self.position_status.setText(text)
            self._last_position_text = text
        active = health.code == "hedged"
        alert_level = health.level if health.level in ("warn", "alert") else ""
        if self.position_status.property("active") != ("true" if active else "false"):
            set_flag(self.position_status, "active", active)
        if self.position_status.property("hedgeAlert") != alert_level:
            set_flag(self.position_status, "hedgeAlert", alert_level or False)
        wrap_alert = health.level in ("warn", "alert")
        if self.position_status.wordWrap() != wrap_alert:
            self.position_status.setWordWrap(wrap_alert)
        alert_height = 52 if wrap_alert else 28
        if self._position_header.height() != alert_height:
            self._position_header.setFixedHeight(alert_height)
            self.position_status.setMinimumHeight(alert_height)
        repair = suggest_hedge_repair(self.preset_id, positions, health)
        show_repair = repair is not None
        if self.position_repair_btn.isVisible() != show_repair:
            self.position_repair_btn.setVisible(show_repair)
        if repair is not None and repair.tooltip:
            self.position_repair_btn.setToolTip(repair.tooltip)
        else:
            self.position_repair_btn.setToolTip("")
        self.pnl_detail.update_hedge_health(health, repair)

    def set_trade_buttons_enabled(self, enabled: bool) -> None:
        """统一启用/禁用对冲入口与补对冲按钮（下单进行中防重复点击）。"""
        self.trade_entry_btn.setEnabled(enabled)
        self.position_repair_btn.setEnabled(enabled)
        self.pnl_detail.hedge_repair_btn.setEnabled(enabled)

    def lock_setting_spins(self) -> None:
        """收起告警/自动交易设置中的所有数字框编辑态。"""
        self.alert_settings.lock_all_spins()
        self.auto_trade_settings.lock_all_spins()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        target = self.childAt(event.pos())
        while target is not None and target is not self:
            if isinstance(target, (ClickToEditDoubleSpinBox, ClickToEditSpinBox)):
                break
            target = target.parentWidget()
        else:
            self.lock_setting_spins()
        super().mousePressEvent(event)


class PanelSectionDialog(QDialog):
    """配置中间栏各板块的显示、顺序、字体与勾选框尺寸。"""

    def __init__(
        self,
        sections: list[tuple[str, bool, int, int]],
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle("板块设置")
        self.setModal(True)
        self._sections: list[list] = [
            [key, bool(visible), clamp_font_pt(font_pt), clamp_check_px(check_px)]
            for key, visible, font_pt, check_px in sections
        ]
        self._checks: dict[str, QCheckBox] = {}
        self._font_spins: dict[str, QSpinBox] = {}
        self._check_spins: dict[str, QSpinBox] = {}

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(8)

        hint = QLabel(
            "勾选显示对应板块；每行可单独设置字体与勾选框大小；用 ↑ ↓ 调整上下顺序。"
        )
        hint.setObjectName("fieldHint")
        hint.setWordWrap(True)
        root.addWidget(hint)

        self._rows_host = QWidget()
        self._rows_layout = QVBoxLayout(self._rows_host)
        self._rows_layout.setContentsMargins(0, 0, 0, 0)
        self._rows_layout.setSpacing(6)
        root.addWidget(self._rows_host)
        self._render_rows()

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        ok_btn = buttons.button(QDialogButtonBox.StandardButton.Ok)
        if ok_btn is not None:
            ok_btn.setText("确定")
        cancel_btn = buttons.button(QDialogButtonBox.StandardButton.Cancel)
        if cancel_btn is not None:
            cancel_btn.setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def _sync_row_check_size(self, key: str) -> None:
        """同步单行勾选框尺寸，便于预览点击区域。"""
        check = self._checks.get(key)
        spin = self._check_spins.get(key)
        if check is None or spin is None:
            return
        px = spin.value()
        touch = max(22, px + 6)
        check.setStyleSheet(
            f"QCheckBox::indicator {{ width: {px}px; height: {px}px; }}"
            f"QCheckBox {{ min-height: {touch}px; }}"
        )

    def _render_rows(self) -> None:
        """按当前顺序重绘每个板块行。"""
        while self._rows_layout.count():
            item = self._rows_layout.takeAt(0)
            if item is None:
                break
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._checks.clear()
        self._font_spins.clear()
        self._check_spins.clear()
        for idx, entry in enumerate(self._sections):
            key, visible, font_pt, check_px = (
                entry[0],
                entry[1],
                entry[2],
                entry[3],
            )
            row = QWidget()
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(6)
            check = QCheckBox(PANEL_SECTION_LABELS.get(key, key))
            check.setChecked(bool(visible))
            check.toggled.connect(lambda state, k=key: self._set_visible(k, state))
            self._checks[key] = check
            row_layout.addWidget(check)
            row_layout.addSpacing(4)
            row_layout.addWidget(QLabel("字体"))
            font_spin = QSpinBox()
            font_spin.setRange(MIN_PANEL_FONT_PT, MAX_PANEL_FONT_PT)
            font_spin.setValue(font_pt)
            font_spin.setSuffix(" pt")
            font_spin.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)
            font_spin.setFixedWidth(68)
            self._font_spins[key] = font_spin
            row_layout.addWidget(font_spin)
            row_layout.addSpacing(4)
            row_layout.addWidget(QLabel("勾选"))
            check_spin = QSpinBox()
            check_spin.setRange(MIN_PANEL_CHECK_PX, MAX_PANEL_CHECK_PX)
            check_spin.setValue(check_px)
            check_spin.setSuffix(" px")
            check_spin.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)
            check_spin.setFixedWidth(68)
            check_spin.valueChanged.connect(
                lambda _value, k=key: self._sync_row_check_size(k)
            )
            self._check_spins[key] = check_spin
            row_layout.addWidget(check_spin)
            row_layout.addStretch(1)
            up_btn = QPushButton("↑")
            up_btn.setFixedWidth(30)
            up_btn.setEnabled(idx > 0)
            up_btn.clicked.connect(lambda _=False, i=idx: self._move(i, -1))
            down_btn = QPushButton("↓")
            down_btn.setFixedWidth(30)
            down_btn.setEnabled(idx < len(self._sections) - 1)
            down_btn.clicked.connect(lambda _=False, i=idx: self._move(i, 1))
            row_layout.addWidget(up_btn)
            row_layout.addWidget(down_btn)
            self._rows_layout.addWidget(row)
            self._sync_row_check_size(key)

    def _set_visible(self, key: str, visible: bool) -> None:
        for entry in self._sections:
            if entry[0] == key:
                entry[1] = bool(visible)
                break

    def _move(self, index: int, delta: int) -> None:
        """上移/下移一个板块并重绘。"""
        target = index + delta
        if target < 0 or target >= len(self._sections):
            return
        self._sections[index], self._sections[target] = (
            self._sections[target],
            self._sections[index],
        )
        self._render_rows()

    def result_sections(self) -> list[tuple[str, bool, int, int]]:
        """返回用户编辑后的板块顺序、显隐与各板块 UI 缩放。"""
        result: list[tuple[str, bool, int, int]] = []
        for key, _visible, _font_pt, _check_px in self._sections:
            check = self._checks.get(key)
            font_spin = self._font_spins.get(key)
            check_spin = self._check_spins.get(key)
            visible = check.isChecked() if check is not None else True
            font_pt = font_spin.value() if font_spin is not None else DEFAULT_PANEL_FONT_PT
            check_px = check_spin.value() if check_spin is not None else DEFAULT_PANEL_CHECK_PX
            result.append(
                (
                    key,
                    visible,
                    clamp_font_pt(font_pt),
                    clamp_check_px(check_px),
                )
            )
        return result


class SymbolTradePanel(QFrame):
    """单品种盘口面板：买盘表 + BA 买价 + 卖盘表，支持紧凑/完整两种密度。"""

    def __init__(self, preset_id: str, title: str, parent=None):
        super().__init__(parent)
        self.preset_id = preset_id
        self._compact = False
        self._book_rows = BOOK_ROWS_FULL
        self.setObjectName("card")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        self._root = QVBoxLayout(self)
        self._root.setContentsMargins(10, 8, 10, 8)
        self._root.setSpacing(6)

        book_col = QVBoxLayout()
        book_col.setSpacing(0)
        book_col.addWidget(self._build_book_side(("买价", "买量"), "bid"))
        self.ba_mid_label = BookMidLabel()
        book_col.addWidget(self.ba_mid_label)
        book_col.addWidget(self._build_book_side(("卖价", "卖量"), "ask"))
        self._root.setAlignment(
            Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft
        )
        self._root.addLayout(book_col)
        self._root.addStretch(1)
        self._apply_book_metrics()

    def set_compact(self, compact: bool) -> None:
        """切换紧凑/完整模式：紧凑显示 10 档自适应宽度，完整显示 20 档固定宽度。"""
        if self._compact == compact:
            return
        self._compact = compact
        self._book_rows = BOOK_ROWS_COMPACT if compact else BOOK_ROWS_FULL
        self.setProperty("bookCompact", compact)
        self.style().unpolish(self)
        self.style().polish(self)
        margins = (4, 4, 4, 4) if compact else (10, 8, 10, 8)
        self._root.setContentsMargins(*margins)
        self._root.setSpacing(4 if compact else 6)
        if compact:
            self.setSizePolicy(
                QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
            )
            self.setMinimumWidth(0)
            self.setMaximumWidth(16777215)
            self.setMinimumHeight(0)
            self.setMaximumHeight(16777215)
        else:
            self.setSizePolicy(
                QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding
            )
            self.setMinimumWidth(BOOK_PANEL_WIDTH)
            self.setMaximumWidth(BOOK_PANEL_WIDTH)
            self.setMinimumHeight(self._book_panel_min_height())
        self._apply_book_metrics()
        self._sync_table_heights()

    def _book_panel_min_height(self) -> int:
        """计算完整模式下盘口面板的最小高度（两表 + 买价 + 边距）。"""
        rows = max(self.bid_table.rowCount(), 1)
        row_h = self.bid_table.verticalHeader().defaultSectionSize()
        hdr_h = self.bid_table.horizontalHeader().height()
        table_h = rows * row_h + hdr_h + self.bid_table.frameWidth() * 2
        mid_h = max(self.ba_mid_label.sizeHint().height(), 0)
        margins = self._root.contentsMargins()
        spacing = self._root.spacing()
        return (
            margins.top()
            + margins.bottom()
            + table_h * 2
            + mid_h
            + spacing * 2
        )

    def _sync_table_heights(self) -> None:
        """按行数把买/卖盘表设为固定高度，避免内部滚动条。"""
        rows = max(self.bid_table.rowCount(), 1)
        row_h = self.bid_table.verticalHeader().defaultSectionSize()
        hdr_h = self.bid_table.horizontalHeader().height()
        table_h = rows * row_h + hdr_h + self.bid_table.frameWidth() * 2
        for table in (self.bid_table, self.ask_table):
            table.setFixedHeight(table_h)
            table.setMinimumWidth(0)
            table.setMaximumWidth(16777215)
            self._apply_book_scroll_policy(table)
            table.setSizePolicy(
                QSizePolicy.Policy.Expanding,
                QSizePolicy.Policy.Fixed,
            )
            side = table.parentWidget()
            if side is not None:
                side.setMinimumWidth(0)
                side.setMaximumWidth(16777215)
                side.setSizePolicy(
                    QSizePolicy.Policy.Expanding,
                    QSizePolicy.Policy.Fixed,
                )
        if not self._compact:
            self.setMinimumHeight(self._book_panel_min_height())

    def _apply_book_scroll_policy(self, table: QTableWidget) -> None:
        table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

    def _apply_book_metrics(self) -> None:
        """根据紧凑/完整模式设置行高、字号与表头高度。"""
        row_h = 16 if self._compact else ROW_HEIGHT
        font_pt = 7 if self._compact else 8
        font = ui_font(point_size=font_pt)
        for table in (self.bid_table, self.ask_table):
            table.setFont(font)
            table.verticalHeader().setDefaultSectionSize(row_h)
            hdr = table.horizontalHeader()
            hdr.setVisible(True)
            hdr.setFixedHeight(18 if self._compact else 24)
            hdr.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
            hdr.setStretchLastSection(True)
        self._sync_table_heights()
        self.ba_mid_label.setMinimumWidth(0)
        self.ba_mid_label.setMaximumWidth(16777215)
        self.ba_mid_label._sync_font()

    def _build_book_side(self, headers: tuple[str, str], key: str) -> QFrame:
        """构建买盘或卖盘一侧的表格容器（key 为 bid/ask）。"""
        frame = QFrame()
        frame.setObjectName("bookSide")
        col = QVBoxLayout(frame)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(0)
        table = self._make_table(headers)
        if key == "bid":
            self.bid_table = table
        else:
            self.ask_table = table
        col.addWidget(table)
        return frame

    @property
    def table(self) -> QTableWidget:
        return self.bid_table

    def _make_table(self, headers: tuple[str, str]) -> QTableWidget:
        """创建一个两列（价/量）的盘口表格。"""
        table = QTableWidget(0, 2)
        table.setObjectName("orderBookTable")
        table.setHorizontalHeaderLabels(list(headers))
        table.verticalHeader().setVisible(False)
        table.setShowGrid(True)
        table.setAlternatingRowColors(True)
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        table.setSizeAdjustPolicy(QAbstractScrollArea.SizeAdjustPolicy.AdjustIgnored)
        self._apply_book_scroll_policy(table)
        hdr = table.horizontalHeader()
        hdr.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        hdr.setStretchLastSection(True)
        hdr.setDefaultAlignment(Qt.AlignmentFlag.AlignCenter)
        font = ui_font(point_size=8)
        table.setFont(font)
        table.verticalHeader().setDefaultSectionSize(ROW_HEIGHT)
        table.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        return table

    def update_ba_bid(self, bid: float | None) -> None:
        """刷新买卖盘之间的 BA 买价标签（无效则隐藏）。"""
        if bid is None or bid <= 0:
            if self.ba_mid_label.isVisible():
                self.ba_mid_label.setVisible(False)
            if self.ba_mid_label.text():
                self.ba_mid_label.setText("")
        else:
            text = f"{bid:.3f}"
            if not self.ba_mid_label.isVisible():
                self.ba_mid_label.setVisible(True)
            if self.ba_mid_label.text() != text:
                self.ba_mid_label.setText(text)

    def update_ba_mid(self, mid: float | None) -> None:
        """兼容旧名：请改用 update_ba_bid。"""
        self.update_ba_bid(mid)

    def update_book(self, book: OrderBook) -> None:
        """用最新盘口刷新买/卖盘表格（买绿卖红，空档灰显）。"""
        rows = min(max(len(book.bids), len(book.asks), 1), self._book_rows)
        if self.bid_table.rowCount() != rows:
            self.bid_table.setRowCount(rows)
            self.ask_table.setRowCount(rows)
            self._sync_table_heights()
        green = QColor("#34d399")
        red = QColor("#f87171")
        muted = QColor("#64748b")

        for row in range(rows):
            bid_price = bid_qty = ask_price = ask_qty = ""
            if row < len(book.bids):
                bid_price = f"{book.bids[row].price:.3f}"
                bid_qty = f"{book.bids[row].quantity:.3f}"
            if row < len(book.asks):
                ask_price = f"{book.asks[row].price:.3f}"
                ask_qty = f"{book.asks[row].quantity:.3f}"

            bid_items = [
                (bid_price, green, Qt.AlignmentFlag.AlignCenter),
                (bid_qty, green, Qt.AlignmentFlag.AlignCenter),
            ]
            ask_items = [
                (ask_price, red, Qt.AlignmentFlag.AlignCenter),
                (ask_qty, red, Qt.AlignmentFlag.AlignCenter),
            ]
            for col, (text, color, align) in enumerate(bid_items):
                self._set_book_cell(self.bid_table, row, col, text, color if text else muted, align)
            for col, (text, color, align) in enumerate(ask_items):
                self._set_book_cell(self.ask_table, row, col, text, color if text else muted, align)

    def _set_book_cell(
        self,
        table: QTableWidget,
        row: int,
        col: int,
        text: str,
        color: QColor,
        align: Qt.AlignmentFlag,
    ) -> None:
        """就地更新单元格文本/颜色（仅变化时改动以减少重绘）。"""
        item = table.item(row, col)
        if item is None:
            item = QTableWidgetItem(text)
            item.setTextAlignment(align)
            table.setItem(row, col, item)
        elif item.text() != text:
            item.setText(text)
        if item.foreground().color() != color:
            item.setForeground(color)
        if item.textAlignment() != align:
            item.setTextAlignment(align)

    def refresh_theme(self) -> None:
        """主题切换钩子（盘口表样式由 QSS 控制，无需额外处理）。"""
        pass
