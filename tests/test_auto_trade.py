"""Auto trade strategy: contraction/expansion thresholds, instant trigger."""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from app.core.auto_trade import AutoTradeState, evaluate_auto_closes, evaluate_auto_trades
from app.core.config import load_config, save_config
from app.core.models import AppConfig, ConnectionMode, HedgeMode, Position, Side, SpreadSnapshot


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


def main() -> int:
    errors: list[str] = []
    tests = [
        test_contraction_fires_immediately,
        test_contraction_resets_when_spread_drops,
        test_expansion_fires_when_spread_below_threshold,
        test_disabled_strategy_never_fires,
        test_fires_immediately_without_cooldown,
        test_hysteresis_keeps_timer_near_threshold,
        test_auto_close_contraction_fires_immediately,
        test_contraction_open_with_existing_contraction_position,
        test_expansion_blocked_with_contraction_position,
        test_other_symbol_unaffected_by_position,
        test_market_auto_open_fires_with_market_order_mode,
        test_maker_fire_preserves_market_contraction_timer,
        test_config_roundtrip,
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
