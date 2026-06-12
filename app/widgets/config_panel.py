"""连接与参数配置面板：品种、连接模式、账户、手续费、代理等；可内嵌或弹窗布局。"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
    QFileDialog,
)

from app.core.models import AppConfig, ConnectionMode, BA_REFRESH_INTERVAL_OPTIONS
from app.core.app_log import LOG_LEVEL_OPTIONS, normalize_log_level
from app.core.mt5_terminal import find_mt5_terminal
from app.core.symbols import SYMBOL_PRESETS
from app.widgets.common import SectionCard
from app.widgets.symbol_alert_settings import ClickToEditDoubleSpinBox, ClickToEditSpinBox

# 连接模式下拉项：演示 / 双实盘 / 仅 BA / 仅 MT5
CONNECTION_OPTIONS = [
    (ConnectionMode.DEMO.value, "演示模式（模拟行情）"),
    (ConnectionMode.LIVE_BOTH.value, "实盘 · BA + MT5"),
    (ConnectionMode.LIVE_BA.value, "实盘 · 仅 BA"),
    (ConnectionMode.LIVE_MT5.value, "实盘 · 仅 MT5"),
]


class ConfigPanel(QFrame):
    """配置面板。embedded=True 为主界面内嵌横排卡片，False 为设置弹窗竖排。"""

    def __init__(self, parent=None, *, embedded: bool = True):
        super().__init__(parent)
        self._embedded = embedded
        self.setObjectName("configPanel")
        if embedded:
            self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        else:
            self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        if embedded:
            header = QHBoxLayout()
            header.setContentsMargins(4, 0, 4, 10)
            title = QLabel("连接与参数")
            title.setObjectName("panelTitle")
            subtitle = QLabel("品种、连接模式、账户与手续费")
            subtitle.setObjectName("panelSubtitle")
            title_col = QVBoxLayout()
            title_col.setSpacing(2)
            title_col.addWidget(title)
            title_col.addWidget(subtitle)
            header.addLayout(title_col)
            header.addStretch()
            root.addLayout(header)

        self._build_fields()
        self._layout_cards(root)

        self.save_btn = QPushButton("保存参数")
        self.save_btn.setObjectName("ghostButton")
        self.start_btn = QPushButton("启用监控")
        self.start_btn.setObjectName("primaryButton")
        self.stop_btn = QPushButton("停止监控")
        self.stop_btn.setObjectName("dangerButton")

        if embedded:
            action_bar = QFrame()
            action_bar.setObjectName("actionBar")
            actions = QHBoxLayout(action_bar)
            actions.setContentsMargins(12, 10, 12, 10)
            actions.addStretch()
            actions.addWidget(self.save_btn)
            actions.addWidget(self.start_btn)
            actions.addWidget(self.stop_btn)
            root.addWidget(action_bar)

        self.symbol_preset.currentIndexChanged.connect(self._on_preset_changed)
        self.connection_mode.currentIndexChanged.connect(self._sync_live_fields)
        self.use_proxy.toggled.connect(self._sync_live_fields)
        self._on_preset_changed()
        self._sync_live_fields()

    def _build_fields(self) -> None:
        """构建四张卡片（交易品种 / Binance / Exness / 手续费与网络）及其字段。"""
        compact = not self._embedded

        self.trade_card = SectionCard("交易品种", badge="必选", accent="#eab308", compact=compact)
        self.symbol_preset = QComboBox()
        for preset in SYMBOL_PRESETS:
            self.symbol_preset.addItem(preset.label, preset.id)
        self.connection_mode = QComboBox()
        for value, label in CONNECTION_OPTIONS:
            self.connection_mode.addItem(label, value)
        self.symbol_ba = QLineEdit("XAUUSDT")
        self.symbol_mt5 = QLineEdit("XAUUSD")
        self.ba_leverage = ClickToEditSpinBox()
        self.ba_leverage.setRange(1, 125)
        self.ba_leverage.setValue(20)
        self.ba_leverage.setSuffix(" x")
        self.ba_leverage.setButtonSymbols(ClickToEditSpinBox.ButtonSymbols.NoButtons)
        self.mt5_leverage = ClickToEditSpinBox()
        self.mt5_leverage.setRange(1, 2000)
        self.mt5_leverage.setValue(100)
        self.mt5_leverage.setSuffix(" x")
        self.mt5_leverage.setButtonSymbols(ClickToEditSpinBox.ButtonSymbols.NoButtons)
        self.sync_leverage_on_trade = QCheckBox("下单时同步杠杆到平台")
        self.sync_leverage_on_trade.setObjectName("settingsCheck")
        self.log_level = QComboBox()
        for value, label in LOG_LEVEL_OPTIONS:
            self.log_level.addItem(label, value)
        if self._embedded:
            self.trade_card.add_grid_fields(
                [
                    ("品种", self.symbol_preset, ""),
                    ("连接模式", self.connection_mode, ""),
                    ("BA 交易对", self.symbol_ba, "Binance U 本位合约"),
                    ("MT5 品种", self.symbol_mt5, "经纪商报价代码"),
                    ("BA 杠杆", self.ba_leverage, "勾选同步后写入 Binance"),
                    ("Ex 杠杆", self.mt5_leverage, "Exness 账户杠杆；连接后自动读取"),
                    ("", self.sync_leverage_on_trade, "仅 BA：勾选后每次开仓写入杠杆"),
                    ("运行日志", self.log_level, "精简模式仅显示交易与错误"),
                ],
                columns=2,
            )
        else:
            dialog_label_w = 88
            self.trade_card.add_inline_field(
                "连接模式", self.connection_mode, label_width=dialog_label_w
            )
            self.trade_card.add_inline_field(
                "BA 杠杆",
                self.ba_leverage,
                "勾选同步后写入 Binance",
                label_width=dialog_label_w,
            )
            self.trade_card.add_inline_field(
                "Ex 杠杆",
                self.mt5_leverage,
                "账户杠杆；连接后自动读取",
                label_width=dialog_label_w,
            )
            self.trade_card.add_inline_field(
                "",
                self.sync_leverage_on_trade,
                "仅 BA：勾选后每次开仓写入",
                label_width=dialog_label_w,
            )
            self.trade_card.add_inline_field(
                "运行日志",
                self.log_level,
                "精简模式仅显示交易与错误",
                label_width=dialog_label_w,
            )

        self.ba_card = SectionCard("Binance (BA)", badge="交易所", accent="#f59e0b", compact=compact)
        self.ba_api_key = QLineEdit()
        self.ba_api_key.setPlaceholderText("API Key")
        self.ba_api_secret = QLineEdit()
        self.ba_api_secret.setEchoMode(QLineEdit.EchoMode.Password)
        self.ba_api_secret.setPlaceholderText("API Secret")
        self.ba_refresh_interval = QComboBox()
        for sec in BA_REFRESH_INTERVAL_OPTIONS:
            self.ba_refresh_interval.addItem(f"{sec:.1f} 秒", sec)
        self.ba_card.add_inline_field("API Key", self.ba_api_key, label_width=self._dialog_label_width())
        self.ba_card.add_inline_field(
            "API Secret", self.ba_api_secret, "仅本地保存", label_width=self._dialog_label_width()
        )
        self.ba_card.add_inline_field(
            "行情刷新",
            self.ba_refresh_interval,
            "订单簿轮询；≤0.8s 易触发限频，建议 ≥1.0s",
            label_width=self._dialog_label_width(),
        )

        self.mt5_card = SectionCard("Exness (MT5)", badge="经纪商", accent="#22c55e", compact=compact)
        self.mt5_login = ClickToEditSpinBox()
        self.mt5_login.setMaximum(999999999)
        self.mt5_login.setButtonSymbols(ClickToEditSpinBox.ButtonSymbols.NoButtons)
        self.mt5_password = QLineEdit()
        self.mt5_password.setEchoMode(QLineEdit.EchoMode.Password)
        self.mt5_server = QLineEdit()
        self.mt5_server.setPlaceholderText("Exness-MT5Real5")
        self.mt5_terminal_path = QLineEdit()
        self.mt5_terminal_path.setPlaceholderText("留空自动查找 terminal64.exe")
        self.mt5_browse_btn = QPushButton("浏览…")
        self.mt5_browse_btn.setObjectName("ghostButton")
        self.mt5_browse_btn.clicked.connect(self._browse_mt5_terminal)
        terminal_row = QWidget()
        terminal_layout = QHBoxLayout(terminal_row)
        terminal_layout.setContentsMargins(0, 0, 0, 0)
        terminal_layout.setSpacing(6)
        terminal_layout.addWidget(self.mt5_terminal_path, stretch=1)
        terminal_layout.addWidget(self.mt5_browse_btn)
        self.mt5_card.add_inline_field("账户", self.mt5_login, label_width=self._dialog_label_width())
        self.mt5_card.add_inline_field("密码", self.mt5_password, label_width=self._dialog_label_width())
        self.mt5_card.add_inline_field("服务器", self.mt5_server, label_width=self._dialog_label_width())
        self.mt5_card.add_inline_field(
            "终端路径",
            terminal_row,
            "Exness MT5 的 terminal64.exe",
            label_width=self._dialog_label_width(),
        )

        self.fee_card = SectionCard("手续费与网络", badge="估算", accent="#6366f1", compact=compact)
        self.ba_fee_rate = ClickToEditDoubleSpinBox()
        self.ba_fee_rate.setRange(0, 0.01)
        self.ba_fee_rate.setDecimals(4)
        self.ba_fee_rate.setSingleStep(0.0001)
        self.ba_fee_rate.setValue(0.0004)
        self.mt5_commission = ClickToEditDoubleSpinBox()
        self.mt5_commission.setRange(0, 100)
        self.mt5_commission.setDecimals(2)
        self.mt5_commission.setValue(0)
        self.mt5_spread_points = ClickToEditDoubleSpinBox()
        self.mt5_spread_points.setRange(0, 50)
        self.mt5_spread_points.setDecimals(2)
        self.mt5_spread_points.setValue(0.25)
        self.use_proxy = QCheckBox("启用 HTTP 代理")
        self.proxy_host = QLineEdit("127.0.0.1")
        self.proxy_port = ClickToEditSpinBox()
        self.proxy_port.setRange(1, 65535)
        self.proxy_port.setValue(7897)
        self.proxy_port.setButtonSymbols(ClickToEditSpinBox.ButtonSymbols.NoButtons)
        proxy_row = QHBoxLayout()
        proxy_row.setSpacing(8)
        proxy_row.addWidget(self.proxy_host, stretch=3)
        proxy_row.addWidget(QLabel(":"), stretch=0)
        proxy_row.addWidget(self.proxy_port, stretch=1)
        proxy_wrap = QWidget()
        proxy_wrap.setLayout(proxy_row)
        if self._embedded:
            self.fee_card.add_grid_fields(
                [
                    ("BA 费率", self.ba_fee_rate, "单边 taker"),
                    ("MT5 佣金/手", self.mt5_commission, "美元/手/边"),
                    ("MT5 点差(点)", self.mt5_spread_points, "估算开平仓成本"),
                ],
                columns=3,
            )
            self.fee_card.body.addWidget(self.use_proxy)
            self.fee_card.add_field("代理", proxy_wrap, "如 127.0.0.1:7897（Clash HTTP 端口）")
        else:
            dialog_label_w = self._dialog_label_width()
            self.fee_card.add_inline_field(
                "BA 费率", self.ba_fee_rate, "单边 taker", label_width=dialog_label_w
            )
            self.fee_card.add_inline_field(
                "MT5 佣金/手", self.mt5_commission, "美元/手/边", label_width=dialog_label_w
            )
            self.fee_card.add_inline_field(
                "MT5 点差(点)",
                self.mt5_spread_points,
                "估算开平仓成本",
                label_width=dialog_label_w,
            )
            self.fee_card.add_inline_field(
                "HTTP 代理", self.use_proxy, label_width=dialog_label_w
            )
            self.fee_card.add_inline_field(
                "代理",
                proxy_wrap,
                "如 127.0.0.1:7897（Clash HTTP 端口）",
                label_width=dialog_label_w,
            )

    def _dialog_label_width(self) -> int:
        return 88 if not self._embedded else 76

    def _layout_cards(self, root: QVBoxLayout) -> None:
        if self._embedded:
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QFrame.Shape.NoFrame)
            scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            scroll.setMinimumHeight(160)
            scroll_content = QWidget()
            scroll_layout = QHBoxLayout(scroll_content)
            scroll_layout.setContentsMargins(0, 0, 0, 0)
            scroll_layout.setSpacing(12)
            scroll_layout.addWidget(self.trade_card, stretch=1)
            scroll_layout.addWidget(self.ba_card, stretch=1)
            scroll_layout.addWidget(self.mt5_card, stretch=1)
            scroll_layout.addWidget(self.fee_card, stretch=1)
            scroll.setWidget(scroll_content)
            root.addWidget(scroll)
            return

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setObjectName("connectionSettingsScroll")

        stack = QWidget()
        stack.setObjectName("connectionSettingsStack")
        stack_layout = QVBoxLayout(stack)
        stack_layout.setContentsMargins(0, 0, 2, 0)
        stack_layout.setSpacing(8)
        for card in (self.trade_card, self.fee_card, self.ba_card, self.mt5_card):
            card.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
            stack_layout.addWidget(card)
        scroll.setWidget(stack)
        root.addWidget(scroll, 1)

    def _on_preset_changed(self) -> None:
        """切换品种预设：自定义时允许手填代码，否则自动填入预设代码并禁用编辑。"""
        preset_id = self.symbol_preset.currentData()
        custom = preset_id == "custom"
        self.symbol_ba.setEnabled(custom)
        self.symbol_mt5.setEnabled(custom)
        if not custom:
            idx = self.symbol_preset.currentIndex()
            preset = SYMBOL_PRESETS[idx]
            self.symbol_ba.setText(preset.symbol_ba)
            self.symbol_mt5.setText(preset.symbol_mt5)

    def _browse_mt5_terminal(self) -> None:
        """弹出文件选择，定位 MT5 的 terminal64.exe。"""
        start_dir = self.mt5_terminal_path.text().strip()
        if not start_dir:
            detected = find_mt5_terminal()
            start_dir = str(detected.parent) if detected else ""
        path, _ = QFileDialog.getOpenFileName(
            self,
            "选择 MetaTrader 5 终端",
            start_dir,
            "MT5 终端 (terminal64.exe);;所有文件 (*.*)",
        )
        if path:
            self.mt5_terminal_path.setText(path)

    def _sync_live_fields(self) -> None:
        """根据连接模式与代理开关，置灰/点亮相应卡片与输入框。"""
        mode = self.connection_mode.currentData()
        is_demo = mode == ConnectionMode.DEMO.value
        need_ba = mode in (ConnectionMode.LIVE_BOTH.value, ConnectionMode.LIVE_BA.value)
        need_mt5 = mode in (ConnectionMode.LIVE_BOTH.value, ConnectionMode.LIVE_MT5.value)

        for widget in (
            self.ba_api_key,
            self.ba_api_secret,
            self.mt5_login,
            self.mt5_password,
            self.mt5_server,
            self.mt5_terminal_path,
            self.mt5_browse_btn,
            self.ba_fee_rate,
            self.mt5_commission,
            self.mt5_spread_points,
            self.use_proxy,
        ):
            widget.setEnabled(True)
        proxy_on = self.use_proxy.isChecked()
        self.proxy_host.setEnabled(proxy_on)
        self.proxy_port.setEnabled(proxy_on)

        self._set_card_dimmed(self.ba_card, is_demo or not need_ba)
        self._set_card_dimmed(self.mt5_card, is_demo or not need_mt5)

    def _set_card_dimmed(self, card: SectionCard, dimmed: bool) -> None:
        card.setProperty("dimmed", "true" if dimmed else "false")
        card.style().unpolish(card)
        card.style().polish(card)

    def refresh_theme(self) -> None:
        self.ba_card.setStyleSheet("")
        self.mt5_card.setStyleSheet("")
        self._sync_live_fields()

    def load_config(self, config: AppConfig) -> None:
        """把配置回填到各控件。"""
        idx = self.symbol_preset.findData(config.symbol_preset)
        if idx >= 0:
            self.symbol_preset.setCurrentIndex(idx)
        idx = self.connection_mode.findData(config.connection_mode)
        if idx >= 0:
            self.connection_mode.setCurrentIndex(idx)
        self.ba_api_key.setText(config.ba_api_key)
        self.ba_api_secret.setText(config.ba_api_secret)
        self.symbol_ba.setText(config.symbol_ba)
        self.mt5_login.setValue(config.mt5_login)
        self.mt5_password.setText(config.mt5_password)
        self.mt5_server.setText(config.mt5_server)
        self.mt5_terminal_path.setText(config.mt5_terminal_path)
        self.symbol_mt5.setText(config.symbol_mt5)
        self.ba_fee_rate.setValue(config.ba_fee_rate)
        self.mt5_commission.setValue(config.mt5_commission_per_lot)
        self.mt5_spread_points.setValue(config.mt5_spread_points)
        self.ba_leverage.setValue(config.ba_leverage)
        self.mt5_leverage.setValue(config.mt5_leverage)
        self.sync_leverage_on_trade.setChecked(config.sync_leverage_on_trade)
        idx = self.ba_refresh_interval.findData(config.ba_refresh_interval_sec)
        if idx >= 0:
            self.ba_refresh_interval.setCurrentIndex(idx)
        log_idx = self.log_level.findData(normalize_log_level(config.log_level))
        if log_idx >= 0:
            self.log_level.setCurrentIndex(log_idx)
        self.use_proxy.setChecked(config.use_proxy)
        self.proxy_host.setText(config.proxy_host)
        self.proxy_port.setValue(config.proxy_port)
        self._on_preset_changed()
        self._sync_live_fields()

    def to_config(self) -> AppConfig:
        """从各控件收集生成一个新的 AppConfig（仅含本面板涉及的字段）。"""
        return AppConfig(
            ba_api_key=self.ba_api_key.text().strip(),
            ba_api_secret=self.ba_api_secret.text().strip(),
            proxy_host=self.proxy_host.text().strip() or "127.0.0.1",
            proxy_port=self.proxy_port.value(),
            use_proxy=self.use_proxy.isChecked(),
            mt5_login=self.mt5_login.value(),
            mt5_password=self.mt5_password.text(),
            mt5_server=self.mt5_server.text().strip(),
            mt5_terminal_path=self.mt5_terminal_path.text().strip(),
            connection_mode=self.connection_mode.currentData(),
            symbol_preset=self.symbol_preset.currentData(),
            symbol_ba=self.symbol_ba.text().strip() or "XAUUSDT",
            symbol_mt5=self.symbol_mt5.text().strip() or "XAUUSD",
            ba_fee_rate=self.ba_fee_rate.value(),
            mt5_commission_per_lot=self.mt5_commission.value(),
            mt5_spread_points=self.mt5_spread_points.value(),
            ba_leverage=self.ba_leverage.value(),
            mt5_leverage=self.mt5_leverage.value(),
            sync_leverage_on_trade=self.sync_leverage_on_trade.isChecked(),
            ba_refresh_interval_sec=float(self.ba_refresh_interval.currentData()),
            log_level=str(self.log_level.currentData()),
        )
