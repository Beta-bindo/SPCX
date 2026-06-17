"""Tests for exchange-aligned liquidation logic."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.core.liquidation import (
    ba_cross_account_liq_buffer,
    ba_isolated_liq_buffer,
    calc_liquidation_price_from_profit,
    estimate_liquidation_price,
    liq_buffer_from_prices,
    liq_price_distance_from_prices,
    mt5_account_liq_buffer,
    resolve_position_liq_price_distance,
    resolve_position_liq_buffer,
)
from app.core.models import Position, Quote, Side


def test_ba_isolated_buffer_matches_exchange_formula():
    buf = ba_isolated_liq_buffer(isolated_wallet=10000, unrealized_pnl=-5000, maint_margin=500)
    assert buf == 4500.0


def test_ba_cross_account_buffer():
    assert ba_cross_account_liq_buffer(10200, 200) == 10000.0


def test_liq_buffer_from_exchange_prices_long():
    buf = liq_buffer_from_prices(Side.BUY, mark=2650, liquidation_price=2600, quantity=500)
    assert buf == 25000.0


def test_liq_price_distance_from_exchange_prices():
    assert liq_price_distance_from_prices(Side.BUY, mark=2650, liquidation_price=2600) == 50.0
    dist = liq_price_distance_from_prices(Side.SELL, mark=2650.2, liquidation_price=2800)
    assert abs(dist - 149.8) < 0.0001


def test_mt5_account_buffer_with_zero_stop_out():
    # Exness 常见：stop-out 0% => 权益归零
    assert mt5_account_liq_buffer(equity=10200, margin=10000, stop_out_pct=0) == 10200.0


def test_mt5_liquidation_price_matches_account_equity_model():
    """用户场景：本金10200，100手多头@100，点值100，跌到约98.98爆仓。"""
    entry = 100.0
    lots = 100.0
    oz = 100.0
    equity_without = 10200.0
    margin = 10000.0
    stop_out = 0.0

    def profit_at(close: float) -> float:
        return (close - entry) * lots * oz

    liq = calc_liquidation_price_from_profit(
        Side.BUY,
        entry,
        equity_without,
        margin,
        stop_out,
        profit_at,
    )
    assert 98.9 <= liq <= 99.0, f"expected ~98.98, got {liq}"


def test_resolve_position_reprices_from_live_quote():
    # 有爆仓价 + 实时报价时，按实时价逐 tick 重算缓冲（使「爆」跟手），
    # 不再停留在轮询时的交易所缓冲值。
    pos = Position(
        platform="BA",
        symbol="XAUUSDT",
        side=Side.BUY,
        quantity=500,
        entry_price=2650,
        liquidation_price=2600,
        mark_price=2640,
        exchange_liq_buffer=888.0,
    )
    # BUY 取买价 2640 → (2640 − 2600) × 500 = 20000
    buf = resolve_position_liq_buffer(pos, Quote("XAUUSDT", 2640, 2640.2), "xau", 100)
    assert buf == 20000.0


def test_resolve_position_price_distance_uses_live_quote():
    pos = Position(
        platform="BA",
        symbol="XAUUSDT",
        side=Side.SELL,
        quantity=500,
        entry_price=2650,
        liquidation_price=2800,
        mark_price=2640,
    )
    dist = resolve_position_liq_price_distance(pos, Quote("XAUUSDT", 2640, 2640.2))
    assert abs(dist - 159.8) < 0.0001


def test_resolve_position_falls_back_to_exchange_buffer():
    # 无爆仓价 / 无实时价时，退回交易所返回的轮询缓冲。
    pos = Position(
        platform="BA",
        symbol="XAUUSDT",
        side=Side.BUY,
        quantity=500,
        entry_price=2650,
        liquidation_price=0.0,
        mark_price=0.0,
        exchange_liq_buffer=888.0,
    )
    buf = resolve_position_liq_buffer(pos, None, "xau", 100)
    assert buf == 888.0


def test_estimate_fallback_for_demo():
    assert estimate_liquidation_price(2650, Side.BUY, 20) < 2650
    assert estimate_liquidation_price(2650, Side.SELL, 20) > 2650


if __name__ == "__main__":
    test_ba_isolated_buffer_matches_exchange_formula()
    test_ba_cross_account_buffer()
    test_liq_buffer_from_exchange_prices_long()
    test_liq_price_distance_from_exchange_prices()
    test_mt5_account_buffer_with_zero_stop_out()
    test_mt5_liquidation_price_matches_account_equity_model()
    test_resolve_position_reprices_from_live_quote()
    test_resolve_position_price_distance_uses_live_quote()
    test_resolve_position_falls_back_to_exchange_buffer()
    test_estimate_fallback_for_demo()
    print("ALL LIQUIDATION TESTS PASSED")
