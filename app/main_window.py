"""主窗口：组织三栏布局（黄金/中栏汇总/白银），连接 SpreadEngine 与各 UI 组件。

负责：行情/持仓/盈亏的展示刷新、手动与自动对冲下单的入口与回执、告警与连接状态、
主题与布局切换、授权门禁校验，以及配置的加载/保存。
"""

from __future__ import annotations

import threading
import time

from PySide6.QtCore import Qt, QTimer, QPoint, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QGuiApplication, QShowEvent
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSplitter,
    QStatusBar,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from app.core.branding import APP_NAME, app_icon_path
from app.core.auto_trade import (
    AutoTradeState,
    collect_auto_close_progress,
    collect_auto_trade_progress,
    diagnose_auto_trade_block,
    evaluate_auto_closes,
    evaluate_auto_trades,
    is_spread_threshold_hint,
)
from app.core.license.client import LicenseError
from app.core.license.service import LicenseService
from app.core.build_config import LICENSE_REQUIRED
from app.core.app_log import LogLevel, should_log
from app.core.config import load_config, save_config, save_config_async
from app.core.models import AppConfig, ConnectionMode, GoldOrderMode, HedgeMode, LayoutMode
from app.core.network_status import NetworkStatus
from app.core.order_mode import auto_trade_lane
from app.core.spread_engine import SpreadEngine
from app.core.theme import load_stylesheet, repolish_tree
from app.core.trading_service import detect_hedge_mode
from app.core.voice import VoiceAnnouncer
from app.widgets.account_balance_widget import BalanceTransferDialog, PlatformAccountRow
from app.widgets.connection_settings_dialog import ConnectionSettingsDialog
from app.widgets.log_panel import LogPanel
from app.widgets.profit_calculator_dialog import ProfitCalculatorDialog
from app.widgets.symbol_trade_panel import BOOK_PANEL_WIDTH, SymbolActionStrip, SymbolTradePanel
from app.widgets.trade_confirm_dialog import TradeConfirmDialog


AUTO_MAKER_RETRY_COOLDOWN_SEC = 2.0  # Maker 自动委托未成交撤单后的重试冷却（秒）


class MainWindow(QMainWindow):
    """应用主窗口：装配三栏 UI、引擎与各类信号，并承载交易/告警/配置交互。"""

    _monitor_start_checked = Signal(bool, str)

    def __init__(
        self,
        license_service: LicenseService | None = None,
        *,
        demo_seed: bool = False,
        demo_seed_mixed: bool = False,
    ):
        super().__init__()
        self.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
        self.setWindowOpacity(0.0)
        self._demo_seed = demo_seed
        self._demo_seed_mixed = demo_seed_mixed
        self.setUpdatesEnabled(False)
        self.license_service = license_service
        self.setWindowTitle(APP_NAME)
        self.resize(1400, 1080)
        icon_path = app_icon_path()
        if icon_path is not None:
            from PySide6.QtGui import QIcon

            self.setWindowIcon(QIcon(str(icon_path)))
        self.setMinimumSize(640, 480)

        self.config = load_config()
        self.engine = SpreadEngine(self.config)
        self._auto_trade_state = AutoTradeState()
        self._auto_trade_hint_last: dict[str, float] = {}
        self._pending_auto_trade: tuple[str, str, str, str] | None = None
        self._pending_auto_maker_restore: list[tuple[str, str]] = []
        self._pending_auto_maker_manual_cancel = False
        self._auto_trade_reevaluate_pending = False
        self._announced_auto_maker_orders: set[tuple[str, str]] = set()
        self._current_ba_open_order_keys: set[tuple[str, str]] = set()
        self._auto_maker_timeout_tokens: dict[tuple[str, str], int] = {}
        self._auto_maker_timeout_seq = 0
        # Maker 自动委托未成交被撤单后的重试冷却：避免每个行情 tick 立即重挂，
        # 导致交易按钮长期置灰、UI 卡顿。键=品种，值=可再次触发的最早时刻。
        self._auto_maker_retry_cooldown_until: dict[str, float] = {}
        self._manual_trade_notify = False
        self._pending_status_preset: str | None = None
        self._trade_dialogs: dict[str, TradeConfirmDialog] = {}
        self._profit_calculator_dialog: ProfitCalculatorDialog | None = None
        self._monitor_buttons_on_header = True
        self._pending_demo_start = False
        self._demo_start_scheduled = False
        self._ui_bootstrapping = True
        self._last_open_orders_log = ""
        self._last_ba_account = None   # 最近一次 BA 账户资金快照
        self._last_mt5_account = None  # 最近一次 EX 账户资金快照
        self._last_network: NetworkStatus | None = None
        self._monitor_start_pending = False
        self._voice = VoiceAnnouncer()  # 自动下单成功取消勾选时语音播报

        central = QWidget()
        central.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
        central.setVisible(False)
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(12, 10, 12, 8)
        root.setSpacing(8)

        root.addLayout(self._build_header())

        self._columns_splitter = QSplitter(Qt.Orientation.Horizontal, central)
        self._columns_splitter.setObjectName("columnsSplitter")
        self._columns_splitter.setHandleWidth(6)
        self._columns_splitter.setChildrenCollapsible(False)

        self.gold_panel = SymbolTradePanel("xau", "黄金 · 币安盘口", parent=self._columns_splitter)
        self.silver_panel = SymbolTradePanel("xag", "白银 · 币安盘口", parent=self._columns_splitter)
        self.gold_actions = SymbolActionStrip("xau", parent=self._columns_splitter)
        self.silver_actions = SymbolActionStrip("xag", parent=self._columns_splitter)
        for strip in (self.gold_actions, self.silver_actions):
            strip.setSizePolicy(
                QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred
            )
            strip.setMinimumWidth(140)
        for panel, min_w, stretch in (
            (self.gold_panel, 72, 0),
            (self.gold_actions, 140, 1),
            (self.silver_panel, 72, 0),
            (self.silver_actions, 140, 1),
        ):
            panel.setMinimumWidth(min_w)
            panel.setSizePolicy(
                QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
            )
            self._columns_splitter.addWidget(panel)
            self._columns_splitter.setStretchFactor(
                self._columns_splitter.count() - 1, stretch
            )
        self._columns_splitter.setMaximumHeight(16777215)
        root.addWidget(self._columns_splitter, stretch=1)

        # 运行日志：浮层覆盖在交易区底部。向上拖动顶部手柄只会遮住交易区，
        # 不会压缩上方窗口；几何位置由 _relayout_log_overlay 维护。
        self._root_margins = (12, 10, 12, 8)
        self._log_min_height = 56
        self._log_height = 220
        self.log_panel = LogPanel(parent=central)
        self.log_panel.setMinimumHeight(self._log_min_height)
        self.log_panel.setAutoFillBackground(True)
        self.log_panel.grip.dragged.connect(self._on_log_drag)
        self.log_panel.raise_()

        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_bar.setVisible(False)
        self.status_bar.showMessage("就绪 · 演示模式可直接启动")

        # 订单簿改为「数据驱动」：盘口写入时引擎发 order_book_updated 置脏，
        # 由 _book_timer 以固定低延迟间隔合并刷新一次，避免行情 tick 重复重绘。
        self._book_flush_ms = 150
        self._book_dirty = False
        self._book_timer = QTimer(self)
        self._book_timer.timeout.connect(self._flush_order_book)

        # 低频兜底：把 UI 设置同步给引擎/连接器，防止个别控件信号漏接。
        # 行情热路径已不再每 tick 合并配置，仅靠控件改动即时同步 + 此处定时校正。
        self._cfg_sync_timer = QTimer(self)
        self._cfg_sync_timer.timeout.connect(self._periodic_cfg_sync)
        self._cfg_sync_timer.start(2500)

        self._wire_signals()
        self._monitor_start_checked.connect(self._on_monitor_start_checked)
        self.gold_actions.load_settings_from(self.config)
        self.silver_actions.load_settings_from(self.config)

    def present(self) -> None:
        """首屏展示：后台完成引导后再显示，避免初始化阶段连闪。"""
        QTimer.singleShot(0, self._finish_ui_bootstrap)

    def _finish_ui_bootstrap(self) -> None:
        self._sync_theme_btn()
        self._apply_theme(self.config.theme)
        self._apply_layout_mode()
        self._finalize_startup()
        self._sync_monitor_buttons()
        self._ui_bootstrapping = False
        self._refresh_status_badges()
        if self._pending_demo_start and not self._demo_start_scheduled:
            self._pending_demo_start = False
            self._demo_start_scheduled = True
            self._start_demo_monitoring()
        self._sync_columns_sizes()
        QTimer.singleShot(0, self._reveal_main_window)

    def _reveal_main_window(self) -> None:
        """一次性显示主窗口（此前始终离屏构建）。"""
        central = self.centralWidget()
        if central is not None:
            central.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, False)
            central.setUpdatesEnabled(True)
            central.setVisible(True)
        self.status_bar.setVisible(True)
        self.setUpdatesEnabled(True)
        self.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, False)
        self.setWindowOpacity(1.0)
        self.show()
        self._sync_columns_sizes()
        self._relayout_log_overlay()
        self.raise_()
        self.activateWindow()

    def _start_demo_monitoring(self) -> None:
        """演示模式自动启用监控：静默启动，不再二次弹窗或重复校验授权。"""
        if not self.config.demo_mode or self.engine.is_running:
            return
        self.config = self._merge_config()
        save_config(self.config)
        self.engine.update_config(self.config)
        self.engine.start()
        self._sync_monitor_buttons()
        self._sync_ba_refresh_timers()
        self._refresh_status_badges()
        self._refresh_order_book()
        self.status_bar.showMessage(f"监控运行中 · {self._mode_label()}")

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        self._relayout_log_overlay()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._relayout_log_overlay()

    def _on_log_drag(self, dy: int) -> None:
        """拖动日志面板顶部手柄：向上拖（dy<0）增高以遮住更多交易区，向下拖缩小。"""
        self._log_height -= dy
        self._relayout_log_overlay()
        # 回写 clamp 后的真实高度，避免越界后反向拖动产生滞后
        self._log_height = self.log_panel.height()

    def _relayout_log_overlay(self) -> None:
        """把日志浮层贴在交易区底部；高度上限到交易区顶部（不遮住顶部 header）。"""
        central = self.centralWidget()
        panel = getattr(self, "log_panel", None)
        if central is None or panel is None or not hasattr(self, "_columns_splitter"):
            return
        left, top, right, bottom = self._root_margins
        cols_top = self._columns_splitter.geometry().top()
        if cols_top <= 0:
            cols_top = top
        bottom_y = central.height() - bottom
        avail_w = max(0, central.width() - left - right)
        max_h = max(self._log_min_height, bottom_y - cols_top)
        h = max(self._log_min_height, min(self._log_height, max_h))
        panel.setGeometry(left, bottom_y - h, avail_w, h)
        panel.raise_()

    def _finalize_startup(self) -> None:
        """在窗口显示前完成静态初始化，连接与行情放到显示后。"""
        if self._demo_seed or self._demo_seed_mixed:
            self._load_demo_seed_positions()
        if self.config.demo_mode and not self.engine.is_running:
            self._pending_demo_start = True
        else:
            self._on_network_status(
                NetworkStatus.from_engine(self.engine, self.engine.is_running)
            )
        if self._demo_seed or self._demo_seed_mixed:
            QTimer.singleShot(500, self._refresh_demo_seed_positions)

    def _load_demo_seed_positions(self) -> None:
        if not self.config.demo_mode:
            self._append_log(LogLevel.INFO, "演示持仓预览需使用演示模式")
            return
        from app.core.demo_seed import seed_hedge_alert_mixed, seed_hedge_alert_preview

        if self._demo_seed_mixed:
            summary = seed_hedge_alert_mixed(self.engine.binance, self.engine.mt5)
        else:
            summary = seed_hedge_alert_preview(self.engine.binance, self.engine.mt5)
        for line in summary.splitlines():
            self._append_log(LogLevel.INFO, f"[演示持仓] {line}")

    def _refresh_demo_seed_positions(self) -> None:
        self.engine.refresh_positions()
        self.status_bar.showMessage("演示持仓已载入 · 请查看黄金/白银告警与「补对冲」", 12000)

    def _sync_monitor_buttons(self) -> None:
        running = self.engine.is_running
        pending = self._monitor_start_pending
        self.start_btn.setEnabled(not running and not pending)
        self.stop_btn.setEnabled(running and not pending)
        forbidden = Qt.CursorShape.ForbiddenCursor
        hand = Qt.CursorShape.PointingHandCursor
        self.start_btn.setCursor(hand if not running and not pending else forbidden)
        self.stop_btn.setCursor(hand if running and not pending else forbidden)

    def _build_header(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(6)
        row.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        host = self.centralWidget()

        brand = QVBoxLayout()
        brand.setSpacing(0)
        title = QLabel(APP_NAME, parent=host)
        title.setObjectName("appTitle")
        brand.addWidget(title)
        row.addLayout(brand)
        row.addSpacing(6)

        platform_wrap = QWidget(host)
        platform_wrap.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
        platform_col = QVBoxLayout(platform_wrap)
        platform_col.setContentsMargins(0, 0, 0, 0)
        platform_col.setSpacing(2)
        self.ba_row = PlatformAccountRow("币安", is_ba=True, currency_hint="USDT", parent=platform_wrap)
        self.mt5_row = PlatformAccountRow("EX", currency_hint="USD", parent=platform_wrap)
        self.ba_row.transfer_clicked.connect(self._on_ba_transfer)
        self.mt5_row.transfer_clicked.connect(self._on_ex_transfer)
        platform_col.addWidget(self.ba_row)
        platform_col.addWidget(self.mt5_row)
        row.addWidget(platform_wrap, 0, Qt.AlignmentFlag.AlignVCenter)

        row.addStretch()

        self.license_expires_lbl = QLabel("", parent=host)
        self.license_expires_lbl.setObjectName("fieldHint")
        row.addWidget(self.license_expires_lbl, 0, Qt.AlignmentFlag.AlignVCenter)

        self.profit_btn = QPushButton("利润计算器", parent=host)
        self._style_toolbar_btn(self.profit_btn)
        row.addWidget(self.profit_btn)

        self.layout_mode_btn = QPushButton("单品种", parent=host)
        self._style_toolbar_btn(self.layout_mode_btn, checkable=True)
        self.layout_mode_btn.clicked.connect(self._on_layout_mode_toggled)
        row.addWidget(self.layout_mode_btn)

        self.symbol_switch_btn = QPushButton("🥈 切换白银", parent=host)
        self._style_toolbar_btn(self.symbol_switch_btn)
        self.symbol_switch_btn.clicked.connect(self._on_symbol_switch)
        row.addWidget(self.symbol_switch_btn)

        self.theme_btn = QPushButton("浅色", parent=host)
        self._style_toolbar_btn(self.theme_btn, checkable=True)
        self.theme_btn.clicked.connect(self._on_theme_toggled)
        row.addWidget(self.theme_btn)

        self.settings_btn = QPushButton("设置", parent=host)
        self._style_toolbar_btn(self.settings_btn)
        self.settings_btn.clicked.connect(self._open_settings)
        row.addWidget(self.settings_btn)

        self.save_btn = QPushButton("保存", parent=host)
        self._style_toolbar_btn(self.save_btn)
        self.save_btn.clicked.connect(self._on_save)
        row.addWidget(self.save_btn)

        self.start_btn = QPushButton("启用监控", parent=host)
        self._style_toolbar_btn(self.start_btn, primary=True)
        self.start_btn.clicked.connect(self._on_start)
        row.addWidget(self.start_btn)

        self.stop_btn = QPushButton("停止监控", parent=host)
        self._style_toolbar_btn(self.stop_btn, danger=True)
        self.stop_btn.clicked.connect(self._on_stop)
        row.addWidget(self.stop_btn)
        self._header_row = row
        return row

    def _style_toolbar_btn(
        self,
        btn: QPushButton,
        *,
        primary: bool = False,
        danger: bool = False,
        checkable: bool = False,
    ) -> None:
        if primary:
            btn.setObjectName("primaryButton")
        elif danger:
            btn.setObjectName("dangerButton")
        else:
            btn.setObjectName("ghostButton")
        btn.setProperty("compact", True)
        btn.setCheckable(checkable)
        btn.setFixedHeight(28)
        btn.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)

    def _sync_theme_btn(self) -> None:
        dark = self.config.theme == "dark"
        self.theme_btn.blockSignals(True)
        self.theme_btn.setChecked(dark)
        self.theme_btn.setText("深色" if dark else "浅色")
        self.theme_btn.blockSignals(False)

    def _apply_theme(self, theme: str) -> None:
        app = QApplication.instance()
        if app:
            load_stylesheet(app, theme)
        self.gold_actions.refresh_theme()
        self.silver_actions.refresh_theme()
        self.gold_panel.refresh_theme()
        self.silver_panel.refresh_theme()
        repolish_tree(self)

    def _merge_config(self) -> AppConfig:
        self.gold_actions.apply_settings_to(self.config)
        self.silver_actions.apply_settings_to(self.config)
        return self.config

    def _commit_auto_trade_inputs(self, preset_id: str | None = None) -> None:
        """把正在编辑的自动交易数字框文本提交为 value，避免刚输入未失焦时读到旧值。"""
        strips = (
            (self.gold_actions,)
            if preset_id == "xau"
            else (self.silver_actions,)
            if preset_id == "xag"
            else (self.gold_actions, self.silver_actions)
        )
        for strip in strips:
            for spin in strip.auto_trade_settings.iter_spin_widgets():
                if hasattr(spin, "interpretText"):
                    spin.interpretText()

    def _relocate_monitor_buttons(self) -> None:
        single = self.config.layout_mode == LayoutMode.SINGLE.value

        if single:
            preset = self.config.single_symbol_preset
            target_strip = (
                self.gold_actions if preset == "xau" else self.silver_actions
            )
            current_strip = self._monitor_buttons_on_strip()
            if current_strip is target_strip and not self._monitor_buttons_on_header:
                self._sync_monitor_buttons()
                return
            if self._header_row.indexOf(self.start_btn) >= 0:
                self._header_row.removeWidget(self.start_btn)
            if self._header_row.indexOf(self.stop_btn) >= 0:
                self._header_row.removeWidget(self.stop_btn)
            self.gold_actions.detach_monitor_buttons()
            self.silver_actions.detach_monitor_buttons()
            target_strip.attach_monitor_buttons(self.start_btn, self.stop_btn)
            self._monitor_buttons_on_header = False
        else:
            if (
                self._monitor_buttons_on_header
                and self._header_row.indexOf(self.start_btn) >= 0
            ):
                self._sync_monitor_buttons()
                return
            self.gold_actions.detach_monitor_buttons()
            self.silver_actions.detach_monitor_buttons()
            if self._header_row.indexOf(self.start_btn) < 0:
                self._header_row.addWidget(self.start_btn)
            if self._header_row.indexOf(self.stop_btn) < 0:
                self._header_row.addWidget(self.stop_btn)
            self._monitor_buttons_on_header = True

        self.start_btn.setVisible(True)
        self.stop_btn.setVisible(True)
        self._sync_monitor_buttons()

    def _monitor_buttons_on_strip(self) -> SymbolActionStrip | None:
        host = self.start_btn.parent()
        for strip in (self.gold_actions, self.silver_actions):
            if host is strip._monitor_host:
                return strip
        return None

    def _sync_ba_refresh_timers(self) -> None:
        timer = getattr(self, "_book_timer", None)
        if timer is None:
            return
        if not self.engine.is_running:
            timer.stop()
            return
        # 固定低延迟合并间隔（与 BA 轮询间隔解耦）：盘口随 depth WS/兜底数据置脏后在此刷新
        if not timer.isActive():
            timer.start(self._book_flush_ms)

    def _on_book_updated(self, symbol: str) -> None:
        """引擎通知盘口数据已更新：仅置脏，由 _book_timer 合并节流后统一重绘。"""
        self._book_dirty = True

    def _flush_order_book(self) -> None:
        if not self._book_dirty:
            return
        self._book_dirty = False
        self._refresh_order_book()

    def _periodic_cfg_sync(self) -> None:
        """低频兜底同步：每 ~2.5s 把 UI 设置合并并下发给引擎/连接器。

        行情热路径已不再每 tick 调用 _merge_config()/sync_config()，控件改动会即时同步；
        此处仅作为防漏接的兜底，监控未运行时跳过以省开销。
        """
        if not self.engine.is_running:
            return
        self.config = self._merge_config()
        self.engine.sync_config(self.config)

    def _open_settings(self) -> None:
        dlg = ConnectionSettingsDialog(self.config, self)
        if dlg.exec() != ConnectionSettingsDialog.DialogCode.Accepted:
            return
        dlg.apply_connection_to(self.config)
        self.config = self._merge_config()
        save_config(self.config)
        QTimer.singleShot(0, self._apply_connection_settings)

    def _apply_connection_settings(self) -> None:
        """设置保存后异步重启连接，避免阻塞对话框关闭。"""
        self.engine.update_config(self.config)
        if self.config.demo_mode and not self.engine.is_running:
            self._on_start()
        self._sync_ba_refresh_timers()
        self._refresh_order_book()
        interval = self.config.ba_refresh_interval_sec
        msg = f"连接与参数已保存 · BA 刷新间隔 {interval:.1f}s"
        if self.engine.is_running:
            msg += "（监控已重启并生效）"
        else:
            msg += "（启用监控后生效）"
        self._append_log(LogLevel.INFO, msg)
        self.status_bar.showMessage(msg)
        if self.license_service:
            threading.Thread(
                target=self.license_service.sync_accounts_now,
                daemon=True,
                name="sync-accounts",
            ).start()

    def _on_theme_toggled(self) -> None:
        dark = self.theme_btn.isChecked()
        theme = "dark" if dark else "light"
        self.theme_btn.setText("深色" if dark else "浅色")
        self.config.theme = theme
        save_config(self.config)
        self._apply_theme(theme)
        self.status_bar.showMessage("已切换为深色主题" if dark else "已切换为浅色主题")

    def _on_layout_mode_toggled(self) -> None:
        single = self.layout_mode_btn.isChecked()
        self.config.layout_mode = LayoutMode.SINGLE.value if single else LayoutMode.DUAL.value
        if single and self.config.single_symbol_preset not in ("xau", "xag"):
            self.config.single_symbol_preset = "xau"
        save_config(self.config)
        self._apply_layout_mode()
        self.status_bar.showMessage("已切换为单品种模式" if single else "已切换为双品种模式")

    def _on_symbol_switch(self) -> None:
        if self.config.layout_mode != LayoutMode.SINGLE.value:
            return
        self.config.single_symbol_preset = (
            "xag" if self.config.single_symbol_preset == "xau" else "xau"
        )
        save_config(self.config)
        self._apply_layout_mode()
        label = "黄金" if self.config.single_symbol_preset == "xau" else "白银"
        self.status_bar.showMessage(f"单品种：{label}")

    def _column_widgets_all(self) -> tuple[QWidget, ...]:
        return (
            self.gold_panel,
            self.gold_actions,
            self.silver_panel,
            self.silver_actions,
        )

    def _apply_column_visibility(self) -> None:
        """固定五列 splitter，仅切换可见性，避免单/双模式重建控件。"""
        single = self.config.layout_mode == LayoutMode.SINGLE.value
        widgets = self._column_widgets_all()
        if not single:
            for widget in widgets:
                widget.setVisible(True)
                widget.setMaximumWidth(16777215)
            return

        show_xau = self.config.single_symbol_preset == "xau"
        visibility = (
            show_xau,
            show_xau,
            not show_xau,
            not show_xau,
        )
        for widget, visible in zip(widgets, visibility):
            widget.setVisible(visible)
            if visible:
                widget.setMaximumWidth(16777215)
            else:
                widget.setMaximumWidth(0)

    def _sync_columns_sizes(self) -> None:
        """按实际内容宽度分配 splitter，订单簿列不撑出空白。"""
        total = max(self._columns_splitter.width(), 1)
        single = self.config.layout_mode == LayoutMode.SINGLE.value
        if single:
            show_xau = self.config.single_symbol_preset == "xau"
            actions = self.gold_actions if show_xau else self.silver_actions
            book_w = BOOK_PANEL_WIDTH
            action_col_min = 280
            act_w = max(actions.minimumSizeHint().width(), action_col_min)
            rest_w = max(total - book_w - act_w, 0)
            if show_xau:
                sizes = [book_w, act_w + rest_w, 0, 0]
                stretch_at = 1
            else:
                sizes = [0, 0, book_w, act_w + rest_w]
                stretch_at = 3
            for i in range(len(sizes)):
                self._columns_splitter.setStretchFactor(i, 1 if i == stretch_at else 0)
            self._columns_splitter.setSizes(sizes)
            return

        gold_w = BOOK_PANEL_WIDTH
        action_col_min = 280
        gold_act_w = max(self.gold_actions.minimumSizeHint().width(), action_col_min)
        silver_act_w = max(self.silver_actions.minimumSizeHint().width(), action_col_min)
        silver_w = BOOK_PANEL_WIDTH
        extra = max(total - gold_w - gold_act_w - silver_act_w - silver_w, 0)
        for panel in (self.gold_panel, self.silver_panel):
            panel.setMaximumWidth(16777215)
        sizes = (
            gold_w,
            gold_act_w + extra // 2,
            silver_w,
            silver_act_w + extra - extra // 2,
        )
        for i, _size in enumerate(sizes):
            self._columns_splitter.setStretchFactor(i, 1 if i in (1, 3) else 0)
        self._columns_splitter.setSizes(list(sizes))

    def _apply_layout_mode(self) -> None:
        restore_updates = self.updatesEnabled()
        central = self.centralWidget()
        if central is not None:
            central.setUpdatesEnabled(False)
        self.setUpdatesEnabled(False)
        self._columns_splitter.setUpdatesEnabled(False)
        try:
            single = self.config.layout_mode == LayoutMode.SINGLE.value
            self.layout_mode_btn.blockSignals(True)
            self.layout_mode_btn.setChecked(single)
            self.layout_mode_btn.setText("双品种" if single else "单品种")
            self.layout_mode_btn.blockSignals(False)
            self.symbol_switch_btn.setVisible(single)

            preset = self.config.single_symbol_preset
            show_xau = not single or preset == "xau"
            show_xag = not single or preset == "xag"
            self.gold_panel.set_compact(single and show_xau)
            self.silver_panel.set_compact(single and show_xag)
            self._apply_column_visibility()

            if single:
                if preset == "xau":
                    self.symbol_switch_btn.setText("🥈 切换白银")
                else:
                    self.symbol_switch_btn.setText("🥇 切换黄金")

            self._relocate_monitor_buttons()
            self._sync_columns_sizes()
        finally:
            self._columns_splitter.setUpdatesEnabled(True)
            if central is not None:
                central.setUpdatesEnabled(True)
            if restore_updates:
                self.setUpdatesEnabled(True)

    def _wire_signals(self) -> None:
        for strip in (self.gold_actions, self.silver_actions):
            alerts = strip.alert_settings
            for toggle in (alerts.spread_enabled, alerts.liq_enabled):
                toggle.stateChanged.connect(self._on_alert_settings_changed)
            for w in alerts.iter_watch_widgets():
                if w in (alerts.spread_enabled, alerts.liq_enabled):
                    continue
                w.valueChanged.connect(self._on_alert_settings_changed)
        self.profit_btn.clicked.connect(self._open_profit_calculator)
        for strip in (self.gold_actions, self.silver_actions):
            strip.section_layout_changed.connect(self._on_panel_sections_changed)
        self._wire_trade_panel(self.gold_actions, "xau")
        self._wire_trade_panel(self.silver_actions, "xag")
        for strip in (self.gold_actions, self.silver_actions):
            auto = strip.auto_trade_settings
            for w in auto.iter_watch_widgets():
                if isinstance(w, QCheckBox):
                    w.toggled.connect(self._on_auto_trade_toggled)
                else:
                    w.valueChanged.connect(self._on_auto_trade_toggled)
            auto.manual_cancel_requested.connect(self._on_manual_cancel_orders)

        self.engine.market_updated.connect(self._on_market)
        self.engine.order_book_updated.connect(self._on_book_updated)
        self.engine.connection_changed.connect(self._on_connection)
        self.engine.network_status_changed.connect(self._on_network_status)
        self.engine.binance.ws_state_changed.connect(self._on_ws_state)
        self.engine.binance.open_orders_changed.connect(self._on_open_orders_changed)
        self.engine.account_updated.connect(self._on_account_updated)
        self.engine.log_message.connect(self.log_panel.append)
        self.engine.positions_updated.connect(self._on_positions)
        self.engine.open_orders_updated.connect(self._on_open_orders)
        self.engine.trade_started.connect(self._on_trade_started)
        self.engine.trade_finished.connect(self._on_trade_finished)
        self.engine.alert_triggered.connect(self._on_alert)
        self.engine.trade_recorded.connect(self._on_trade_recorded)
        if self.license_service and LICENSE_REQUIRED:
            self.license_service.revoked.connect(self._on_license_revoked)
            self.license_service.auto_trade_changed.connect(self._on_auto_trade_availability_changed)
            self.license_service.status_changed.connect(self._on_license_status_changed)
            self.license_service.set_telemetry_provider(self._license_telemetry)
            self.license_service.set_connection_mode_provider(
                lambda: self.config.connection_mode
            )
        elif self.license_service:
            self.license_service.status_changed.connect(self._on_license_status_changed)
            self.license_service.set_telemetry_provider(self._license_telemetry)
            self.license_service.set_connection_mode_provider(
                lambda: self.config.connection_mode
            )
        self._apply_auto_trade_availability(initial=True)
        self._refresh_license_expires_label()

    def _append_log(self, level: LogLevel, message: str) -> None:
        if should_log(self.config.log_level, level):
            self.log_panel.append(message)

    def _wire_trade_panel(self, panel: SymbolActionStrip, preset_id: str) -> None:
        panel.trade_entry_btn.clicked.connect(lambda: self._open_trade_dialog(preset_id))
        panel.position_refresh_requested.connect(self._on_refresh_positions)
        panel.hedge_repair_requested.connect(self._on_hedge_repair_requested)

    def _on_panel_sections_changed(self) -> None:
        self.config = self._merge_config()
        save_config(self.config)

    def _on_hedge_repair_requested(self, preset_id: str, repair) -> None:
        self._open_trade_dialog(
            preset_id,
            active_mode=repair.mode,
            order_mode=repair.order_mode,
        )

    def _ensure_license(self, action: str = "此操作", *, fast: bool = False) -> bool:
        if not LICENSE_REQUIRED or not self.license_service:
            return True
        try:
            if fast:
                self.license_service.ensure_approved_for_trade(
                    self.config.connection_mode, fast=True
                )
            else:
                self.license_service.ensure_approved()
            return True
        except LicenseError as exc:
            # 授权或 BA/EX 账号未通过时不再弹窗（多个弹窗会相互阻塞导致界面卡死），
            # 仅记录日志并在状态栏提示，同时阻止本次操作。
            self._append_log(LogLevel.ERROR, f"{action}被拒绝：{exc}")
            self.status_bar.showMessage(f"{action}已被拒绝：{exc}", 8000)
            return False

    def _on_license_revoked(self, message: str) -> None:
        # 后台停用账号/撤销授权时：停止监控并记录日志，不再弹窗打断用户。
        if self.engine.is_running:
            self.engine.stop()
        self._sync_monitor_buttons()
        self._refresh_status_badges()
        self._append_log(LogLevel.ERROR, f"授权已失效，监控已停止：{message}")
        self.status_bar.showMessage(f"授权已失效，监控已停止：{message}", 10000)

    def _apply_auto_trade_availability(self, *, initial: bool = False) -> None:
        """按运营后台开通状态显示/隐藏自动下单板块。"""
        if self.license_service is not None:
            available = self.license_service.client.is_auto_trade_enabled
        else:
            # 无授权服务（测试/本地调试）：不做后台门控，默认放开
            available = True
        cancelled = 0
        for strip in (self.gold_actions, self.silver_actions):
            cancelled += strip.set_auto_trade_available(available)
        if cancelled and not initial:
            self.config = self._merge_config()
            save_config(self.config)

    def _on_auto_trade_availability_changed(self, available: bool) -> None:
        self._apply_auto_trade_availability()
        if available:
            self._append_log(LogLevel.INFO, "运营后台已开通自动下单功能")
        else:
            self._append_log(LogLevel.INFO, "运营后台已关闭自动下单功能，相关勾选已取消")

    def _on_trade_recorded(self, record) -> None:
        # 成交上报含同步网络请求（最长 15s），必须放到后台线程，
        # 否则会在每次下单成交后阻塞主线程导致界面卡顿。
        service = self.license_service
        if service is None:
            return
        threading.Thread(
            target=service.upload_trade,
            args=(record,),
            daemon=True,
            name="upload-trade",
        ).start()

    def _open_trade_dialog(
        self,
        preset_id: str,
        *,
        active_mode: str | None = None,
        order_mode: str | None = None,
    ) -> None:
        # 打开交易弹窗属于点击热路径：只做本地快速授权判断，避免网络心跳卡住 UI。
        if not self._ensure_license("手动交易", fast=True):
            return
        existing = self._trade_dialogs.get(preset_id)
        if existing is not None and existing.isVisible():
            if active_mode is not None:
                existing.set_active_mode(active_mode)
            if order_mode is not None:
                existing.set_order_mode(order_mode)
            existing.raise_()
            existing.activateWindow()
            if not existing.user_positioned():
                self._position_trade_dialog(existing, preset_id)
            return
        if active_mode is None:
            active_mode = detect_hedge_mode(preset_id, self.engine.positions)
        cfg = self._merge_config()
        dlg = TradeConfirmDialog(preset_id, cfg, active_mode, self)
        if order_mode is not None:
            dlg.set_order_mode(order_mode)
        self._trade_dialogs[preset_id] = dlg

        def persist_dialog_ratio(ref=dlg, *, sync: bool = False) -> None:
            self.config = self._merge_config()
            ref.apply_ratio_to(self.config)
            self.engine.sync_config(self.config)
            if sync:
                save_config(self.config)
            else:
                save_config_async(self.config)

        def _drop_dialog(_=None, pid: str = preset_id, ref=dlg) -> None:
            if self._trade_dialogs.get(pid) is ref:
                self._trade_dialogs.pop(pid, None)

        dlg.ratio_changed.connect(persist_dialog_ratio)
        dlg.closed.connect(lambda _=None: persist_dialog_ratio(sync=True))
        dlg.closed.connect(_drop_dialog)

        def on_trade_requested(action: str, mode: str) -> None:
            if not self._ensure_license("手动交易", fast=True):
                dlg.set_actions_enabled(True)
                return
            order_mode = dlg.gold_order_mode()
            dlg.apply_ratio_to(self.config)
            sym = "黄金" if preset_id == "xau" else "白银"
            self.status_bar.showMessage(f"正在提交{sym}{action}...")
            self.engine.sync_config(self.config)
            self._manual_trade_notify = True
            if action == "开仓":
                started = self.engine.open_hedge(preset_id, mode, order_mode)
            else:
                started = self.engine.close_hedge(preset_id, mode, order_mode)
            # 用返回值判断是否已受理，不读 is_trading：秒成交时后台可能已重置 _trading=False。
            if started:
                def _persist_config() -> None:
                    self.config = self._merge_config()
                    dlg.apply_ratio_to(self.config)
                    save_config_async(self.config)

                threading.Thread(
                    target=_persist_config, daemon=True, name="persist-trade-config"
                ).start()
            else:
                self._manual_trade_notify = False
                dlg.set_actions_enabled(True)

        dlg.trade_requested.connect(on_trade_requested)
        dlg.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        dlg.set_position_callback(lambda d=dlg, pid=preset_id: self._position_trade_dialog(d, pid))
        dlg.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
        dlg._fit_size()
        dlg.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, False)
        dlg.show()
        dlg._fit_size()
        self._position_trade_dialog(dlg, preset_id)
        for other in self._trade_dialogs.values():
            if other.isVisible():
                other.raise_()
        self._refresh_trade_dialog_pnl(dlg)
        QTimer.singleShot(0, self.engine.refresh_positions)

    def _license_telemetry(self) -> dict[str, str]:
        from app.core.license.telemetry import build_license_telemetry

        return build_license_telemetry(
            self.config, self.engine.positions, self.engine.open_orders
        )

    def _refresh_trade_dialog_pnl(self, dlg: TradeConfirmDialog | None = None) -> None:
        positions = self.engine.positions
        ba_q = self.engine.ba_quotes
        mt5_q = self.engine.mt5_quotes
        cfg = self.config
        targets = [dlg] if dlg is not None else list(self._trade_dialogs.values())
        for dialog in targets:
            if dialog is not None and dialog.isVisible():
                dialog.update_pnl(positions, ba_q, mt5_q, cfg)

    def _position_trade_dialog(self, dlg: TradeConfirmDialog, preset_id: str) -> None:
        if dlg.user_positioned():
            return
        strip = self.gold_actions if preset_id == "xau" else self.silver_actions
        dlg._fit_size()
        dlg_w = dlg.width()
        dlg_h = dlg.height()
        btn = strip.trade_entry_btn
        btn_origin = btn.mapToGlobal(QPoint(0, btn.height()))
        if preset_id == "xau":
            x = strip.mapToGlobal(QPoint(0, 0)).x()
        else:
            x = strip.mapToGlobal(QPoint(strip.width(), 0)).x() - dlg_w
        y = btn_origin.y() + 8
        screen = QGuiApplication.screenAt(btn_origin)
        if screen is None:
            screen = QGuiApplication.primaryScreen()
        if screen is not None:
            avail = screen.availableGeometry()
            x = max(avail.left(), min(x, avail.right() - dlg_w))
            y = max(avail.top(), min(y, avail.bottom() - dlg_h))
        dlg.set_auto_positioning(True)
        dlg.move(x, y)
        dlg.set_auto_positioning(False)

    def _any_auto_trade_enabled(self) -> bool:
        return (
            self.gold_actions.auto_trade_settings.any_enabled()
            or self.silver_actions.auto_trade_settings.any_enabled()
        )

    def _auto_trade_hint(self, message: str, preset_id: str | None = None) -> None:
        now = time.time()
        # 点差未达阈值时文案含实时点差，不能用整句做节流 key，否则每 tick 都会打日志。
        throttle_key = message.rsplit("点差", 1)[0] if is_spread_threshold_hint(message) else message
        if self._auto_trade_hint_last.get(throttle_key, 0.0) + 15.0 > now:
            return
        self._auto_trade_hint_last[throttle_key] = now
        if not is_spread_threshold_hint(message):
            self._append_log(LogLevel.INFO, message)
        if preset_id is None:
            if "黄金" in message:
                preset_id = "xau"
            elif "白银" in message:
                preset_id = "xag"
        if preset_id == "xau":
            targets = (self.gold_actions,)
        elif preset_id == "xag":
            targets = (self.silver_actions,)
        else:
            targets = (self.gold_actions, self.silver_actions)
        for strip in targets:
            if strip.auto_trade_settings.any_enabled():
                strip.auto_trade_settings.set_status(message)

    def _update_auto_trade_progress(self, progress) -> None:
        for strip in (self.gold_actions, self.silver_actions):
            if not strip.auto_trade_settings.any_enabled():
                strip.auto_trade_settings.set_status("")
        if progress is None or progress.hold_sec <= 0:
            return
        strip = self.gold_actions if progress.preset_id == "xau" else self.silver_actions
        text = f"计时中 {progress.elapsed_sec:.1f}/{progress.hold_sec:g}s · {progress.label}"
        strip.auto_trade_settings.set_status(text)
        kind = "自动平仓" if "平仓" in progress.label else "自动开仓"
        self.status_bar.showMessage(f"{kind} · {text}")

    def _on_auto_trade_toggled(self) -> None:
        self.config = self._merge_config()
        save_config(self.config)
        # 控件改动即时下发给引擎/连接器（热路径已不再每 tick sync），确保新参数立即生效
        self.engine.sync_config(self.config)
        # 人工调整勾选/阈值视为显式意图：清掉冷却与计时，满足条件即可立即触发
        self._auto_trade_state.since.clear()
        self._auto_trade_state.close_since.clear()
        if not self.engine.is_running and self._any_auto_trade_enabled():
            self._on_start()
            self._append_log(LogLevel.INFO, "自动下单已开启，监控已启动")
            return
        # 监控已在运行：勾选后立即用最近行情评估一次，避免要等下一拍/点差再次穿越才触发
        update = self.engine.last_market_update
        if update is not None:
            self._maybe_auto_trade(update)

    def _sync_programmatic_auto_trade_change(self) -> None:
        """同步由程序改动的自动交易勾选/锁定状态。"""
        self.config = self._merge_config()
        save_config_async(self.config)
        self.engine.sync_config(self.config)

    def _request_auto_trade_reevaluate(self) -> None:
        """状态变化后用最近行情补评估一次，避免等下一次报价 tick。"""
        if self._auto_trade_reevaluate_pending:
            return
        if self.engine.last_market_update is None:
            return
        self._auto_trade_reevaluate_pending = True
        QTimer.singleShot(0, self._run_auto_trade_reevaluate)

    def _run_auto_trade_reevaluate(self) -> None:
        self._auto_trade_reevaluate_pending = False
        update = self.engine.last_market_update
        if update is None:
            return
        self._maybe_auto_trade(update)

    def _execute_auto_open(self, preset_id: str, mode: str, order_mode: str) -> None:
        if not self._ensure_license("自动下单", fast=True):
            return
        self._commit_auto_trade_inputs(preset_id)
        self.config = self._merge_config()
        self.engine.sync_config(self.config)
        # 互斥：engine.is_trading 由后台线程同步重置，会先于 trade_finished 回主线程取消勾选，
        # 中间存在窗口；用 _pending_auto_trade(主线程读写、随取消勾选同一时机清空)兜住该窗口，
        # 避免同一笔尚未收尾就被重复触发。
        if self.engine.is_trading or self._pending_auto_trade is not None:
            self._append_log(LogLevel.DEBUG, "自动下单：上一笔交易尚未完成，已跳过")
            return
        # 必须在 open_hedge 之前乐观置位：市价/秒成交时后台线程可能在本函数返回前就
        # 完成并 emit trade_finished，若等调用后再读 is_trading 会读到 False 而漏置位，
        # 导致 trade_finished 误判非自动交易、不取消勾选 → 下一拍重复委托(委托2单)。
        self._pending_auto_trade = ("open", preset_id, mode, order_mode)
        self._pending_auto_maker_restore = self._capture_auto_maker_restore(preset_id, order_mode)
        self._pending_auto_maker_manual_cancel = False
        lane = auto_trade_lane(preset_id, order_mode)
        min_open_spread = None
        max_open_spread = None
        if mode == HedgeMode.CONTRACTION.value:
            min_open_spread = self.config.auto_contraction_threshold_lane(preset_id, lane)
        else:
            max_open_spread = self.config.auto_expansion_threshold_lane(preset_id, lane)
        if self.engine.open_hedge(
            preset_id,
            mode,
            order_mode,
            min_open_spread=min_open_spread,
            max_open_spread=max_open_spread,
        ):
            save_config_async(self.config)
        else:
            # 前置校验失败/未启动(不会发 trade_finished)：撤销置位，避免永久卡死
            self._clear_pending_auto_trade_state()

    def _execute_auto_close(self, preset_id: str, mode: str, order_mode: str) -> None:
        if not self._ensure_license("自动平仓", fast=True):
            return
        self._commit_auto_trade_inputs(preset_id)
        self.config = self._merge_config()
        self.engine.sync_config(self.config)
        # 互斥：同 _execute_auto_open，用 _pending_auto_trade 兜住 is_trading 重置与取消勾选之间的窗口，
        # 防止点差持续满足时一次性平掉多手。
        if self.engine.is_trading or self._pending_auto_trade is not None:
            self._append_log(LogLevel.DEBUG, "自动平仓：上一笔交易尚未完成，已跳过")
            return
        # 必须在 close_hedge 之前乐观置位，理由同 _execute_auto_open（防秒成交漏置位重复委托）。
        self._pending_auto_trade = ("close", preset_id, mode, order_mode)
        self._pending_auto_maker_restore = self._capture_auto_maker_restore(preset_id, order_mode)
        self._pending_auto_maker_manual_cancel = False
        if not self.engine.close_hedge(preset_id, mode, order_mode):
            self._clear_pending_auto_trade_state()

    def _capture_auto_maker_restore(
        self, preset_id: str, order_mode: str
    ) -> list[tuple[str, str]]:
        """记录 Maker 自动委托前已勾选的自动项；市价/白银不参与恢复。"""
        if auto_trade_lane(preset_id, order_mode) != "maker":
            return []
        strip = self.gold_actions if preset_id == "xau" else self.silver_actions
        auto = strip.auto_trade_settings
        out: list[tuple[str, str]] = []
        for action in ("open", "close"):
            for mode in (HedgeMode.CONTRACTION.value, HedgeMode.EXPANSION.value):
                checkbox = (
                    auto.open_checkbox("maker", mode)
                    if action == "open"
                    else auto.close_checkbox("maker", mode)
                )
                if checkbox is not None and checkbox.isChecked():
                    out.append((action, mode))
        return out

    def _restore_auto_maker_checkboxes(
        self, preset_id: str, snapshot: list[tuple[str, str]]
    ) -> int:
        """按自动撤单前的快照恢复 Maker 自动勾选，并同步配置。"""
        if not snapshot:
            return 0
        strip = self.gold_actions if preset_id == "xau" else self.silver_actions
        auto = strip.auto_trade_settings
        restored = 0
        for action, mode in snapshot:
            checkbox = (
                auto.open_checkbox("maker", mode)
                if action == "open"
                else auto.close_checkbox("maker", mode)
            )
            if checkbox is None or checkbox.isChecked():
                continue
            checkbox.blockSignals(True)
            checkbox.setChecked(True)
            checkbox.blockSignals(False)
            restored += 1
        if restored:
            self._sync_programmatic_auto_trade_change()
        return restored

    def _clear_pending_auto_trade_state(self) -> None:
        self._pending_auto_trade = None
        self._pending_auto_maker_restore = []
        self._pending_auto_maker_manual_cancel = False

    @staticmethod
    def _maker_auto_cancelled_without_fill(result) -> bool:
        if result.success or result.partial:
            return False
        for leg in getattr(result, "legs", []) or []:
            if getattr(leg, "platform", "") != "BA":
                continue
            msg = str(getattr(leg, "message", "") or "")
            filled = float(getattr(leg, "filled_quantity", 0.0) or 0.0)
            if filled > 1e-9:
                continue
            cancel_words = (
                "已撤单",
                "撤单",
                "已取消",
                "取消",
                "CANCELED",
                "CANCELLED",
                "EXPIRED",
                "timeout",
                "Timeout",
            )
            no_fill_words = ("未成交", "无成交", "0成交", "未完全成交")
            if any(word in msg for word in cancel_words) and (
                any(word in msg for word in no_fill_words) or "Maker" in msg
            ):
                return True
        return False

    def _disable_auto_open(
        self,
        preset_id: str,
        mode: str,
        order_mode: str,
        outcome: str = "success",
        *,
        log_cancel: bool = True,
        keep_checked: bool = False,
    ) -> None:
        from app.core.auto_trade import _reset_lane_open_timers

        strip = self.gold_actions if preset_id == "xau" else self.silver_actions
        auto = strip.auto_trade_settings
        is_market = order_mode == GoldOrderMode.MARKET.value
        lane = auto_trade_lane(preset_id, order_mode)
        checkbox = auto.open_checkbox(lane, mode)
        if checkbox is not None and not keep_checked:
            checkbox.blockSignals(True)
            checkbox.setChecked(False)
            checkbox.blockSignals(False)
        auto.apply_position_lock(mode)
        _reset_lane_open_timers(self._auto_trade_state, preset_id, lane)
        self.config = self._merge_config()
        # 处于交易收尾热路径：异步落盘，避免每次（尤其 Maker 未成交重试）同步加密写盘卡顿 UI。
        save_config_async(self.config)
        sym = "黄金" if preset_id == "xau" else "白银"
        mlabel = "收缩" if mode == HedgeMode.CONTRACTION.value else "扩张"
        lane_label = "市价" if is_market else "Maker"
        if outcome == "success":
            status = "已成功"
        elif outcome == "partial":
            status = "部分成功"
        else:
            status = "未成功"
        if log_cancel:
            self._append_log(
                LogLevel.INFO,
                f"自动开仓{mlabel}({lane_label}){status}，已取消{sym}对应勾选，可手动重新开启",
            )

    def _disable_auto_close(
        self,
        preset_id: str,
        mode: str,
        order_mode: str,
        outcome: str = "success",
        *,
        log_cancel: bool = True,
        keep_checked: bool = False,
    ) -> None:
        from app.core.auto_trade import _reset_lane_close_timers

        strip = self.gold_actions if preset_id == "xau" else self.silver_actions
        auto = strip.auto_trade_settings
        is_market = order_mode == GoldOrderMode.MARKET.value
        lane = auto_trade_lane(preset_id, order_mode)
        checkbox = auto.close_checkbox(lane, mode)
        if checkbox is not None and not keep_checked:
            checkbox.blockSignals(True)
            checkbox.setChecked(False)
            checkbox.blockSignals(False)
        _reset_lane_close_timers(self._auto_trade_state, preset_id, lane)
        self.config = self._merge_config()
        # 处于交易收尾热路径：异步落盘，避免每次（尤其 Maker 未成交重试）同步加密写盘卡顿 UI。
        save_config_async(self.config)
        sym = "黄金" if preset_id == "xau" else "白银"
        mlabel = "收缩" if mode == HedgeMode.CONTRACTION.value else "扩张"
        lane_label = "市价" if is_market else "Maker"
        if outcome == "success":
            status = "已平一手"
        elif outcome == "partial":
            status = "部分成功"
        else:
            status = "未成功"
        if log_cancel:
            self._append_log(
                LogLevel.INFO,
                f"自动平仓{mlabel}({lane_label}){status}，已取消{sym}对应勾选，可手动重新开启",
            )

    def _on_manual_cancel_orders(self) -> None:
        """手动撤销全部未成交委托：仅在监控运行时有效，后台执行避免阻塞 UI。"""
        if not self.engine.is_running:
            self._append_log(LogLevel.INFO, "未开始监控，无法撤单")
            return
        if (
            self._pending_auto_trade is not None
            and auto_trade_lane(self._pending_auto_trade[1], self._pending_auto_trade[3]) == "maker"
        ):
            self._pending_auto_maker_manual_cancel = True
        self._append_log(LogLevel.INFO, "手动撤单 · 正在撤销全部委托…")
        self.engine.cancel_all_open_orders()

    def _schedule_auto_maker_timeout(self, preset_id: str, orders: list) -> None:
        pending = self._pending_auto_trade
        if pending is None:
            return
        if pending[1] != preset_id or auto_trade_lane(pending[1], pending[3]) != "maker":
            return
        self._commit_auto_trade_inputs(preset_id)
        self.config = self._merge_config()
        self.engine.sync_config(self.config)
        timeout_sec = max(1.0, float(self.config.ba_maker_timeout_sec))
        for order in orders:
            order_id = str(getattr(order, "order_id", "") or "")
            if not order_id:
                continue
            key = (preset_id, order_id)
            if key in self._auto_maker_timeout_tokens:
                continue
            self._auto_maker_timeout_seq += 1
            token = self._auto_maker_timeout_seq
            self._auto_maker_timeout_tokens[key] = token
            QTimer.singleShot(
                int(round(timeout_sec * 1000)),
                lambda p=preset_id, oid=order_id, t=token: self._auto_cancel_maker_order_if_still_pending(
                    p, oid, t
                ),
            )

    def _auto_cancel_maker_order_if_still_pending(
        self, preset_id: str, order_id: str, token: int
    ) -> None:
        key = (preset_id, order_id)
        if self._auto_maker_timeout_tokens.get(key) != token:
            return
        if key not in self._current_ba_open_order_keys:
            self._auto_maker_timeout_tokens.pop(key, None)
            return
        pending = self._pending_auto_trade
        if (
            pending is None
            or pending[1] != preset_id
            or auto_trade_lane(pending[1], pending[3]) != "maker"
            or self._pending_auto_maker_manual_cancel
        ):
            return
        timeout_sec = max(1.0, float(self.config.ba_maker_timeout_sec))
        sym = "黄金" if preset_id == "xau" else "白银"
        self._append_log(
            LogLevel.INFO,
            f"{sym}自动 Maker 委托等待 {timeout_sec:.0f}s 未成交，已自动撤单",
        )
        self.engine.cancel_all_open_orders(manual=False)

    def _on_open_orders_changed(self, symbols) -> None:
        """委托单集合变化：点亮/熄灭各品种委托灯，并联动禁用 Maker 自动开仓。"""
        from app.core.symbols import preset_for_ba_symbol

        pending = {preset_for_ba_symbol(s) for s in symbols}
        changed = False
        for preset_id, strip in (("xau", self.gold_actions), ("xag", self.silver_actions)):
            auto = strip.auto_trade_settings
            before = auto.snapshot_lock_state()
            auto.set_pending_order(preset_id in pending)
            changed = changed or auto.snapshot_lock_state() != before
        if changed:
            self._sync_programmatic_auto_trade_change()
            self._request_auto_trade_reevaluate()

    def _sync_auto_trade_locks(self, positions) -> None:
        changed = False
        for preset_id, strip in (("xau", self.gold_actions), ("xag", self.silver_actions)):
            mode = detect_hedge_mode(preset_id, positions)
            auto = strip.auto_trade_settings
            before = auto.snapshot_lock_state()
            auto.apply_position_lock(mode)
            if auto.snapshot_lock_state() != before:
                changed = True
        if changed:
            self._sync_programmatic_auto_trade_change()
            self._request_auto_trade_reevaluate()

    def _maybe_auto_trade(self, update) -> None:
        # 自动下单未经运营后台开通时，禁止任何自动开/平仓评估（防止隐藏后仍按旧配置触发）
        if not self.gold_actions.auto_trade_available:
            return
        # 热路径不再每 tick 合并/下发配置；改由控件改动即时同步 + _cfg_sync_timer 低频兜底。
        cfg = self.config
        now = time.time()
        single = self.config.layout_mode == LayoutMode.SINGLE.value
        preset_ids: tuple[str, ...] = ("xau", "xag")
        if single:
            preset_ids = (self.config.single_symbol_preset,)

        if not self.engine.is_running:
            reason = diagnose_auto_trade_block(
                cfg, update.spreads, self.engine.positions, preset_ids=preset_ids, engine_running=False
            )
            if reason:
                self._auto_trade_hint(reason)
            return

        # 上一笔自动交易尚未收尾时，本 tick 不再评估/触发，避免「已满足→已跳过」每秒刷屏。
        if self.engine.is_trading or self._pending_auto_trade is not None:
            return

        # 跳过仍处于「Maker 未成交撤单」重试冷却中的品种，给 UI 留出可操作窗口。
        if self._auto_maker_retry_cooldown_until:
            preset_ids = tuple(
                pid
                for pid in preset_ids
                if now >= self._auto_maker_retry_cooldown_until.get(pid, 0.0)
            )
            if not preset_ids:
                return

        progress = collect_auto_close_progress(
            cfg,
            update.spreads,
            self.engine.positions,
            now,
            self._auto_trade_state,
            preset_ids=preset_ids,
        )
        if progress is None:
            progress = collect_auto_trade_progress(
                cfg,
                update.spreads,
                self.engine.positions,
                now,
                self._auto_trade_state,
                preset_ids=preset_ids,
            )
        self._update_auto_trade_progress(progress)

        closes = evaluate_auto_closes(
            cfg,
            update.spreads,
            self.engine.positions,
            now,
            self._auto_trade_state,
            preset_ids=preset_ids,
        )
        if closes:
            for strip in (self.gold_actions, self.silver_actions):
                strip.auto_trade_settings.set_status("")
            for preset_id, mode, order_mode, message in closes:
                self._append_log(LogLevel.INFO, message)
                self._execute_auto_close(preset_id, mode, order_mode)
            return

        orders = evaluate_auto_trades(
            cfg,
            update.spreads,
            self.engine.positions,
            now,
            self._auto_trade_state,
            preset_ids=preset_ids,
        )
        if orders:
            for strip in (self.gold_actions, self.silver_actions):
                strip.auto_trade_settings.set_status("")
        for preset_id, mode, order_mode, message in orders:
            self._append_log(LogLevel.INFO, message)
            self._execute_auto_open(preset_id, mode, order_mode)
            return

        if progress is None:
            reason = diagnose_auto_trade_block(
                cfg,
                update.spreads,
                self.engine.positions,
                preset_ids=preset_ids,
                engine_running=True,
            )
            if reason:
                self._auto_trade_hint(reason)

    def _mode_label(self) -> str:
        labels = {
            ConnectionMode.DEMO.value: "演示模式",
            ConnectionMode.LIVE_BOTH.value: "实盘双端",
            ConnectionMode.LIVE_BA.value: "仅 BA 实盘",
            ConnectionMode.LIVE_MT5.value: "仅 Exness 实盘",
        }
        return labels.get(self.config.connection_mode, "未知")

    def _on_save(self) -> None:
        self.config = self._merge_config()
        save_config(self.config)
        self.engine.update_config(self.config)
        self._append_log(LogLevel.INFO, "参数已保存")
        self.status_bar.showMessage("参数已保存")

    def _on_start(self) -> None:
        if self._monitor_start_pending:
            return
        self.config = self._merge_config()
        save_config(self.config)
        self.engine.update_config(self.config)
        if LICENSE_REQUIRED and self.license_service:
            self._monitor_start_pending = True
            self.start_btn.setEnabled(False)
            self.status_bar.showMessage("正在校验授权与 BA/EX 账号状态…")
            self._append_log(LogLevel.INFO, "正在校验授权与 BA/EX 账号状态…")
            mode = self.config.connection_mode
            threading.Thread(
                target=self._check_monitor_start_license,
                args=(mode,),
                daemon=True,
                name="monitor-license-check",
            ).start()
            return
        self._start_monitor_after_license()

    def _check_monitor_start_license(self, mode: str) -> None:
        """后台校验授权与平台账号状态，避免网络异常时卡住主线程。"""
        if not self.license_service:
            self._monitor_start_checked.emit(True, "")
            return
        try:
            self.license_service.ensure_approved_for_trade(mode)
        except LicenseError as exc:
            self._monitor_start_checked.emit(False, str(exc))
            return
        except Exception as exc:  # noqa: BLE001
            self._monitor_start_checked.emit(False, f"授权校验异常：{exc}")
            return
        self._monitor_start_checked.emit(True, "")

    def _on_monitor_start_checked(self, ok: bool, message: str) -> None:
        self._monitor_start_pending = False
        if not ok:
            self.start_btn.setEnabled(True)
            self._append_log(LogLevel.ERROR, f"启用监控被拒绝：{message}")
            self.status_bar.showMessage(f"启用监控被拒绝：{message}", 8000)
            return
        self._start_monitor_after_license()

    def _start_monitor_after_license(self) -> None:
        """授权校验通过后在主线程启动 Qt 定时器和连接器。"""
        self.engine.start()
        self._sync_monitor_buttons()
        self._sync_ba_refresh_timers()
        self._refresh_status_badges()
        self._refresh_order_book()
        self.status_bar.showMessage(f"监控运行中 · {self._mode_label()}")

    def _on_stop(self) -> None:
        self.engine.stop()
        self._sync_monitor_buttons()
        self._sync_ba_refresh_timers()
        self._refresh_status_badges()
        self.status_bar.showMessage("监控已停止")

    def _on_alert_settings_changed(self) -> None:
        self.config = self._merge_config()
        self.engine.config = self.config
        sender = self.sender()
        if isinstance(sender, QCheckBox) and not sender.isChecked():
            self.engine.alerts.stop()
        if not self.config.any_alert_sound_enabled():
            self.engine.alerts.stop()
            self.status_bar.showMessage("声音告警已关闭")
            return
        self.engine.reevaluate_alerts()

    def _on_refresh_positions(self) -> None:
        self.engine.refresh_positions(force_full=True)
        self._append_log(LogLevel.INFO, "已刷新持仓、委托与盈亏")

    def _on_open_orders(self, orders) -> None:
        """委托单刷新：更新各品种委托明细行，并同步 Maker 委托指示灯。"""
        from app.core.license.telemetry import build_open_orders_summary
        from app.core.symbols import find_preset

        deduped_orders = []
        seen_ba_orders: set[tuple[str, str]] = set()
        for order in orders:
            order_id = str(getattr(order, "order_id", "") or "")
            platform = getattr(order, "platform", "")
            symbol = getattr(order, "symbol", "")
            if platform == "BA" and order_id:
                key = (symbol, order_id)
                if key in seen_ba_orders:
                    continue
                seen_ba_orders.add(key)
            deduped_orders.append(order)
        orders = deduped_orders

        self.gold_actions.update_open_orders(orders)
        self.silver_actions.update_open_orders(orders)
        changed = False
        current_ba_keys: set[tuple[str, str]] = set()
        for preset_id, strip in (("xau", self.gold_actions), ("xag", self.silver_actions)):
            preset = find_preset(preset_id)
            ba_pending_by_id = {}
            for o in orders:
                if (
                    o.platform == "BA"
                    and o.symbol == preset.symbol_ba
                    and o.remaining_quantity > 0
                ):
                    ba_pending_by_id[str(o.order_id)] = o
            ba_pending = list(ba_pending_by_id.values())
            for order_id in ba_pending_by_id:
                current_ba_keys.add((preset_id, order_id))
            pending_qty = sum(o.remaining_quantity for o in ba_pending)
            auto = strip.auto_trade_settings
            before = auto.snapshot_lock_state()
            auto.set_pending_order(bool(ba_pending), pending_qty)
            changed = changed or auto.snapshot_lock_state() != before
            self._announce_auto_maker_order_accepted(preset_id, ba_pending)
            self._schedule_auto_maker_timeout(preset_id, ba_pending)
        self._current_ba_open_order_keys = current_ba_keys
        for key in list(self._auto_maker_timeout_tokens):
            if key not in current_ba_keys:
                self._auto_maker_timeout_tokens.pop(key, None)
        if changed:
            self._sync_programmatic_auto_trade_change()
            self._request_auto_trade_reevaluate()

        summary = build_open_orders_summary(orders)
        if summary != self._last_open_orders_log:
            self._last_open_orders_log = summary
            # 委托明细属调试信息（UI 已有委托灯/剩余量指示），降到 DEBUG 避免刷屏。
            if orders:
                self._append_log(LogLevel.DEBUG, f"委托同步 · {summary}")
            else:
                self._append_log(LogLevel.DEBUG, "委托同步 · 当前无挂单")

    def _on_license_status_changed(self, _status: str, _message: str) -> None:
        self._refresh_license_expires_label()

    def _refresh_license_expires_label(self) -> None:
        from app.core.license.format import format_license_expires_label

        if self.license_service is not None:
            text = format_license_expires_label(self.license_service.client.state.expires_at)
        else:
            text = format_license_expires_label("")
        self.license_expires_lbl.setText(text)
        self.license_expires_lbl.setVisible(bool(text))

    def _open_profit_calculator(self) -> None:
        dlg = self._profit_calculator_dialog
        if dlg is None:
            dlg = ProfitCalculatorDialog(
                self,
                engine=self.engine,
                trade_recorded_signal=self.engine.trade_recorded,
            )
            dlg.setWindowModality(Qt.WindowModality.NonModal)
            self._profit_calculator_dialog = dlg
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()
        if dlg is not None:
            dlg.refresh_soon()

    def _on_positions(self, positions, summary) -> None:
        self.gold_actions.update_positions(positions, summary, self.config)
        self.silver_actions.update_positions(positions, summary, self.config)
        self._sync_auto_trade_locks(positions)
        ba_q = self.engine.ba_quotes
        mt5_q = self.engine.mt5_quotes
        cfg = self.config
        self.gold_actions.update_pnl(positions, ba_q, mt5_q, cfg)
        self.silver_actions.update_pnl(positions, ba_q, mt5_q, cfg)
        for preset_id, dlg in list(self._trade_dialogs.items()):
            if dlg.isVisible():
                mode = detect_hedge_mode(preset_id, positions)
                dlg.set_active_mode(mode)
        self._refresh_trade_dialog_pnl()
        pending = self._pending_status_preset
        if pending:
            self._pending_status_preset = None
            self.status_bar.showMessage(self._format_position_status(pending), 12000)

    def _on_trade_started(self, action: str, preset_id: str, order_mode: str) -> None:
        from app.core.order_mode import order_mode_log_label

        self._last_trade_preset_id = preset_id
        label = "开仓" if action == "open" else "平仓"
        sym = "黄金" if preset_id == "xau" else "白银"
        om = order_mode_log_label(preset_id, order_mode)
        self.gold_actions.set_trade_buttons_enabled(False)
        self.silver_actions.set_trade_buttons_enabled(False)
        for dlg in self._trade_dialogs.values():
            if dlg.isVisible():
                dlg.set_actions_enabled(False)
        self.status_bar.showMessage(f"正在{sym}{label} · {om}...")
        # 自动交易已在触发时打印了「自动开/平仓 · …」表头，这里只为手动交易补一行，避免重复。
        if self._pending_auto_trade is None:
            self._append_log(LogLevel.TRADE, f"正在{sym}{label} · {om}")

    def _format_position_status(self, preset_id: str) -> str:
        strip = self.gold_actions if preset_id == "xau" else self.silver_actions
        return strip.position_status.text()

    def _on_trade_finished(self, result) -> None:
        self.gold_actions.set_trade_buttons_enabled(True)
        self.silver_actions.set_trade_buttons_enabled(True)
        for dlg in self._trade_dialogs.values():
            if dlg.isVisible():
                dlg.set_actions_enabled(True)
        pending = self._pending_auto_trade
        restore_snapshot = list(self._pending_auto_maker_restore)
        manual_auto_cancel = self._pending_auto_maker_manual_cancel
        self._auto_maker_timeout_tokens.clear()
        is_auto = pending is not None
        restored_auto_checkbox = False
        if is_auto and pending:
            action_p, preset_id_p, mode, order_mode = pending
            outcome = (
                "partial"
                if result.partial
                else "failed"
                if not result.success
                else "success"
            )
            # 仅「Maker 自动委托因超时未成交被自动撤单（非手动撤单）」才恢复之前勾选；
            # 成交/部分成交/手动撤单/市价通道一律取消勾选，需人工重新授权。
            should_restore = (
                auto_trade_lane(preset_id_p, order_mode) == "maker"
                and bool(restore_snapshot)
                and not manual_auto_cancel
                and self._maker_auto_cancelled_without_fill(result)
            )
            if action_p == "open":
                self._disable_auto_open(
                    preset_id_p,
                    mode,
                    order_mode,
                    outcome,
                    log_cancel=not should_restore,
                    keep_checked=should_restore,
                )
            elif action_p == "close":
                self._disable_auto_close(
                    preset_id_p,
                    mode,
                    order_mode,
                    outcome,
                    log_cancel=not should_restore,
                    keep_checked=should_restore,
                )
            if should_restore:
                restored_auto_checkbox = (
                    self._restore_auto_maker_checkboxes(preset_id_p, restore_snapshot) > 0
                )
                if restored_auto_checkbox:
                    sym = "黄金" if preset_id_p == "xau" else "白银"
                    self._append_log(
                        LogLevel.INFO,
                        f"自动 Maker 委托未成交已自动撤单，已恢复{sym}之前的自动勾选",
                    )
                    # 进入重试冷却：避免每个行情 tick 立即重挂，按钮长期置灰、UI 卡顿。
                    # 冷却结束后由后续行情 tick 自然重评估再挂 Maker。
                    self._auto_maker_retry_cooldown_until[preset_id_p] = (
                        time.time() + AUTO_MAKER_RETRY_COOLDOWN_SEC
                    )
            self._clear_pending_auto_trade_state()
        self._manual_trade_notify = False
        preset_id = getattr(self, "_last_trade_preset_id", "xau")
        if result.partial:
            self.engine.refresh_positions(force_full=True)
            self.status_bar.showMessage(result.message, 10000)
            if is_auto:
                # 自动下单不弹模态窗口，仅日志+状态栏，防止阻塞 UI / 连环弹窗
                note = "自动下单已恢复勾选" if restored_auto_checkbox else "自动下单已取消勾选"
                self._append_log(LogLevel.ERROR, f"部分成交：{result.message}（{note}）")
            else:
                box = QMessageBox(QMessageBox.Icon.Warning, "部分成交", result.message, parent=self)
                box.addButton("确定", QMessageBox.ButtonRole.AcceptRole)
                box.exec()
        elif not result.success:
            self.engine.refresh_positions(force_full=True)
            self.status_bar.showMessage(result.message, 10000)
            if is_auto:
                note = "自动下单已恢复勾选" if restored_auto_checkbox else "自动下单已取消勾选"
                self._append_log(LogLevel.ERROR, f"交易失败：{result.message}（{note}）")
            else:
                box = QMessageBox(QMessageBox.Icon.Critical, "交易失败", result.message, parent=self)
                box.addButton("确定", QMessageBox.ButtonRole.AcceptRole)
                box.exec()
        else:
            self._pending_status_preset = preset_id
            self.engine.refresh_positions(force_full=True)
            self.status_bar.showMessage(result.message, 5000)
            if is_auto:
                # 自动开仓/平仓成功后均已自动取消勾选，语音提醒用户需人工重新授权
                # （平仓同样是下单，须播报）
                auto_action = pending[0] if pending else "open"
                self._announce_auto_cancel(auto_action)

    def _announce_auto_maker_order_accepted(self, preset_id: str, orders: list) -> None:
        pending = self._pending_auto_trade
        if pending is None:
            return
        if pending[1] != preset_id or auto_trade_lane(pending[1], pending[3]) != "maker":
            return
        for order in orders:
            order_id = str(getattr(order, "order_id", "") or "")
            if not order_id:
                continue
            key = (preset_id, order_id)
            if key in self._announced_auto_maker_orders:
                continue
            self._announced_auto_maker_orders.add(key)
            self._say_trade_voice("委托成功", timeout_ms=4000)
            return

    def _announce_auto_cancel(self, action: str = "open") -> None:
        """语音播报「开仓成功 / 平仓成功」。

        优先级：爆仓告警 > 语音播报 > 点差预警。
        - 正在响爆仓告警时让位，不播报；
        - 正在响点差预警时语音优先，播报期间静音点差，播完恢复。
        """
        text = "平仓成功" if action == "close" else "开仓成功"
        self._say_trade_voice(text)

    def _say_trade_voice(self, text: str, *, timeout_ms: int = 8000) -> None:
        alerts = getattr(self.engine, "alerts", None)
        if alerts is not None and alerts.is_liq_ringing():
            return
        if alerts is not None:
            alerts.begin_voice()
            self._voice.say(text, on_finished=alerts.end_voice)
            # 兜底：万一播放完成回调丢失，超时后强制解除占用，避免点差被永久静音
            QTimer.singleShot(timeout_ms, alerts.end_voice)
        else:
            self._voice.say(text)

    def _on_market(self, update) -> None:
        if self._ui_bootstrapping:
            return
        risk = update.risk
        self.gold_actions.update_risk(risk.xau_ba_liq, risk.xau_mt5_liq)
        self.silver_actions.update_risk(risk.xag_ba_liq, risk.xag_mt5_liq)
        positions = self.engine.positions
        ba_q = self.engine.ba_quotes
        mt5_q = self.engine.mt5_quotes
        cfg = self.config
        self.gold_actions.update_pnl(positions, ba_q, mt5_q, cfg)
        self.silver_actions.update_pnl(positions, ba_q, mt5_q, cfg)
        self._refresh_trade_dialog_pnl()
        self.gold_actions.update_spread(update.spreads.get("xau"))
        self.silver_actions.update_spread(update.spreads.get("xag"))
        xau = update.spreads.get("xau")
        xag = update.spreads.get("xag")
        from app.core.symbols import find_preset

        xau_ba = update.ba_quotes.get(find_preset("xau").symbol_ba)
        xag_ba = update.ba_quotes.get(find_preset("xag").symbol_ba)
        self.gold_panel.update_ba_bid(
            xau.ba_bid if xau else (xau_ba.bid if xau_ba and xau_ba.bid > 0 else None)
        )
        self.silver_panel.update_ba_bid(
            xag.ba_bid if xag else (xag_ba.bid if xag_ba and xag_ba.bid > 0 else None)
        )
        self._maybe_auto_trade(update)
        # 订单簿不再随行情 tick 重绘；由 order_book_updated 信号置脏 + _book_timer 合并刷新

    def _on_alert(self, message: str) -> None:
        self.status_bar.showMessage(f"⚠ 告警：{message}")

    def _on_connection(self, platform: str, state: str) -> None:
        if self._ui_bootstrapping:
            return
        self._refresh_status_badges()

    def _refresh_status_badges(self) -> None:
        """合并连接状态、延迟与 BA 行情连接方式到平台徽标。"""
        from app.core.network_status import HIGH_LATENCY_MS

        status = NetworkStatus.from_engine(self.engine, self.engine.is_running)
        self._last_network = status
        ws_mode = (
            self.engine.binance.ws_mode if not self._ui_bootstrapping else "off"
        )

        def _slow(live: bool, conn_state: str, ms: float | None) -> bool:
            if not live:
                return False
            if conn_state == "connecting":
                return True
            return ms is not None and ms >= HIGH_LATENCY_MS

        self.ba_row.update_status(
            conn_state=status.ba_state,
            live=status.ba_live,
            running=status.running,
            latency_ms=status.ba_ms,
            slow=_slow(status.ba_live, status.ba_state, status.ba_ms),
            ws_mode=ws_mode,
        )
        self.mt5_row.update_status(
            conn_state=status.mt5_state,
            live=status.mt5_live,
            running=status.running,
            latency_ms=status.mt5_ms,
            slow=_slow(status.mt5_live, status.mt5_state, status.mt5_ms),
        )

    def _on_account_updated(self, snap) -> None:
        """收到账户资金快照：刷新对应平台的余额/保证金徽标。"""
        if snap is None:
            return
        if snap.platform == "BA":
            self._last_ba_account = snap
            self.ba_row.set_snapshot(snap)
        elif snap.platform == "MT5":
            self._last_mt5_account = snap
            self.mt5_row.set_snapshot(snap)

    def _on_ba_transfer(self) -> None:
        """打开 BA 现货↔合约划转对话框。"""
        if not self.config.use_live_ba:
            QMessageBox.information(
                self, "无法划转", "币安当前为模拟/未实盘连接，无法进行资金划转。"
            )
            return
        spot_balance = None
        futures_available = None
        snap = getattr(self, "_last_ba_account", None)
        if snap is not None and snap.is_live:
            spot_balance = snap.cash_balance
            futures_available = snap.free_margin
        from app.core.symbols import WATCHED_PRESETS, find_preset

        symbol_options = []
        for pid in WATCHED_PRESETS:
            preset = find_preset(pid)
            symbol_options.append((f"{preset.label}（{preset.symbol_ba}）", preset.symbol_ba))
        dlg = BalanceTransferDialog(
            self.engine.binance.transfer_spot_futures,
            position_margin_fn=self.engine.binance.change_position_margin,
            symbol_options=symbol_options,
            spot_balance=spot_balance,
            futures_available=futures_available,
            parent=self,
        )
        dlg.exec()

    def _on_ex_transfer(self) -> None:
        """EX/MT5 无法通过 API 划转资金，引导用户到 Exness 官网操作。"""
        ret = QMessageBox.question(
            self,
            "Exness 资金划转",
            "MT5 接口不支持程序化资金划转。\n是否打开 Exness 官网进行充值/划转？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if ret == QMessageBox.StandardButton.Yes:
            QDesktopServices.openUrl(QUrl("https://my.exness.com/"))

    def _on_network_status(self, status: NetworkStatus) -> None:
        self._last_network = status
        self._refresh_status_badges()
        self._enforce_latency_auto_trade_guard(status)

    def _enforce_latency_auto_trade_guard(self, status: NetworkStatus) -> None:
        """网络延迟超过设定阈值（或断网）时，自动取消所有已勾选的自动下单。

        仅取消已勾选的，不会替用户勾选；阈值 auto_trade_max_latency_ms<=0 表示关闭该保护。
        """
        if not status.running:
            return
        limit = float(getattr(self.config, "auto_trade_max_latency_ms", 0.0) or 0.0)
        if limit <= 0:
            return
        over: list[tuple[str, float]] = []
        if status.ba_live and status.ba_ms is not None and status.ba_ms > limit:
            over.append(("BA", status.ba_ms))
        if status.mt5_live and status.mt5_ms is not None and status.mt5_ms > limit:
            over.append(("Ex", status.mt5_ms))
        offline = status.level == "offline"
        if not over and not offline:
            return
        total = 0
        for strip in (self.gold_actions, self.silver_actions):
            total += strip.auto_trade_settings.disable_checked_auto_trades()
        if total <= 0:
            return
        # 与人工取消一致：清掉计时/冷却，持久化并同步引擎
        self._auto_trade_state.since.clear()
        self._auto_trade_state.close_since.clear()
        self.config = self._merge_config()
        save_config(self.config)
        self.engine.sync_config(self.config)
        if offline:
            detail = "网络断开"
        else:
            detail = "、".join(f"{name} {ms:.0f}ms" for name, ms in over)
        self._append_log(
            LogLevel.TRADE,
            f"网络延迟过高（阈值 {limit:.0f}ms：{detail}），已自动取消 {total} 个自动下单勾选",
        )

    def _on_ws_state(self, mode: str) -> None:
        if self._ui_bootstrapping:
            return
        self._refresh_status_badges()

    def _refresh_order_book(self) -> None:
        from app.core.models import OrderBook
        from app.core.symbols import resolve_symbols

        books = self.engine.ba_order_books
        ba_xau, _, _ = resolve_symbols("xau", self.config.symbol_ba, self.config.symbol_mt5)
        ba_xag, _, _ = resolve_symbols("xag", self.config.symbol_ba, self.config.symbol_mt5)
        single = self.config.layout_mode == LayoutMode.SINGLE.value
        if single:
            if self.config.single_symbol_preset == "xau":
                self.gold_panel.update_book(books.get(ba_xau, OrderBook()))
            else:
                self.silver_panel.update_book(books.get(ba_xag, OrderBook()))
            return
        self.gold_panel.update_book(books.get(ba_xau, OrderBook()))
        self.silver_panel.update_book(books.get(ba_xag, OrderBook()))

    def closeEvent(self, event) -> None:
        self.engine.stop()
        super().closeEvent(event)
