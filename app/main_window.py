"""主窗口：组织三栏布局（黄金/中栏汇总/白银），连接 SpreadEngine 与各 UI 组件。

负责：行情/持仓/盈亏的展示刷新、手动与自动对冲下单的入口与回执、告警与连接状态、
主题与布局切换、授权门禁校验，以及配置的加载/保存。
"""

from __future__ import annotations

import threading
import time

from PySide6.QtCore import Qt, QTimer, QPoint
from PySide6.QtGui import QFontMetrics, QGuiApplication, QShowEvent
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
)
from app.core.license.client import LicenseError
from app.core.license.service import LicenseService
from app.core.build_config import LICENSE_REQUIRED
from app.core.app_log import LogLevel, should_log
from app.core.config import load_config, save_config, save_config_async
from app.core.models import AppConfig, ConnectionMode, GoldOrderMode, HedgeMode, LayoutMode
from app.core.network_status import NetworkStatus
from app.core.spread_engine import SpreadEngine
from app.core.theme import load_stylesheet, polish_widget, repolish_tree, ui_mono_font
from app.core.trading_service import detect_hedge_mode
from app.widgets.connection_settings_dialog import ConnectionSettingsDialog
from app.widgets.log_panel import LogPanel
from app.widgets.profit_calculator_dialog import ProfitCalculatorDialog
from app.widgets.spread_panel import SpreadPanel
from app.widgets.symbol_trade_panel import BOOK_PANEL_WIDTH, SymbolActionStrip, SymbolTradePanel
from app.widgets.trade_confirm_dialog import TradeConfirmDialog


class StatusBadge(QFrame):
    """平台连接状态徽标：彩色圆点 + "BA · 真实连接"之类文案。"""

    def __init__(self, name: str, parent=None):
        super().__init__(parent)
        self.setObjectName("statusBadge")
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 3, 8, 3)
        layout.setSpacing(6)
        self.dot = QFrame()
        self.dot.setFixedSize(8, 8)
        self.dot.setObjectName("statusDotDisconnected")
        self.label = QLabel(f"{name} · 未连接")
        self.label.setObjectName("statusText")
        metrics = QFontMetrics(self.label.font())
        self.label.setMinimumWidth(
            metrics.horizontalAdvance(f"{name} · 模拟数据")
        )
        layout.addWidget(self.dot)
        layout.addWidget(self.label)
        self._dot_name = "statusDotDisconnected"
        self._label_text = self.label.text()

    def set_state(self, name: str, state: str) -> None:
        """根据连接状态更新圆点颜色与文案。"""
        mapping = {
            "connected": ("statusDotConnected", "真实连接"),
            "simulated": ("statusDotSimulated", "模拟数据"),
            "connecting": ("statusDotDisconnected", "连接中"),
            "disconnected": ("statusDotDisconnected", "未连接"),
            "error": ("statusDotError", "异常"),
        }
        dot_name, text = mapping.get(state, mapping["disconnected"])
        label_text = f"{name} · {text}"
        if label_text != self._label_text:
            self.label.setText(label_text)
            self._label_text = label_text
        if dot_name != self._dot_name:
            self._dot_name = dot_name
            self.dot.setObjectName(dot_name)
            polish_widget(self.dot)


class WsStatusBadge(QFrame):
    """BA 行情连接方式徽标：WebSocket 实时推流 / REST 兜底（非管理后台）。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("statusBadge")
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 3, 8, 3)
        layout.setSpacing(6)
        self.dot = QFrame()
        self.dot.setFixedSize(8, 8)
        self.dot.setObjectName("statusDotDisconnected")
        self.label = QLabel("连接方式：关闭")
        self.label.setObjectName("statusText")
        metrics = QFontMetrics(self.label.font())
        self.label.setMinimumWidth(metrics.horizontalAdvance("连接方式：WebSocket"))
        layout.addWidget(self.dot)
        layout.addWidget(self.label)
        self._dot_name = "statusDotDisconnected"
        self._label_text = self.label.text()

    def set_mode(self, mode: str, *, live_ba: bool) -> None:
        if not live_ba:
            dot_name, text = "statusDotSimulated", "连接方式：模拟"
        else:
            mapping = {
                "streaming": ("statusDotConnected", "连接方式：WebSocket"),
                "rest": ("statusDotSlow", "连接方式：REST"),
                "connecting": ("statusDotDisconnected", "连接方式：WebSocket(连接中)"),
                "off": ("statusDotDisconnected", "连接方式：关闭"),
            }
            dot_name, text = mapping.get(mode, mapping["off"])
        if text != self._label_text:
            self.label.setText(text)
            self._label_text = text
        if dot_name != self._dot_name:
            self._dot_name = dot_name
            self.dot.setObjectName(dot_name)
            polish_widget(self.dot)


class NetworkStatusBadge(QFrame):
    """网络状态徽标：展示 BA/Ex 行情延迟，或离线/未启动等精简文案。"""

    _MS_SAMPLE = "9999ms"  # 数值列按四位数 ms 预留，避免 >999 时被裁切

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("networkStatusBadge")
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 5, 2)
        layout.setSpacing(4)
        self.dot = QFrame()
        self.dot.setFixedSize(8, 8)
        self.dot.setObjectName("statusDotDisconnected")
        layout.addWidget(self.dot, 0, Qt.AlignmentFlag.AlignVCenter)

        latency_font = ui_mono_font(point_size=18)
        metrics = QFontMetrics(latency_font)
        tag_w = metrics.horizontalAdvance("Ex")
        ms_w = metrics.horizontalAdvance(self._MS_SAMPLE)
        line_h = metrics.height()
        row_w = tag_w + 2 + ms_w

        text_col = QVBoxLayout()
        text_col.setContentsMargins(0, 0, 0, 0)
        text_col.setSpacing(0)

        self.ba_tag = QLabel("BA")
        self.ba_ms = QLabel("----")
        self.ex_tag = QLabel("Ex")
        self.ex_ms = QLabel("----")
        for tag, ms in ((self.ba_tag, self.ba_ms), (self.ex_tag, self.ex_ms)):
            tag.setObjectName("statusLatencyTag")
            ms.setObjectName("statusLatency")
            tag.setFont(latency_font)
            ms.setFont(latency_font)
            tag.setFixedSize(tag_w, line_h)
            ms.setFixedSize(ms_w, line_h)
            tag.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            ms.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            row = QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(2)
            row.addWidget(tag)
            row.addWidget(ms)
            text_col.addLayout(row)

        self._latency_wrap = QWidget()
        self._latency_wrap.setLayout(text_col)

        self._compact_label = QLabel("未启动")
        self._compact_label.setObjectName("statusLatencyCompact")
        compact_w = metrics.horizontalAdvance("断网")
        content_w = max(row_w, compact_w)
        self._latency_wrap.setFixedSize(content_w, line_h * 2)
        self._compact_label.setFixedSize(content_w, line_h * 2)
        self._compact_label.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )
        self._compact_label.setVisible(False)
        layout.addWidget(self._latency_wrap, 0, Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(self._compact_label, 0, Qt.AlignmentFlag.AlignVCenter)

        self.setFixedSize(4 + 8 + 4 + content_w + 5, line_h * 2 + 4)

        self._dot_name = "statusDotDisconnected"
        self._ba_ms_text = self.ba_ms.text()
        self._ex_ms_text = self.ex_ms.text()
        self._compact_text = ""

    def update_status(self, status: NetworkStatus) -> None:
        """刷新网络徽标：有延迟数据则双行显示，否则显示精简状态文案。"""
        mapping = {
            "ok": "statusDotConnected",
            "slow": "statusDotSlow",
            "offline": "statusDotError",
        }
        dot_name = mapping.get(status.level, "statusDotDisconnected")
        compact = status.compact_text
        if compact:
            if compact != self._compact_text:
                self._compact_label.setText(compact)
                self._compact_text = compact
            self._latency_wrap.setVisible(False)
            self._compact_label.setVisible(True)
        else:
            ba_ms = status.ba_ms_text()
            ex_ms = status.ex_ms_text()
            if ba_ms != self._ba_ms_text:
                self.ba_ms.setText(ba_ms)
                self._ba_ms_text = ba_ms
            if ex_ms != self._ex_ms_text:
                self.ex_ms.setText(ex_ms)
                self._ex_ms_text = ex_ms
            self._compact_label.setVisible(False)
            self._latency_wrap.setVisible(True)
        if dot_name != self._dot_name:
            self._dot_name = dot_name
            self.dot.setObjectName(dot_name)
            polish_widget(self.dot)


class MainWindow(QMainWindow):
    """应用主窗口：装配三栏 UI、引擎与各类信号，并承载交易/告警/配置交互。"""

    def __init__(
        self,
        license_service: LicenseService | None = None,
        *,
        demo_seed: bool = False,
        demo_seed_mixed: bool = False,
    ):
        super().__init__()
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
        self._manual_trade_notify = False
        self._pending_status_preset: str | None = None
        self._trade_dialogs: dict[str, TradeConfirmDialog] = {}
        self._monitor_buttons_on_header = True
        self._pending_demo_start = False
        self._demo_start_scheduled = False
        self._ui_bootstrapping = True
        self._last_open_orders_log = ""

        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(12, 10, 12, 8)
        root.setSpacing(8)

        root.addLayout(self._build_header())

        self._main_splitter = QSplitter(Qt.Orientation.Vertical)
        self._main_splitter.setObjectName("mainSplitter")
        self._main_splitter.setHandleWidth(8)
        self._main_splitter.setChildrenCollapsible(False)

        self._columns_splitter = QSplitter(Qt.Orientation.Horizontal)
        self._columns_splitter.setObjectName("columnsSplitter")
        self._columns_splitter.setHandleWidth(6)
        self._columns_splitter.setChildrenCollapsible(False)

        self.gold_panel = SymbolTradePanel("xau", "黄金 · 币安盘口")
        self.silver_panel = SymbolTradePanel("xag", "白银 · 币安盘口")
        self.gold_actions = SymbolActionStrip("xau")
        self.silver_actions = SymbolActionStrip("xag")
        for strip in (self.gold_actions, self.silver_actions):
            strip.setSizePolicy(
                QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred
            )
            strip.setMinimumWidth(140)
        # 中间「盈利·告警」面板已下线，仅保留对象用于"模拟/真实"行情徽标更新（不加入布局）
        self.spread_panel = SpreadPanel()
        self.spread_panel.set_action_strips(self.gold_actions, self.silver_actions)
        self.spread_panel.set_source_badge(self.source_badge)
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
        self._main_splitter.addWidget(self._columns_splitter)

        self.log_panel = LogPanel()
        self.log_panel.setMinimumHeight(48)
        self.log_panel.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self._main_splitter.addWidget(self.log_panel)

        self._columns_splitter.setMaximumHeight(16777215)
        self._main_splitter.setStretchFactor(0, 1)
        self._main_splitter.setStretchFactor(1, 0)
        self._main_splitter.setSizes([680, 220])
        root.addWidget(self._main_splitter, stretch=1)

        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_bar.showMessage("就绪 · 演示模式可直接启动")

        self._book_timer = QTimer(self)
        self._book_timer.timeout.connect(self._refresh_order_book)

        self._wire_signals()
        self.gold_actions.load_settings_from(self.config)
        self.silver_actions.load_settings_from(self.config)
        self._sync_theme_btn()
        self._apply_theme(self.config.theme)
        self._apply_layout_mode()

        self._sync_ba_refresh_timers()

        self._finalize_startup()
        self._sync_monitor_buttons()

    def present(self) -> None:
        """首屏展示：布局就绪后只 show 一次，避免透明窗/processEvents 造成连闪。"""
        self._sync_columns_sizes()
        self.show()
        QTimer.singleShot(400, self._finish_ui_bootstrap)

    def _finish_ui_bootstrap(self) -> None:
        self._ui_bootstrapping = False
        self._sync_ws_status()
        if self._pending_demo_start and not self._demo_start_scheduled:
            self._pending_demo_start = False
            self._demo_start_scheduled = True
            QTimer.singleShot(800, self._on_start)

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)

    def _finalize_startup(self) -> None:
        """在窗口显示前完成静态初始化，连接与行情放到显示后。"""
        if self._demo_seed or self._demo_seed_mixed:
            self._load_demo_seed_positions()
        if self.config.demo_mode and not self.engine.is_running:
            self._pending_demo_start = True
        else:
            from app.core.network_status import NetworkStatus

            self._on_network_status(
                NetworkStatus.from_engine(self.engine, self.engine.is_running)
            )
        if self._demo_seed or self._demo_seed_mixed:
            QTimer.singleShot(500, self._refresh_demo_seed_positions)
        self.setUpdatesEnabled(True)

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
        self.start_btn.setEnabled(not running)
        self.stop_btn.setEnabled(running)
        forbidden = Qt.CursorShape.ForbiddenCursor
        hand = Qt.CursorShape.PointingHandCursor
        self.start_btn.setCursor(hand if not running else forbidden)
        self.stop_btn.setCursor(hand if running else forbidden)

    def _build_header(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(6)
        row.setAlignment(Qt.AlignmentFlag.AlignVCenter)

        brand = QVBoxLayout()
        brand.setSpacing(0)
        title = QLabel(APP_NAME)
        title.setObjectName("appTitle")
        self.subtitle_label = QLabel("Binance × Exness 跨平台点差 · 黄金 / 白银")
        self.subtitle_label.setObjectName("appSubtitle")
        subtitle_metrics = QFontMetrics(self.subtitle_label.font())
        self.subtitle_label.setMinimumWidth(
            max(
                subtitle_metrics.horizontalAdvance(
                    "Binance × Exness 跨平台点差 · 黄金 / 白银"
                ),
                subtitle_metrics.horizontalAdvance(
                    "Binance × Exness · 单品种 · 黄金"
                ),
                subtitle_metrics.horizontalAdvance(
                    "Binance × Exness · 单品种 · 白银"
                ),
            )
        )
        brand.addWidget(title)
        brand.addWidget(self.subtitle_label)
        row.addLayout(brand)
        row.addSpacing(6)

        self.ba_status = StatusBadge("币安")
        self.mt5_status = StatusBadge("Exness")
        self.network_status = NetworkStatusBadge()
        self.ws_status = WsStatusBadge()
        status_col = QVBoxLayout()
        status_col.setContentsMargins(0, 0, 0, 0)
        status_col.setSpacing(2)
        status_col.addWidget(self.ba_status)
        status_col.addWidget(self.mt5_status)
        status_wrap = QWidget()
        status_wrap.setLayout(status_col)
        row.addWidget(status_wrap)
        row.addWidget(self.network_status)
        row.addWidget(self.ws_status)
        self.profit_btn = QPushButton("利润计算器")
        self._style_toolbar_btn(self.profit_btn)
        row.addWidget(self.profit_btn)
        self.source_badge = QLabel("模拟")
        self.source_badge.setObjectName("demoBadge")
        row.addWidget(self.source_badge)
        row.addStretch()

        self.layout_mode_btn = QPushButton("单品种")
        self._style_toolbar_btn(self.layout_mode_btn, checkable=True)
        self.layout_mode_btn.clicked.connect(self._on_layout_mode_toggled)
        row.addWidget(self.layout_mode_btn)

        self.symbol_switch_btn = QPushButton("🥈 切换白银")
        self._style_toolbar_btn(self.symbol_switch_btn)
        self.symbol_switch_btn.clicked.connect(self._on_symbol_switch)
        row.addWidget(self.symbol_switch_btn)

        self.theme_btn = QPushButton("浅色")
        self._style_toolbar_btn(self.theme_btn, checkable=True)
        self.theme_btn.clicked.connect(self._on_theme_toggled)
        row.addWidget(self.theme_btn)

        self.settings_btn = QPushButton("设置")
        self._style_toolbar_btn(self.settings_btn)
        self.settings_btn.clicked.connect(self._open_settings)
        row.addWidget(self.settings_btn)

        self.save_btn = QPushButton("保存")
        self._style_toolbar_btn(self.save_btn)
        self.save_btn.clicked.connect(self._on_save)
        row.addWidget(self.save_btn)

        self.start_btn = QPushButton("启用监控")
        self._style_toolbar_btn(self.start_btn, primary=True)
        self.start_btn.clicked.connect(self._on_start)
        row.addWidget(self.start_btn)

        self.stop_btn = QPushButton("停止监控")
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
        self.spread_panel.refresh_theme()
        self.gold_actions.refresh_theme()
        self.silver_actions.refresh_theme()
        self.gold_panel.refresh_theme()
        self.silver_panel.refresh_theme()
        repolish_tree(self)

    def _merge_config(self) -> AppConfig:
        self.gold_actions.apply_settings_to(self.config)
        self.silver_actions.apply_settings_to(self.config)
        return self.config

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
        ms = max(100, int(round(self.config.ba_refresh_interval_sec * 1000)))
        if timer.isActive():
            timer.setInterval(ms)
        else:
            timer.start(ms)

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
            self.spread_panel.apply_layout_mode(single)
            self._apply_column_visibility()

            if single:
                if preset == "xau":
                    self.symbol_switch_btn.setText("🥈 切换白银")
                    self.subtitle_label.setText(
                        "Binance × Exness · 单品种 · 黄金"
                    )
                else:
                    self.symbol_switch_btn.setText("🥇 切换黄金")
                    self.subtitle_label.setText(
                        "Binance × Exness · 单品种 · 白银"
                    )
            else:
                self.subtitle_label.setText(
                    "Binance × Exness 跨平台点差 · 黄金 / 白银"
                )

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

        self.engine.market_updated.connect(self._on_market)
        self.engine.connection_changed.connect(self._on_connection)
        self.engine.network_status_changed.connect(self._on_network_status)
        self.engine.binance.ws_state_changed.connect(self._on_ws_state)
        self.engine.binance.open_orders_changed.connect(self._on_open_orders_changed)
        self.engine.log_message.connect(self.log_panel.append)
        self.engine.positions_updated.connect(self._on_positions)
        self.engine.open_orders_updated.connect(self._on_open_orders)
        self.engine.trade_started.connect(self._on_trade_started)
        self.engine.trade_finished.connect(self._on_trade_finished)
        self.engine.alert_triggered.connect(self._on_alert)
        self.engine.trade_recorded.connect(self._on_trade_recorded)
        if self.license_service and LICENSE_REQUIRED:
            self.license_service.revoked.connect(self._on_license_revoked)
            self.license_service.set_telemetry_provider(self._license_telemetry)
        elif self.license_service:
            self.license_service.set_telemetry_provider(self._license_telemetry)

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
                self.license_service.ensure_approved_for_trade()
            else:
                self.license_service.ensure_approved()
            return True
        except LicenseError as exc:
            from app.widgets.license_gate import LicenseGateDialog, _verify_with_server

            box = QMessageBox(self)
            box.setIcon(QMessageBox.Icon.Warning)
            box.setWindowTitle("未授权")
            box.setText(f"{action}需要有效授权。\n{exc}")
            box.setInformativeText("请在授权窗口提交申请，或刷新审核状态。")
            open_btn = box.addButton("打开授权", QMessageBox.ButtonRole.AcceptRole)
            box.addButton("取消", QMessageBox.ButtonRole.RejectRole)
            box.exec()
            if box.clickedButton() is not open_btn:
                return False
            dlg = LicenseGateDialog(self.license_service, self)
            dlg.exec()
            if _verify_with_server(self.license_service):
                return True
            QMessageBox.warning(self, "未授权", "尚未通过授权，无法继续。")
            return False

    def _on_license_revoked(self, message: str) -> None:
        if self.engine.is_running:
            self.engine.stop()
        self._sync_monitor_buttons()
        self._append_log(LogLevel.INFO, f"授权已失效：{message}")
        QMessageBox.warning(
            self,
            "授权已失效",
            f"{message}\n\n监控已停止，请重新申请或联系管理员。",
        )

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

        def _drop_dialog(_=None, pid: str = preset_id, ref=dlg) -> None:
            if self._trade_dialogs.get(pid) is ref:
                self._trade_dialogs.pop(pid, None)

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
                self.engine.open_hedge(preset_id, mode, order_mode)
            else:
                self.engine.close_hedge(preset_id, mode, order_mode)
            if self.engine.is_trading:
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

    def _auto_trade_hint(self, message: str) -> None:
        now = time.time()
        if self._auto_trade_hint_last.get(message, 0.0) + 15.0 > now:
            return
        self._auto_trade_hint_last[message] = now
        self._append_log(LogLevel.INFO, message)
        for strip in (self.gold_actions, self.silver_actions):
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
        # 人工调整勾选/阈值视为显式意图：清掉冷却与计时，满足条件即可立即触发
        self._auto_trade_state.last_fire.clear()
        self._auto_trade_state.last_close_fire.clear()
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

    def _execute_auto_open(self, preset_id: str, mode: str, order_mode: str) -> None:
        if not self._ensure_license("自动下单", fast=True):
            return
        self.config = self._merge_config()
        self.engine.sync_config(self.config)
        if self.engine.is_trading:
            self._append_log(LogLevel.INFO, "自动下单：上一笔交易尚未完成，已跳过")
            return
        self._pending_auto_trade = ("open", preset_id, mode, order_mode)
        self.engine.open_hedge(preset_id, mode, order_mode)
        if self.engine.is_trading:
            save_config_async(self.config)

    def _execute_auto_close(self, preset_id: str, mode: str, order_mode: str) -> None:
        if not self._ensure_license("自动平仓", fast=True):
            return
        self.config = self._merge_config()
        self.engine.sync_config(self.config)
        if self.engine.is_trading:
            self._append_log(LogLevel.INFO, "自动平仓：上一笔交易尚未完成，已跳过")
            return
        self._pending_auto_trade = ("close", preset_id, mode, order_mode)
        self.engine.close_hedge(preset_id, mode, order_mode)

    def _disable_auto_open(self, preset_id: str, mode: str, order_mode: str) -> None:
        from app.core.auto_trade import _reset_lane_open_timers
        from app.core.order_mode import auto_trade_lane

        strip = self.gold_actions if preset_id == "xau" else self.silver_actions
        auto = strip.auto_trade_settings
        is_market = order_mode == GoldOrderMode.MARKET.value
        lane = auto_trade_lane(preset_id, order_mode)
        checkbox = auto.open_checkbox(lane, mode)
        if checkbox is not None:
            checkbox.blockSignals(True)
            checkbox.setChecked(False)
            checkbox.blockSignals(False)
        auto.apply_position_lock(mode)
        _reset_lane_open_timers(self._auto_trade_state, preset_id, lane)
        self.config = self._merge_config()
        save_config(self.config)
        sym = "黄金" if preset_id == "xau" else "白银"
        mlabel = "收缩" if mode == HedgeMode.CONTRACTION.value else "扩张"
        lane_label = "市价" if is_market else "Maker"
        self._append_log(
            LogLevel.INFO,
            f"自动开仓{mlabel}({lane_label})已成功，已取消{sym}对应勾选，可手动重新开启",
        )

    def _disable_auto_close(self, preset_id: str, mode: str, order_mode: str) -> None:
        from app.core.auto_trade import _reset_lane_close_timers
        from app.core.order_mode import auto_trade_lane

        strip = self.gold_actions if preset_id == "xau" else self.silver_actions
        auto = strip.auto_trade_settings
        is_market = order_mode == GoldOrderMode.MARKET.value
        lane = auto_trade_lane(preset_id, order_mode)
        checkbox = auto.close_checkbox(lane, mode)
        if checkbox is not None:
            checkbox.blockSignals(True)
            checkbox.setChecked(False)
            checkbox.blockSignals(False)
        _reset_lane_close_timers(self._auto_trade_state, preset_id, lane)
        self.config = self._merge_config()
        save_config(self.config)
        sym = "黄金" if preset_id == "xau" else "白银"
        mlabel = "收缩" if mode == HedgeMode.CONTRACTION.value else "扩张"
        lane_label = "市价" if is_market else "Maker"
        self._append_log(
            LogLevel.INFO,
            f"自动平仓{mlabel}({lane_label})已平一手，已取消{sym}对应勾选，可手动重新开启",
        )

    def _on_open_orders_changed(self, symbols) -> None:
        """委托单集合变化：点亮/熄灭各品种委托灯，并联动禁用 Maker 自动开仓。"""
        from app.core.symbols import preset_for_ba_symbol

        pending = {preset_for_ba_symbol(s) for s in symbols}
        for preset_id, strip in (("xau", self.gold_actions), ("xag", self.silver_actions)):
            strip.auto_trade_settings.set_pending_order(preset_id in pending)

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
            self.config = self._merge_config()
            save_config(self.config)

    def _maybe_auto_trade(self, update) -> None:
        cfg = self._merge_config()
        self.engine.sync_config(cfg)
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
        if not self._ensure_license("启用监控"):
            return
        self.config = self._merge_config()
        save_config(self.config)
        self.engine.update_config(self.config)
        self.engine.start()
        self._sync_monitor_buttons()
        self._sync_ba_refresh_timers()
        self._sync_ws_status()
        self._refresh_order_book()
        self.status_bar.showMessage(f"监控运行中 · {self._mode_label()}")

    def _on_stop(self) -> None:
        self.engine.stop()
        self._sync_monitor_buttons()
        self._sync_ws_status()
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
        self.engine.refresh_positions()
        self._append_log(LogLevel.INFO, "已刷新持仓、委托与盈亏")

    def _on_open_orders(self, orders) -> None:
        """委托单刷新：更新各品种委托明细行，并同步 Maker 委托指示灯。"""
        from app.core.license.telemetry import build_open_orders_summary
        from app.core.symbols import find_preset

        self.gold_actions.update_open_orders(orders)
        self.silver_actions.update_open_orders(orders)
        for preset_id, strip in (("xau", self.gold_actions), ("xag", self.silver_actions)):
            preset = find_preset(preset_id)
            has_ba_pending = any(
                o.platform == "BA"
                and o.symbol == preset.symbol_ba
                and o.remaining_quantity > 0
                for o in orders
            )
            strip.auto_trade_settings.set_pending_order(has_ba_pending)

        summary = build_open_orders_summary(orders)
        if summary != self._last_open_orders_log:
            self._last_open_orders_log = summary
            if orders:
                self._append_log(LogLevel.INFO, f"委托同步 · {summary}")
            else:
                self._append_log(LogLevel.INFO, "委托同步 · 当前无挂单")

    def _open_profit_calculator(self) -> None:
        dlg = ProfitCalculatorDialog(self)
        dlg.exec()

    def _on_positions(self, positions, summary) -> None:
        self.gold_actions.update_positions(positions, summary, self.config)
        self.silver_actions.update_positions(positions, summary, self.config)
        self._sync_auto_trade_locks(positions)
        ba_q = self.engine.ba_quotes
        mt5_q = self.engine.mt5_quotes
        cfg = self.config
        self.gold_actions.update_pnl(positions, ba_q, mt5_q, cfg)
        self.silver_actions.update_pnl(positions, ba_q, mt5_q, cfg)
        self.spread_panel.update_pnl(positions, ba_q, mt5_q, cfg)
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
        self._pending_auto_trade = None
        is_auto = pending is not None
        if pending and pending[0] == "open":
            # 自动开仓无论成功/部分/失败都取消勾选：需人工重新勾选授权，
            # 避免部分成交/失败后条件仍满足导致反复触发、不停弹窗。
            _, preset_id_p, mode, order_mode = pending
            self._disable_auto_open(preset_id_p, mode, order_mode)
        elif pending and pending[0] == "close":
            # 自动平仓与开仓对称：每次只平一手，平成功/部分/失败后均取消勾选，
            # 需人工重新勾选才平下一手，避免点差持续满足时连续平到光。
            _, preset_id_p, mode, order_mode = pending
            self._disable_auto_close(preset_id_p, mode, order_mode)
        self._manual_trade_notify = False
        preset_id = getattr(self, "_last_trade_preset_id", "xau")
        if result.partial:
            self.engine.refresh_positions()
            self.status_bar.showMessage(result.message, 10000)
            if is_auto:
                # 自动下单不弹模态窗口，仅日志+状态栏，防止阻塞 UI / 连环弹窗
                self._append_log(LogLevel.ERROR, f"部分成交：{result.message}（自动下单已取消勾选）")
            else:
                box = QMessageBox(QMessageBox.Icon.Warning, "部分成交", result.message, parent=self)
                box.addButton("确定", QMessageBox.ButtonRole.AcceptRole)
                box.exec()
        elif not result.success:
            self.engine.refresh_positions()
            self.status_bar.showMessage(result.message, 10000)
            if is_auto:
                self._append_log(LogLevel.ERROR, f"交易失败：{result.message}（自动下单已取消勾选）")
            else:
                box = QMessageBox(QMessageBox.Icon.Critical, "交易失败", result.message, parent=self)
                box.addButton("确定", QMessageBox.ButtonRole.AcceptRole)
                box.exec()
        else:
            self._pending_status_preset = preset_id
            self.engine.refresh_positions()
            self.status_bar.showMessage(result.message, 5000)

    def _on_market(self, update) -> None:
        if self._ui_bootstrapping:
            return
        self.spread_panel.update_market(update)
        risk = update.risk
        self.spread_panel.update_risk(
            risk.xau_ba_liq,
            risk.xau_mt5_liq,
            risk.xag_ba_liq,
            risk.xag_mt5_liq,
        )
        self.gold_actions.update_risk(risk.xau_ba_liq, risk.xau_mt5_liq)
        self.silver_actions.update_risk(risk.xag_ba_liq, risk.xag_mt5_liq)
        positions = self.engine.positions
        ba_q = self.engine.ba_quotes
        mt5_q = self.engine.mt5_quotes
        cfg = self.config
        self.gold_actions.update_pnl(positions, ba_q, mt5_q, cfg)
        self.silver_actions.update_pnl(positions, ba_q, mt5_q, cfg)
        self.spread_panel.update_pnl(positions, ba_q, mt5_q, cfg)
        self._refresh_trade_dialog_pnl()
        self.gold_actions.update_spread(update.spreads.get("xau"))
        self.silver_actions.update_spread(update.spreads.get("xag"))
        xau = update.spreads.get("xau")
        xag = update.spreads.get("xag")
        from app.core.symbols import find_preset

        xau_ba = update.ba_quotes.get(find_preset("xau").symbol_ba)
        xag_ba = update.ba_quotes.get(find_preset("xag").symbol_ba)
        self.gold_panel.update_ba_mid(
            xau.ba_mid if xau else (xau_ba.mid if xau_ba and xau_ba.bid > 0 else None)
        )
        self.silver_panel.update_ba_mid(
            xag.ba_mid if xag else (xag_ba.mid if xag_ba and xag_ba.bid > 0 else None)
        )
        self._maybe_auto_trade(update)
        self._refresh_order_book()

    def _on_alert(self, message: str) -> None:
        self.status_bar.showMessage(f"⚠ 告警：{message}")

    def _on_connection(self, platform: str, state: str) -> None:
        if self._ui_bootstrapping:
            return
        if platform == "BA":
            self.ba_status.set_state("币安", state)
        else:
            self.mt5_status.set_state("Exness", state)

    def _on_network_status(self, status: NetworkStatus) -> None:
        self.network_status.update_status(status)
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
        self._auto_trade_state.last_fire.clear()
        self._auto_trade_state.last_close_fire.clear()
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
        self.ws_status.set_mode(mode, live_ba=self.config.use_live_ba)

    def _sync_ws_status(self) -> None:
        self.ws_status.set_mode(
            self.engine.binance.ws_mode,
            live_ba=self.config.use_live_ba,
        )

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
