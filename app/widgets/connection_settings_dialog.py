"""连接与参数设置对话框：内嵌 ConfigPanel，保存时回写连接相关配置。"""

from __future__ import annotations

from PySide6.QtWidgets import QDialog, QDialogButtonBox, QVBoxLayout

from app.core.models import AppConfig
from app.widgets.config_panel import ConfigPanel


class ConnectionSettingsDialog(QDialog):
    """连接与参数：账号、品种、手续费、杠杆、代理等。"""

    def __init__(self, config: AppConfig, parent=None):
        super().__init__(parent)
        self.setObjectName("connectionSettingsDialog")
        self.setWindowTitle("设置 · 连接与参数")
        self.resize(400, 480)
        self.setMinimumSize(360, 320)
        self.setMaximumWidth(440)

        self._panel = ConfigPanel(embedded=False)
        self._panel.load_config(config)

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 8, 10, 6)
        root.setSpacing(6)
        root.addWidget(self._panel, 1)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        save_btn = buttons.button(QDialogButtonBox.StandardButton.Save)
        cancel_btn = buttons.button(QDialogButtonBox.StandardButton.Cancel)
        save_btn.setText("保存并关闭")
        cancel_btn.setText("取消")
        save_btn.setProperty("compact", True)
        cancel_btn.setProperty("compact", True)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def apply_connection_to(self, config: AppConfig) -> None:
        """把面板上的连接/参数项回写到给定 config（不动告警/自动交易等其他字段）。"""
        panel_cfg = self._panel.to_config()
        config.ba_api_key = panel_cfg.ba_api_key
        config.ba_api_secret = panel_cfg.ba_api_secret
        config.proxy_host = panel_cfg.proxy_host
        config.proxy_port = panel_cfg.proxy_port
        config.use_proxy = panel_cfg.use_proxy
        config.mt5_login = panel_cfg.mt5_login
        config.mt5_password = panel_cfg.mt5_password
        config.mt5_server = panel_cfg.mt5_server
        config.mt5_terminal_path = panel_cfg.mt5_terminal_path
        config.connection_mode = panel_cfg.connection_mode
        config.symbol_preset = panel_cfg.symbol_preset
        config.symbol_ba = panel_cfg.symbol_ba
        config.symbol_mt5 = panel_cfg.symbol_mt5
        config.ba_fee_rate = panel_cfg.ba_fee_rate
        config.mt5_commission_per_lot = panel_cfg.mt5_commission_per_lot
        config.mt5_spread_points = panel_cfg.mt5_spread_points
        config.ba_leverage = panel_cfg.ba_leverage
        config.mt5_leverage = panel_cfg.mt5_leverage
        config.sync_leverage_on_trade = panel_cfg.sync_leverage_on_trade
        config.ba_margin_type = panel_cfg.ba_margin_type
        config.ba_refresh_interval_sec = panel_cfg.ba_refresh_interval_sec
        config.auto_trade_max_latency_ms = panel_cfg.auto_trade_max_latency_ms
