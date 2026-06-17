"""Auto trade strategy: contraction/expansion thresholds, instant trigger."""

import os
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from app.core.auto_trade import AutoTradeState, evaluate_auto_closes, evaluate_auto_trades
from app.core.config import load_config, save_config
from app.core.models import (
    AppConfig,
    ConnectionMode,
    GoldOrderMode,
    HedgeMode,
    MarketUpdate,
    OpenOrder,
    Position,
    Side,
    SpreadSnapshot,
)
from app.core.spread_engine import SpreadEngine
from app.core.trade_result import HedgeTradeResult, LegResult


def _cfg_contraction_only(threshold: float = 3.0) -> AppConfig:
    return AppConfig(
        xau_auto_contraction_enabled=True,
        xau_auto_contraction_threshold=threshold,
    )


def test_contraction_fires_immediately():
    state = AutoTradeState()
    cfg = _cfg_contraction_only()
    spreads = {"xau": SpreadSnapshot(preset_id="xau", mid_spread=3.5)}

    orders = evaluate_auto_trades(cfg, spreads, [], 100.0, state)
    assert len(orders) == 1
    assert orders[0][0] == "xau"
    assert orders[0][1] == HedgeMode.CONTRACTION.value
    assert orders[0][2] == "maker"
    assert "收缩" in orders[0][3]
    print("  ✓ 收缩：满足阈值即触发")


def test_contraction_resets_when_spread_drops():
    state = AutoTradeState()
    cfg = _cfg_contraction_only()
    ok = {"xau": SpreadSnapshot(preset_id="xau", mid_spread=3.5)}
    low = {"xau": SpreadSnapshot(preset_id="xau", mid_spread=2.0)}

    evaluate_auto_trades(cfg, ok, [], 100.0, state)
    assert not evaluate_auto_trades(cfg, low, [], 101.0, state)
    orders = evaluate_auto_trades(cfg, ok, [], 102.0, state)
    assert len(orders) == 1
    print("  ✓ 收缩：点差回落出迟滞带后再次满足仍立即触发")


def test_expansion_fires_when_spread_below_threshold():
    state = AutoTradeState()
    cfg = AppConfig(
        xau_auto_expansion_enabled=True,
        xau_auto_expansion_threshold=-3.0,
        xau_auto_trade_hold_sec=2.0,
    )
    spreads = {"xau": SpreadSnapshot(preset_id="xau", mid_spread=-3.5)}

    orders = evaluate_auto_trades(cfg, spreads, [], 10.0, state)
    assert len(orders) == 1
    assert orders[0][1] == HedgeMode.EXPANSION.value
    assert "扩张" in orders[0][3]
    print("  ✓ 扩张：点差 ≤ 阈值即触发")


def test_auto_open_maker_waits_timeout_not_spread_guard():
    engine = SpreadEngine(AppConfig(connection_mode=ConnectionMode.DEMO.value))
    engine._spreads["xau"] = SpreadSnapshot(
        preset_id="xau",
        ba_bid=100.0,
        ba_ask=100.1,
        mt5_bid=99.3,
        mt5_ask=99.4,
        mid_spread=0.806,
    )
    captured: dict[str, object] = {}

    def _fake_open_hedge(*_args, **kwargs):
        captured["spread_guard"] = kwargs.get("spread_guard")
        return HedgeTradeResult(action="open", success=False, message="fake")

    with patch("app.core.spread_engine.open_hedge", side_effect=_fake_open_hedge):
        engine._run_open(
            "xau",
            HedgeMode.EXPANSION.value,
            "maker",
            max_open_spread=0.9,
        )

    assert captured["spread_guard"] is None
    print("  ✓ 自动 Maker 开仓挂单后不因点差回落提前撤单")


def test_auto_open_spread_check_reports_executable_spread():
    engine = SpreadEngine(AppConfig(connection_mode=ConnectionMode.DEMO.value))
    engine._spreads["xau"] = SpreadSnapshot(
        preset_id="xau",
        ba_bid=4365.65,
        ba_ask=4365.66,
        mt5_bid=4363.465,
        mt5_ask=4363.90,
        mid_spread=2.185,
    )

    ok, message = engine._auto_open_spread_check(
        "xau",
        HedgeMode.CONTRACTION.value,
        min_open_spread=2.0,
        max_open_spread=None,
    )

    assert not ok
    assert "收缩开仓可执行点差 +1.750" in message
    assert "点差指数 +2.185" in message
    print("  ✓ 自动开仓拦截原因区分展示点差与可执行点差")


def test_disabled_strategy_never_fires():
    state = AutoTradeState()
    cfg = AppConfig(
        xau_auto_contraction_enabled=False,
        xau_auto_expansion_enabled=False,
    )
    spreads = {"xau": SpreadSnapshot(preset_id="xau", mid_spread=10.0)}
    assert not evaluate_auto_trades(cfg, spreads, [], 100.0, state)
    assert not evaluate_auto_trades(cfg, spreads, [], 200.0, state)
    print("  ✓ 未勾选策略不触发")


def test_fires_immediately_without_cooldown():
    state = AutoTradeState()
    cfg = _cfg_contraction_only()
    spreads = {"xau": SpreadSnapshot(preset_id="xau", mid_spread=4.0)}

    # 满足阈值即触发，无需等待；无触发冷却，连续多轮均会产出意图
    # （实际防重复由执行层 is_trading + 下单后自动取消勾选保证）
    assert len(evaluate_auto_trades(cfg, spreads, [], 0.0, state)) == 1
    assert len(evaluate_auto_trades(cfg, spreads, [], 0.1, state)) == 1
    assert len(evaluate_auto_trades(cfg, spreads, [], 1.0, state)) == 1
    print("  ✓ 满足即触发、无冷却")


def test_hysteresis_keeps_timer_near_threshold():
    state = AutoTradeState()
    cfg = _cfg_contraction_only(threshold=3.0)
    ok = {"xau": SpreadSnapshot(preset_id="xau", mid_spread=3.1)}
    dip = {"xau": SpreadSnapshot(preset_id="xau", mid_spread=2.98)}
    recover = {"xau": SpreadSnapshot(preset_id="xau", mid_spread=3.05)}

    evaluate_auto_trades(cfg, ok, [], 0.0, state)
    assert not evaluate_auto_trades(cfg, dip, [], 1.0, state)
    assert not evaluate_auto_trades(cfg, dip, [], 3.0, state)
    orders = evaluate_auto_trades(cfg, recover, [], 3.5, state)
    assert len(orders) == 1
    print("  ok hysteresis + threshold at fire")


def test_auto_close_contraction_fires_immediately():
    state = AutoTradeState()
    cfg = AppConfig(
        xau_auto_close_contraction_enabled=True,
        xau_auto_close_contraction_threshold=0.5,
    )
    spreads = {"xau": SpreadSnapshot(preset_id="xau", mid_spread=0.3)}
    positions = [
        Position(platform="BA", symbol="XAUUSDT", side=Side.SELL, quantity=500),
        Position(platform="MT5", symbol="XAUUSD", side=Side.BUY, quantity=1.0),
    ]

    orders = evaluate_auto_closes(cfg, spreads, positions, 10.0, state)
    assert len(orders) == 1
    assert orders[0][1] == HedgeMode.CONTRACTION.value
    assert "自动平仓" in orders[0][3] or "平仓" in orders[0][3]
    print("  ✓ 收缩自动平仓：点差满足即触发")


def _xau_contraction_positions() -> list[Position]:
    return [
        Position(platform="BA", symbol="XAUUSDT", side=Side.SELL, quantity=500),
        Position(platform="MT5", symbol="XAUUSD", side=Side.BUY, quantity=1.0),
    ]


def test_contraction_open_with_existing_contraction_position():
    state = AutoTradeState()
    cfg = _cfg_contraction_only()
    spreads = {"xau": SpreadSnapshot(preset_id="xau", mid_spread=3.5)}
    positions = _xau_contraction_positions()

    orders = evaluate_auto_trades(cfg, spreads, positions, 100.0, state)
    assert len(orders) == 1
    assert orders[0][1] == HedgeMode.CONTRACTION.value
    print("  ✓ 有收缩持仓仍可同方向自动开仓")


def test_expansion_blocked_with_contraction_position():
    state = AutoTradeState()
    cfg = AppConfig(
        xau_auto_expansion_enabled=True,
        xau_auto_expansion_threshold=-3.0,
        xau_auto_trade_hold_sec=2.0,
    )
    spreads = {"xau": SpreadSnapshot(preset_id="xau", mid_spread=-3.5)}
    positions = _xau_contraction_positions()

    evaluate_auto_trades(cfg, spreads, positions, 10.0, state)
    orders = evaluate_auto_trades(cfg, spreads, positions, 12.0, state)
    assert not orders
    print("  ✓ 有收缩持仓时不触发扩张开仓")


def test_other_symbol_unaffected_by_position():
    state = AutoTradeState()
    cfg = AppConfig(
        xag_auto_expansion_enabled=True,
        xag_auto_expansion_threshold=-3.0,
        xag_auto_trade_hold_sec=2.0,
    )
    spreads = {
        "xau": SpreadSnapshot(preset_id="xau", mid_spread=3.5),
        "xag": SpreadSnapshot(preset_id="xag", mid_spread=-3.5),
    }
    positions = _xau_contraction_positions()

    evaluate_auto_trades(cfg, spreads, positions, 10.0, state)
    orders = evaluate_auto_trades(cfg, spreads, positions, 12.0, state)
    assert len(orders) == 1
    assert orders[0][0] == "xag"
    assert orders[0][1] == HedgeMode.EXPANSION.value
    assert orders[0][2] == "market"
    print("  ✓ 另一品种持仓不影响自动下单")


def test_maker_fire_preserves_market_contraction_timer():
    state = AutoTradeState()
    cfg = AppConfig(
        xau_auto_contraction_enabled=True,
        xau_auto_contraction_threshold=3.0,
        xau_auto_market_contraction_enabled=True,
        xau_auto_market_contraction_threshold=5.0,
        xau_auto_trade_hold_sec=2.0,
    )
    spreads = {"xau": SpreadSnapshot(preset_id="xau", mid_spread=5.5)}
    state.since[("xau", HedgeMode.CONTRACTION.value, "market")] = 50.0
    state.since[("xau", HedgeMode.CONTRACTION.value, "maker")] = 51.0

    orders = evaluate_auto_trades(cfg, spreads, [], 53.0, state)
    assert len(orders) == 1
    assert orders[0][2] == "maker"
    assert state.since[("xau", HedgeMode.CONTRACTION.value, "market")] == 50.0
    print("  ✓ Maker 触发后不影响市价收缩计时")


def test_executable_spread_four_scenarios():
    """可执行点差按两腿真实成交方向计算（收缩/扩张 × 开/平 共 4 种）。"""
    snap = SpreadSnapshot(
        preset_id="xau",
        ba_bid=4343.8, ba_ask=4344.0,
        mt5_bid=4341.6, mt5_ask=4342.0,
        mid_spread=4343.8 - 4341.6,  # 点差指数 = 2.2
    )
    # 收缩开仓 = BA卖@bid + Ex买@ask = 4343.8 - 4342.0 = 1.8
    assert round(snap.executable_spread("open", "contraction"), 3) == 1.8
    # 扩张平仓 与收缩开仓同侧
    assert round(snap.executable_spread("close", "expansion"), 3) == 1.8
    # 扩张开仓 = BA买@ask + Ex卖@bid = 4344.0 - 4341.6 = 2.4
    assert round(snap.executable_spread("open", "expansion"), 3) == 2.4
    # 收缩平仓 与扩张开仓同侧
    assert round(snap.executable_spread("close", "contraction"), 3) == 2.4
    print("  ✓ 可执行点差：四种场景方向正确")


def test_executable_spread_falls_back_to_mid_when_no_quotes():
    """缺完整 bid/ask 时回退到点差指数（兼容仅设 mid_spread 的快照）。"""
    snap = SpreadSnapshot(preset_id="xau", mid_spread=3.5)
    assert snap.executable_spread("open", "contraction") == 3.5
    assert snap.executable_spread("close", "expansion") == 3.5
    print("  ✓ 可执行点差：缺报价回退 mid_spread")


def test_contraction_open_uses_executable_spread():
    """收缩开仓按可执行点差判断：点差指数=2.2 但可执行=1.8，阈值2.0 时不应触发。"""
    state = AutoTradeState()
    cfg = _cfg_contraction_only(threshold=2.0)
    snap = SpreadSnapshot(
        preset_id="xau",
        ba_bid=4343.8, ba_ask=4344.0,
        mt5_bid=4341.6, mt5_ask=4342.0,
        mid_spread=2.2,
    )
    # 可执行点差(1.8) < 阈值(2.0) → 不触发，尽管点差指数(2.2) ≥ 阈值
    assert not evaluate_auto_trades(cfg, {"xau": snap}, [], 10.0, state)

    # 阈值降到 1.8 → 可执行点差正好达标，触发
    cfg2 = _cfg_contraction_only(threshold=1.8)
    state2 = AutoTradeState()
    orders = evaluate_auto_trades(cfg2, {"xau": snap}, [], 10.0, state2)
    assert len(orders) == 1
    assert "1.800" in orders[0][3]  # 日志显示的是可执行点差
    print("  ✓ 收缩开仓按可执行点差判断与展示")


def test_market_auto_open_fires_with_market_order_mode():
    state = AutoTradeState()
    cfg = AppConfig(
        xau_auto_market_contraction_enabled=True,
        xau_auto_market_contraction_threshold=3.0,
        xau_auto_trade_hold_sec=2.0,
    )
    spreads = {"xau": SpreadSnapshot(preset_id="xau", mid_spread=3.5)}

    orders = evaluate_auto_trades(cfg, spreads, [], 10.0, state)
    assert len(orders) == 1
    assert orders[0][2] == "market"
    print("  ✓ 黄金市价自动开仓返回 market 模式")


def test_config_roundtrip():
    from app.main_window import MainWindow

    app = QApplication.instance() or QApplication(sys.argv)
    window = MainWindow()
    auto = window.gold_actions.auto_trade_settings
    auto.contraction_enabled.setChecked(True)
    auto.expansion_enabled.setChecked(True)
    auto.contraction_threshold.setValue(2.5)
    auto.expansion_threshold.setValue(-2.5)
    auto.close_contraction_enabled.setChecked(True)
    auto.close_expansion_threshold.setValue(-0.8)
    auto.market_contraction_enabled.setChecked(True)
    auto.market_expansion_threshold.setValue(-2.2)
    auto.market_close_expansion_enabled.setChecked(True)
    auto.market_close_expansion_threshold.setValue(-0.3)
    window.config = window._merge_config()
    save_config(window.config)
    loaded = load_config()
    assert loaded.xau_auto_contraction_enabled is True
    assert loaded.xau_auto_expansion_enabled is True
    assert loaded.xau_auto_contraction_threshold == 2.5
    assert loaded.xau_auto_expansion_threshold == -2.5
    assert loaded.xau_auto_trade_hold_sec == 0.0
    assert loaded.xau_auto_close_contraction_enabled is True
    assert loaded.xau_auto_close_expansion_threshold == -0.8
    assert loaded.xau_auto_market_contraction_enabled is True
    assert loaded.xau_auto_market_expansion_threshold == -2.2
    assert loaded.xau_auto_market_close_expansion_enabled is True
    assert loaded.xau_auto_market_close_expansion_threshold == -0.3
    save_config(AppConfig(connection_mode=ConnectionMode.DEMO.value))
    window.close()
    print("  ✓ 自动下单配置持久化")


def test_manual_cancel_button_emits_signal():
    app = QApplication.instance() or QApplication(sys.argv)
    from app.widgets.symbol_auto_trade_settings import SymbolAutoTradeSettings

    gold = SymbolAutoTradeSettings("xau")
    assert hasattr(gold, "cancel_orders_btn")
    assert gold.cancel_orders_btn.text() == "撤销委托"
    assert gold.cancel_orders_btn.objectName() == "primaryButton"

    fired: list[bool] = []
    gold.manual_cancel_requested.connect(lambda: fired.append(True))
    gold.cancel_orders_btn.click()
    assert fired == [True]

    silver = SymbolAutoTradeSettings("xag")
    assert not hasattr(silver, "cancel_orders_btn")
    print("  ✓ 撤销委托按钮：仅黄金面板存在且点击触发信号")


def test_pending_light_states():
    app = QApplication.instance() or QApplication(sys.argv)
    from app.widgets.symbol_auto_trade_settings import SymbolAutoTradeSettings

    gold = SymbolAutoTradeSettings("xau")
    light = gold.maker_pending_light

    # 初始：无委托，灰色
    assert light.text() == "○ 无委托"
    assert light.property("pendingActive") == "false"

    # 有委托并带数量：点亮，数字明确表示剩余委托量
    gold.set_pending_order(True, 500.0)
    assert light.text() == "● 有委托 · 剩余量 500"
    assert light.property("pendingActive") == "true"

    # 数量变化时实时刷新
    gold.set_pending_order(True, 1234.0)
    assert light.text() == "● 有委托 · 剩余量 1234"

    # 仅集合更新（不带数量）时沿用上次数量
    gold.set_pending_order(True)
    assert light.text() == "● 有委托 · 剩余量 1234"

    # 撤销后恢复无委托
    gold.set_pending_order(False)
    assert light.text() == "○ 无委托"
    assert light.property("pendingActive") == "false"
    print("  ✓ 委托指示灯：无委托/有委托+数量/清空 状态正确")


def test_pending_order_locks_all_maker_auto_checkboxes():
    app = QApplication.instance() or QApplication(sys.argv)
    from app.widgets.symbol_auto_trade_settings import SymbolAutoTradeSettings

    gold = SymbolAutoTradeSettings("xau")
    for cb in (
        gold.contraction_enabled,
        gold.expansion_enabled,
        gold.close_contraction_enabled,
        gold.close_expansion_enabled,
    ):
        cb.setChecked(True)

    gold.set_pending_order(True, 1.0)

    for cb in (
        gold.contraction_enabled,
        gold.expansion_enabled,
        gold.close_contraction_enabled,
        gold.close_expansion_enabled,
    ):
        assert not cb.isChecked()
        assert not cb.isEnabled()
    for spin in (
        gold.contraction_threshold,
        gold.expansion_threshold,
        gold.close_contraction_threshold,
        gold.close_expansion_threshold,
    ):
        assert not spin.isEnabled()
    print("  ✓ 有 BA Maker 委托时锁定 Maker 自动开仓和平仓")


def _set_checked_no_signal(checkbox, checked: bool) -> None:
    checkbox.blockSignals(True)
    checkbox.setChecked(checked)
    checkbox.blockSignals(False)


def _maker_cancel_result(
    action: str = "open", ba_message: str = "BA Maker 100s 未成交已撤单"
) -> HedgeTradeResult:
    return HedgeTradeResult(
        action=action,
        success=False,
        legs=[
            LegResult(
                platform="BA",
                success=False,
                message=ba_message,
            ),
            LegResult(
                platform="MT5",
                success=False,
                message="BA 委托未成交，已跳过 Exness 对冲下单",
            ),
        ],
        message="黄金开仓收缩(Maker)失败",
    )


def test_auto_maker_timeout_restore_previous_checkbox():
    from app.main_window import MainWindow

    app = QApplication.instance() or QApplication(sys.argv)
    window = MainWindow()
    auto = window.gold_actions.auto_trade_settings
    _set_checked_no_signal(auto.contraction_enabled, True)
    _set_checked_no_signal(auto.expansion_enabled, True)
    window._pending_auto_trade = (
        "open",
        "xau",
        HedgeMode.CONTRACTION.value,
        GoldOrderMode.MAKER.value,
    )
    window._pending_auto_maker_restore = window._capture_auto_maker_restore(
        "xau", GoldOrderMode.MAKER.value
    )

    auto.set_pending_order(True, 1.0)
    assert not auto.contraction_enabled.isChecked()
    assert not auto.expansion_enabled.isChecked()
    auto.set_pending_order(False)
    window._on_trade_finished(_maker_cancel_result("open"))

    assert auto.contraction_enabled.isChecked()
    assert auto.expansion_enabled.isChecked()
    window.close()
    print("  ✓ 自动 Maker 未成交自动撤单后恢复原勾选")


def test_auto_maker_timeout_restore_accepts_cancel_variants():
    from app.main_window import MainWindow

    app = QApplication.instance() or QApplication(sys.argv)
    window = MainWindow()
    auto = window.gold_actions.auto_trade_settings
    _set_checked_no_signal(auto.contraction_enabled, True)
    window._pending_auto_trade = (
        "open",
        "xau",
        HedgeMode.CONTRACTION.value,
        GoldOrderMode.MAKER.value,
    )
    window._pending_auto_maker_restore = window._capture_auto_maker_restore(
        "xau", GoldOrderMode.MAKER.value
    )

    auto.set_pending_order(True, 1.0)
    auto.set_pending_order(False)
    window._on_trade_finished(
        _maker_cancel_result("open", "BA Maker 已取消，0成交")
    )

    assert auto.contraction_enabled.isChecked()
    window.close()
    print("  ✓ 自动 Maker 自动撤单文案变化时仍恢复勾选")


def test_auto_maker_timeout_restore_on_open_orders_cleared_before_trade_finished():
    from app.main_window import MainWindow

    app = QApplication.instance() or QApplication(sys.argv)
    window = MainWindow()
    auto = window.gold_actions.auto_trade_settings
    _set_checked_no_signal(auto.contraction_enabled, True)
    window._pending_auto_trade = (
        "open",
        "xau",
        HedgeMode.CONTRACTION.value,
        GoldOrderMode.MAKER.value,
    )
    window._pending_auto_maker_restore = window._capture_auto_maker_restore(
        "xau", GoldOrderMode.MAKER.value
    )
    window._pending_auto_maker_auto_cancel_restore = True
    window._current_ba_open_order_keys = set()

    window._on_open_orders([])
    assert auto.contraction_enabled.isChecked()
    assert window._pending_auto_trade is not None
    assert window._pending_auto_maker_restored_after_cancel is True
    window._on_trade_finished(_maker_cancel_result("open"))
    assert window._pending_auto_trade is None
    assert auto.contraction_enabled.isChecked()
    window.close()
    print("  ✓ 自动 Maker 委托清空后先恢复勾选，再等 trade_finished 收尾")


def test_auto_maker_timeout_restore_does_not_reclear_restored_checkbox():
    from app.main_window import MainWindow

    app = QApplication.instance() or QApplication(sys.argv)
    window = MainWindow()
    auto = window.gold_actions.auto_trade_settings
    _set_checked_no_signal(auto.contraction_enabled, True)
    window._pending_auto_trade = (
        "open",
        "xau",
        HedgeMode.CONTRACTION.value,
        GoldOrderMode.MAKER.value,
    )
    window._pending_auto_maker_restore = window._capture_auto_maker_restore(
        "xau", GoldOrderMode.MAKER.value
    )
    window._pending_auto_maker_auto_cancel_restore = True
    window._current_ba_open_order_keys = set()
    window._on_open_orders([])
    window._on_trade_finished(_maker_cancel_result("open"))
    assert auto.contraction_enabled.isChecked()
    window.close()
    print("  ✓ 自动 Maker 恢复后不会被 trade_finished 再次取消")


def test_auto_maker_restore_reevaluates_latest_market():
    from app.main_window import MainWindow

    app = QApplication.instance() or QApplication(sys.argv)
    window = MainWindow()
    auto = window.gold_actions.auto_trade_settings
    _set_checked_no_signal(auto.contraction_enabled, True)
    auto.contraction_threshold.setValue(2.0)
    window.config = window._merge_config()
    window.engine._running = True
    window.engine.refresh_positions = lambda **_kwargs: None  # type: ignore[method-assign]
    window.engine._last_market_update = MarketUpdate(
        spreads={
            "xau": SpreadSnapshot(
                preset_id="xau",
                ba_bid=4365.65,
                ba_ask=4365.66,
                mt5_bid=4363.20,
                mt5_ask=4363.60,
                mid_spread=2.45,
            )
        }
    )
    captured: list[tuple[str, str, str]] = []
    window._execute_auto_open = lambda *args: captured.append(args)  # type: ignore[method-assign]
    window._pending_auto_trade = (
        "open",
        "xau",
        HedgeMode.CONTRACTION.value,
        GoldOrderMode.MAKER.value,
    )
    window._pending_auto_maker_restore = window._capture_auto_maker_restore(
        "xau", GoldOrderMode.MAKER.value
    )

    auto.set_pending_order(True, 1.0)
    auto.set_pending_order(False)
    window._on_trade_finished(_maker_cancel_result("open"))
    app.processEvents()

    assert auto.contraction_enabled.isChecked()
    assert captured == [("xau", HedgeMode.CONTRACTION.value, GoldOrderMode.MAKER.value)]
    save_config(AppConfig(connection_mode=ConnectionMode.DEMO.value))
    window.close()
    print("  ✓ 自动 Maker 恢复勾选后立即按最近行情重评估")


def test_manual_cancel_does_not_restore_auto_maker_checkbox():
    from app.main_window import MainWindow

    app = QApplication.instance() or QApplication(sys.argv)
    window = MainWindow()
    auto = window.gold_actions.auto_trade_settings
    _set_checked_no_signal(auto.contraction_enabled, True)
    window._pending_auto_trade = (
        "open",
        "xau",
        HedgeMode.CONTRACTION.value,
        GoldOrderMode.MAKER.value,
    )
    window._pending_auto_maker_restore = window._capture_auto_maker_restore(
        "xau", GoldOrderMode.MAKER.value
    )
    window.engine._running = True
    window.engine.cancel_all_open_orders = lambda: None  # type: ignore[method-assign]

    auto.set_pending_order(True, 1.0)
    auto.set_pending_order(False)
    window._on_manual_cancel_orders()
    window._on_trade_finished(_maker_cancel_result("open"))

    assert not auto.contraction_enabled.isChecked()
    window.close()
    print("  ✓ 手动撤销 Maker 委托后不自动恢复勾选")


def test_market_auto_failure_does_not_restore_checkbox():
    from app.main_window import MainWindow

    app = QApplication.instance() or QApplication(sys.argv)
    window = MainWindow()
    auto = window.gold_actions.auto_trade_settings
    _set_checked_no_signal(auto.market_contraction_enabled, True)
    window._pending_auto_trade = (
        "open",
        "xau",
        HedgeMode.CONTRACTION.value,
        GoldOrderMode.MARKET.value,
    )
    window._pending_auto_maker_restore = window._capture_auto_maker_restore(
        "xau", GoldOrderMode.MARKET.value
    )
    window._on_trade_finished(
        HedgeTradeResult(action="open", success=False, message="市价开仓失败")
    )

    assert not auto.market_contraction_enabled.isChecked()
    window.close()
    print("  ✓ 市价自动失败不使用 Maker 恢复勾选逻辑")


def test_auto_maker_pending_order_announces_accepted():
    from app.main_window import MainWindow

    app = QApplication.instance() or QApplication(sys.argv)
    window = MainWindow()
    spoken: list[str] = []
    window._voice.say = lambda text, on_finished=None: spoken.append(text)  # type: ignore[method-assign]
    window._pending_auto_trade = (
        "open",
        "xau",
        HedgeMode.CONTRACTION.value,
        GoldOrderMode.MAKER.value,
    )

    window._on_open_orders(
        [
            OpenOrder(
                platform="BA",
                symbol="XAUUSDT",
                order_id="42",
                side=Side.SELL,
                total_quantity=1.0,
                remaining_quantity=1.0,
            )
        ]
    )
    window._on_open_orders(
        [
            OpenOrder(
                platform="BA",
                symbol="XAUUSDT",
                order_id="42",
                side=Side.SELL,
                total_quantity=1.0,
                remaining_quantity=1.0,
            )
        ]
    )

    assert spoken == ["委托成功"]
    window.close()
    print("  ✓ 自动 Maker 委托挂上 BA 后语音播报委托成功")


def test_auto_maker_pending_order_schedules_configured_timeout_cancel():
    from app.main_window import MainWindow

    app = QApplication.instance() or QApplication(sys.argv)
    window = MainWindow()
    auto = window.gold_actions.auto_trade_settings
    auto.maker_timeout_sec.setValue(7)
    window._pending_auto_trade = (
        "open",
        "xau",
        HedgeMode.CONTRACTION.value,
        GoldOrderMode.MAKER.value,
    )
    window.engine.sync_config = lambda _config: None  # type: ignore[method-assign]
    cancel_calls: list[dict] = []
    window.engine.cancel_all_open_orders = (  # type: ignore[method-assign]
        lambda **kwargs: cancel_calls.append(kwargs)
    )
    timers: list[tuple[int, object]] = []

    with patch("app.main_window.QTimer.singleShot", side_effect=lambda ms, cb: timers.append((ms, cb))):
        window._on_open_orders(
            [
                OpenOrder(
                    platform="BA",
                    symbol="XAUUSDT",
                    order_id="42",
                    side=Side.SELL,
                    total_quantity=1.0,
                    remaining_quantity=1.0,
                )
            ]
        )

    timeout_timers = [(ms, cb) for ms, cb in timers if ms == 7000]
    assert len(timeout_timers) == 1
    timeout_timers[0][1]()
    assert cancel_calls == [{"manual": False}]
    window.close()
    print("  ✓ 自动 Maker 委托挂上后按设置秒数自动撤单")


def test_auto_trade_hint_routes_to_matching_symbol_panel():
    from app.main_window import MainWindow

    app = QApplication.instance() or QApplication(sys.argv)
    window = MainWindow()
    auto_gold = window.gold_actions.auto_trade_settings
    auto_silver = window.silver_actions.auto_trade_settings
    _set_checked_no_signal(auto_gold.contraction_enabled, True)
    _set_checked_no_signal(auto_silver.contraction_enabled, True)

    window._auto_trade_hint("自动下单：白银 市价点差 -0.007 未达收缩阈值 ≥ 0.080")

    assert auto_gold.status_label.text() == ""
    assert "白银" in auto_silver.status_label.text()
    window.close()
    print("  ✓ 自动下单诊断提示只显示在对应品种板块")


def test_open_orders_dedupes_same_ba_order_for_pending_light():
    from app.main_window import MainWindow

    app = QApplication.instance() or QApplication(sys.argv)
    window = MainWindow()
    orders = [
        OpenOrder(
            platform="BA",
            symbol="XAUUSDT",
            order_id="42",
            side=Side.SELL,
            total_quantity=2.0,
            remaining_quantity=2.0,
        ),
        OpenOrder(
            platform="BA",
            symbol="XAUUSDT",
            order_id="42",
            side=Side.SELL,
            total_quantity=2.0,
            remaining_quantity=2.0,
        ),
    ]

    window._on_open_orders(orders)

    light = window.gold_actions.auto_trade_settings.maker_pending_light
    assert light.text() == "● 有委托 · 剩余量 2"
    assert "总量2" in window.gold_actions.pending_label.text()
    assert "总量4" not in window.gold_actions.pending_label.text()
    window.close()
    print("  ✓ 委托同步：同一 BA order_id 去重后再统计数量")


def test_pending_ba_order_displays_order_price_index():
    from app.main_window import MainWindow

    app = QApplication.instance() or QApplication(sys.argv)
    window = MainWindow()
    window.gold_actions.update_spread(
        SpreadSnapshot(
            preset_id="xau",
            ba_bid=4365.64,
            ba_ask=4365.66,
            mt5_bid=4363.40,
            mt5_ask=4363.65,
            mid_spread=2.24,
        )
    )
    window._on_open_orders(
        [
            OpenOrder(
                platform="BA",
                symbol="XAUUSDT",
                order_id="42",
                side=Side.SELL,
                total_quantity=1.0,
                remaining_quantity=1.0,
                price=4365.65,
            )
        ]
    )

    text = window.gold_actions.pending_label.text()
    assert "@ 4365.650" in text
    assert "指数+2.000" in text
    window.close()
    print("  ✓ 委托同步：BA 委托显示委托价与对应指数")


def main() -> int:
    errors: list[str] = []
    tests = [
        test_contraction_fires_immediately,
        test_contraction_resets_when_spread_drops,
        test_expansion_fires_when_spread_below_threshold,
        test_auto_open_maker_waits_timeout_not_spread_guard,
        test_auto_open_spread_check_reports_executable_spread,
        test_disabled_strategy_never_fires,
        test_fires_immediately_without_cooldown,
        test_hysteresis_keeps_timer_near_threshold,
        test_auto_close_contraction_fires_immediately,
        test_contraction_open_with_existing_contraction_position,
        test_expansion_blocked_with_contraction_position,
        test_other_symbol_unaffected_by_position,
        test_executable_spread_four_scenarios,
        test_executable_spread_falls_back_to_mid_when_no_quotes,
        test_contraction_open_uses_executable_spread,
        test_market_auto_open_fires_with_market_order_mode,
        test_maker_fire_preserves_market_contraction_timer,
        test_config_roundtrip,
        test_manual_cancel_button_emits_signal,
        test_pending_light_states,
        test_pending_order_locks_all_maker_auto_checkboxes,
        test_auto_maker_timeout_restore_previous_checkbox,
        test_auto_maker_timeout_restore_accepts_cancel_variants,
        test_auto_maker_timeout_restore_on_open_orders_cleared_before_trade_finished,
        test_auto_maker_timeout_restore_does_not_reclear_restored_checkbox,
        test_auto_maker_restore_reevaluates_latest_market,
        test_manual_cancel_does_not_restore_auto_maker_checkbox,
        test_market_auto_failure_does_not_restore_checkbox,
        test_auto_maker_pending_order_announces_accepted,
        test_auto_maker_pending_order_schedules_configured_timeout_cancel,
        test_auto_trade_hint_routes_to_matching_symbol_panel,
        test_open_orders_dedupes_same_ba_order_for_pending_light,
        test_pending_ba_order_displays_order_price_index,
    ]
    for fn in tests:
        try:
            fn()
        except Exception as exc:
            errors.append(f"{fn.__name__}: {exc}")

    if errors:
        print("AUTO TRADE TEST FAILED:")
        for err in errors:
            print(f"  - {err}")
        return 1
    print("ALL AUTO TRADE TESTS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
