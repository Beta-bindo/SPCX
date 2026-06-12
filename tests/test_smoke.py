"""Headless UI smoke tests for XAU Assistant."""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from unittest.mock import patch

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from app.core.config import load_config, save_config
from app.core.models import AppConfig, ConnectionMode, HedgeMode
from app.main_window import MainWindow
from app.widgets.config_panel import ConfigPanel
from app.widgets.trade_confirm_dialog import TradeConfirmDialog


def _accept_open_contraction(dialog: TradeConfirmDialog):
    dialog._apply_action("开仓", HedgeMode.CONTRACTION.value)
    return TradeConfirmDialog.DialogCode.Accepted


def _accept_close_contraction(dialog: TradeConfirmDialog):
    dialog._apply_action("平仓", HedgeMode.CONTRACTION.value)
    return TradeConfirmDialog.DialogCode.Accepted


class TestRunner:
    def __init__(self):
        self.errors: list[str] = []
        self.market_count = 0
        self.pnl_count = 0
        self.log_count = 0

    def run(self) -> int:
        app = QApplication(sys.argv)
        window = MainWindow()
        window.show()

        engine = window.engine
        engine.market_updated.connect(self._on_market)
        engine.positions_updated.connect(self._on_pnl)
        engine.log_message.connect(self._on_log)

        QTimer.singleShot(300, lambda: self._step_start(window))
        QTimer.singleShot(2500, lambda: self._step_refresh(window))
        QTimer.singleShot(4000, lambda: self._step_save(window))
        QTimer.singleShot(5500, lambda: self._step_mode(window))
        QTimer.singleShot(7000, lambda: self._step_stop(window))
        QTimer.singleShot(8500, lambda: self._step_trade_demo(window))
        QTimer.singleShot(16000, lambda: self._step_trade_silver(window))
        QTimer.singleShot(20000, lambda: self._finish(app, window))
        return app.exec()

    def _on_market(self, update) -> None:
        if "xau" in update.spreads and "xag" in update.spreads:
            self.market_count += 1
        elif self.market_count > 0:
            pass

    def _on_pnl(self, positions, summary) -> None:
        self.pnl_count += 1

    def _on_log(self, _msg: str) -> None:
        self.log_count += 1

    def _step_start(self, window: MainWindow) -> None:
        if not window.engine.is_running:
            window.start_btn.click()

    def _step_refresh(self, window: MainWindow) -> None:
        window._on_refresh_positions()

    def _step_save(self, window: MainWindow) -> None:
        window.config.ba_api_key = "test-key"
        window.gold_actions.alert_settings.spread_max.setValue(99)
        window.save_btn.click()
        loaded = load_config()
        if loaded.ba_api_key != "test-key":
            self.errors.append("config save/load failed")
        if loaded.xau_spread_alert_max != 99:
            self.errors.append("spread settings not saved via save button")

    def _step_mode(self, window: MainWindow) -> None:
        panel = ConfigPanel(embedded=False)
        panel.load_config(window.config)
        idx = panel.connection_mode.findData(ConnectionMode.LIVE_BOTH.value)
        panel.connection_mode.setCurrentIndex(idx)
        if not panel.ba_api_key.isEnabled():
            self.errors.append("live mode should enable BA fields")

    def _step_stop(self, window: MainWindow) -> None:
        window.stop_btn.click()

    def _step_trade_demo(self, window: MainWindow) -> None:
        window.start_btn.click()
        with patch.object(TradeConfirmDialog, "show", _accept_open_contraction):
            window.gold_actions.trade_entry_btn.click()
        QTimer.singleShot(1500, lambda: window.engine.refresh_positions())

    def _step_trade_silver(self, window: MainWindow) -> None:
        with patch.object(TradeConfirmDialog, "show", _accept_open_contraction):
            window.silver_actions.trade_entry_btn.click()
        QTimer.singleShot(1500, lambda: window.engine.refresh_positions())
        with patch.object(TradeConfirmDialog, "show", _accept_close_contraction):
            window.silver_actions.trade_entry_btn.click()

    def _finish(self, app: QApplication, window: MainWindow) -> None:
        if self.market_count < 3:
            self.errors.append(f"expected market updates, got {self.market_count}")
        if self.pnl_count < 2:
            self.errors.append(f"expected pnl updates, got {self.pnl_count}")
        if self.log_count < 2:
            self.errors.append(f"expected log messages, got {self.log_count}")
        if window.gold_actions.spread_ba.text() == "--":
            self.errors.append("gold BA mid not displayed")
        if window.gold_panel.table.rowCount() < 10:
            self.errors.append("gold order book not populated")
        if window.silver_panel.table.rowCount() < 10:
            self.errors.append("silver order book not populated")

        save_config(AppConfig(connection_mode=ConnectionMode.DEMO.value))

        if self.errors:
            print("SMOKE TEST FAILED:")
            for err in self.errors:
                print(f"  - {err}")
            window.close()
            app.exit(1)
        else:
            print("SMOKE TEST PASSED")
            print(f"  market updates: {self.market_count}")
            print(f"  pnl updates: {self.pnl_count}")
            window.close()
            app.exit(0)


if __name__ == "__main__":
    raise SystemExit(TestRunner().run())
