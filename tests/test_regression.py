"""Regression tests for layout, spread colors, and position status."""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from app.core.config import load_config, save_config
from app.core.models import AppConfig, ConnectionMode, HedgeMode, LayoutMode
from app.core.theme import load_stylesheet
from app.main_window import MainWindow
from app.widgets.spread_value_label import SpreadValueLabel
from app.widgets.trade_confirm_dialog import TradeConfirmDialog


def _accept_open(dlg: TradeConfirmDialog):
    dlg._apply_action("开仓", HedgeMode.CONTRACTION.value)
    return TradeConfirmDialog.DialogCode.Accepted


def test_dual_trade_dialogs_can_coexist():
    app = QApplication.instance() or QApplication(sys.argv)
    window = MainWindow()
    window.config.layout_mode = LayoutMode.DUAL.value
    window._apply_layout_mode()
    window._open_trade_dialog("xau")
    window._open_trade_dialog("xag")
    gold = window._trade_dialogs.get("xau")
    silver = window._trade_dialogs.get("xag")
    assert gold is not None and silver is not None
    assert gold is not silver
    assert gold.isVisible() and silver.isVisible()
    window.close()
    print("  ✓ 黄金/SPCXUSDT对冲弹窗可同时存在")


def main() -> int:
    errors: list[str] = []
    app = QApplication(sys.argv)

    def check(cond: bool, msg: str) -> None:
        if not cond:
            errors.append(msg)

    # Spread tone: positive=hot, negative=cold
    for theme in ("light", "dark"):
        load_stylesheet(app, theme)
        sv = SpreadValueLabel()
        sv.set_spread(3.04)
        check(sv._main.property("spreadHot") == "true", f"{theme}: positive spread hot")
        check("color:" not in sv._main.styleSheet(), f"{theme}: spread uses QSS not inline color")
        sv.set_spread(-1.5)
        check(sv._main.property("spreadCold") == "true", f"{theme}: negative spread cold")

    cfg = load_config()
    cfg.layout_mode = LayoutMode.SINGLE.value
    cfg.single_symbol_preset = "xau"
    cfg.connection_mode = ConnectionMode.DEMO.value
    save_config(cfg)

    window = MainWindow()
    window.resize(1400, 880)
    window.show()
    QApplication.processEvents()
    time.sleep(0.4)
    QApplication.processEvents()

    sp = window._columns_splitter
    book = sp.widget(0)
    actions = sp.widget(1)
    check(book is window.gold_panel, "single xau: gold book left")
    check(actions is window.gold_actions, "single xau: gold actions column")
    gap = actions.geometry().x() - (book.geometry().x() + book.geometry().width())
    check(gap < 40, f"single mode layout gap {gap}px")

    if not window.engine.is_running:
        window.start_btn.click()
    deadline = time.time() + 5
    while time.time() < deadline:
        QApplication.processEvents()
        time.sleep(0.05)
    check(window.gold_actions.spread_value._main.text() != "--", "spread shown after start")

    with patch.object(TradeConfirmDialog, "show", _accept_open):
        window.gold_actions.trade_entry_btn.click()
    deadline = time.time() + 10
    while window.engine._trading and time.time() < deadline:
        QApplication.processEvents()
        time.sleep(0.05)
    window.engine.refresh_positions()
    QApplication.processEvents()
    time.sleep(0.2)
    QApplication.processEvents()

    status = window.gold_actions.position_status.text()
    check("收缩策略" in status, f"position status: {status}")
    check("持仓差价" in status, f"position spread in status: {status}")
    check(window.gold_actions.position_status.property("active") == "true", "position active")

    window.config.single_symbol_preset = "xag"
    window._apply_layout_mode()
    QApplication.processEvents()
    QTimer.singleShot(0, lambda: None)
    QApplication.processEvents()
    check(sp.widget(3) is window.silver_panel, "single xag: silver book left")
    check(sp.widget(4) is window.silver_actions, "single xag: silver actions column")

    window.layout_mode_btn.click()
    QApplication.processEvents()
    check(window.gold_panel.isVisible() and window.silver_panel.isVisible(), "dual both visible")
    check(sp.count() == 5, f"dual splitter cols {sp.count()}")
    check(sp.widget(0) is window.gold_panel, "dual gold book")
    check(sp.widget(1) is window.gold_actions, "dual gold actions")
    check(sp.widget(3) is window.silver_panel, "dual silver book")
    check(sp.widget(4) is window.silver_actions, "dual silver actions")

    window.layout_mode_btn.click()
    QApplication.processEvents()
    check(sp.count() == 5, f"single still uses five fixed columns {sp.count()}")

    window.gold_actions.refresh_positions_btn.click()
    QApplication.processEvents()

    save_config(AppConfig(connection_mode=ConnectionMode.DEMO.value))
    window.close()

    try:
        test_dual_trade_dialogs_can_coexist()
    except AssertionError as exc:
        errors.append(f"dual trade dialogs: {exc}")

    if errors:
        print("REGRESSION TEST FAILED:")
        for err in errors:
            print(f"  - {err}")
        return 1
    print("REGRESSION TEST PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
