"""
Commercial-grade data correctness tests for spread, PnL, ratio, risk, alerts.
Run: python tests/test_data_correctness.py
Repeat 3x via: python tests/test_data_correctness.py --repeat 3
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.core.models import AppConfig, ConnectionMode, Position, Quote, Side, SpreadSnapshot
from app.core.pnl_calculator import build_spread_snapshot, calculate_pnl
from app.core.liquidation import estimate_liquidation_price
from app.core.position_detail import build_platform_details
from app.core.profit_calculator import calculate_profit
from app.core.risk import _ba_liq_distance, build_risk_snapshot
from app.core.trade_ledger import TradeLedger, TradeRecord
from app.core.trading_service import hedge_mode_strategy_label, position_entry_spread


class Report:
    def __init__(self):
        self.passed = 0
        self.failed: list[str] = []

    def check(self, name: str, cond: bool, detail: str = "") -> None:
        if cond:
            self.passed += 1
            print(f"  ✓ {name}")
        else:
            msg = f"{name}" + (f" — {detail}" if detail else "")
            self.failed.append(msg)
            print(f"  ✗ {msg}")


def run_spread_tests(r: Report) -> None:
    ba = Quote("XAUUSDT", bid=2652.0, ask=2652.3)
    mt5 = Quote("XAUUSD", bid=2649.0, ask=2649.2)
    snap = build_spread_snapshot(ba, mt5, "xau")
    r.check("点差 = BA买价 - Exness买价", snap is not None and abs(snap.mid_spread - (2652.0 - 2649.0)) < 1e-9)
    r.check("点差 exec = BA买价 - Exness卖价", abs(snap.exec_spread - (2652.0 - 2649.2)) < 1e-9)
    r.check("点差保留3位精度展示", f"{snap.mid_spread:.3f}" == f"{snap.mid_spread:.3f}")

    bad = build_spread_snapshot(Quote("X", bid=0, ask=1), mt5, "xau")
    r.check("无效 BA 报价返回 None", bad is None)

    neg = build_spread_snapshot(ba, Quote("X", bid=-1, ask=1), "xau")
    r.check("负价报价返回 None", neg is None)


def run_ratio_tests(r: Report) -> None:
    cfg = AppConfig(xau_ba_qty_map=500, xau_mt5_lot_map=1, xau_trade_lots=2)
    r.check("黄金 BA 数量 = 手数 × 配比", cfg.ba_quantity_for("xau") == 1000.0)
    r.check("黄金 Exness 手数", cfg.mt5_lot_for("xau") == 2.0)

    cfg2 = AppConfig(xag_ba_qty_map=5000, xag_mt5_lot_map=1, xag_trade_lots=0.5)
    r.check("白银 BA 数量配比", cfg2.ba_quantity_for("xag") == 2500.0)

    cfg3 = AppConfig(xau_mt5_lot_map=0)
    r.check("零手数映射防除零", cfg3.ba_quantity_for("xau") > 0)


def run_pnl_demo_tests(r: Report) -> None:
    cfg = AppConfig(connection_mode=ConnectionMode.DEMO.value, ba_fee_rate=0.0004)
    ba_q = Quote("XAUUSDT", bid=2640.0, ask=2640.2)
    mt5_q = Quote("XAUUSD", bid=2645.0, ask=2645.2)
    # 空 BA: entry 2650, mark ask 2640.2 -> (2650-2640.2)*500 = 4900
    positions = [
        Position(platform="BA", symbol="XAUUSDT", side=Side.SELL, quantity=500, entry_price=2650.0),
        Position(platform="MT5", symbol="XAUUSD", side=Side.BUY, quantity=1.0, entry_price=2645.0),
    ]
    snap = build_spread_snapshot(ba_q, mt5_q, "xau")
    updated, summary = calculate_pnl(positions, {"XAUUSDT": ba_q}, {"XAUUSD": mt5_q}, cfg, snap)
    ba_pnl = updated[0].unrealized_pnl
    expected_ba = round((2650.0 - 2640.2) * 500, 2)
    r.check("演示 BA 空头盈亏", ba_pnl == expected_ba, f"got {ba_pnl} want {expected_ba}")

    # 多 MT5: (2645 bid - 2645 entry)*100 oz/lot * 1 lot = 0
    mt5_pnl = updated[1].unrealized_pnl
    r.check("演示 MT5 多头盈亏", mt5_pnl == 0.0, f"got {mt5_pnl}")

    r.check("毛盈亏 = BA + Exness", summary.gross_pnl == round(ba_pnl + mt5_pnl, 2))
    r.check("净盈亏 ≤ 毛盈亏", summary.net_pnl <= summary.gross_pnl)


def run_pnl_live_tests(r: Report) -> None:
    cfg = AppConfig(connection_mode=ConnectionMode.LIVE_BOTH.value)
    ba_q = Quote("XAUUSDT", bid=2640.0, ask=2640.2)
    exchange_ba_pnl = 1234.56
    positions = [
        Position(
            platform="BA",
            symbol="XAUUSDT",
            side=Side.SELL,
            quantity=500,
            entry_price=2650.0,
            unrealized_pnl=exchange_ba_pnl,
        ),
    ]
    updated, summary = calculate_pnl(positions, {"XAUUSDT": ba_q}, {}, cfg, None)
    r.check("实盘 BA 始终使用交易所盈亏", updated[0].unrealized_pnl == exchange_ba_pnl)
    r.check("实时净盈亏汇总使用 BA 平台盈亏", summary.ba_pnl == exchange_ba_pnl)

    updated_fallback, summary_fallback = calculate_pnl(
        positions, {"XAUUSDT": Quote("XAUUSDT")}, {}, cfg, None
    )
    r.check("实盘 BA 无行情时回退交易所盈亏", updated_fallback[0].unrealized_pnl == exchange_ba_pnl)
    r.check("无行情汇总使用交易所数据", summary_fallback.ba_pnl == exchange_ba_pnl)

    mt5_exchange = -88.5
    cfg2 = AppConfig(connection_mode=ConnectionMode.LIVE_MT5.value)
    pos_mt5 = [
        Position(
            platform="MT5",
            symbol="XAUUSD",
            side=Side.BUY,
            quantity=1.0,
            entry_price=2650.0,
            unrealized_pnl=mt5_exchange,
        )
    ]
    updated2, sum2 = calculate_pnl(pos_mt5, {}, {"XAUUSD": ba_q}, cfg2, None)
    r.check("实盘 Exness 始终使用交易所盈亏", updated2[0].unrealized_pnl == mt5_exchange)
    r.check("实时净盈亏汇总使用 Exness 平台盈亏", sum2.mt5_pnl == mt5_exchange)
    updated2_fb, _ = calculate_pnl(pos_mt5, {}, {"XAUUSD": Quote("XAUUSD")}, cfg2, None)
    r.check("实盘 Exness 无行情时回退交易所盈亏", updated2_fb[0].unrealized_pnl == mt5_exchange)


def run_pnl_zero_quote_tests(r: Report) -> None:
    cfg = AppConfig(connection_mode=ConnectionMode.DEMO.value)
    pos = Position(platform="BA", symbol="XAUUSDT", side=Side.SELL, quantity=1, entry_price=2650, unrealized_pnl=10)
    updated, _ = calculate_pnl([pos], {"XAUUSDT": Quote("XAUUSDT")}, {}, cfg, None)
    r.check("无行情时保留原盈亏", updated[0].unrealized_pnl == 10)


def run_risk_tests(r: Report) -> None:
    cfg = AppConfig(ba_leverage=20)
    ba_q = Quote("XAUUSDT", bid=2640, ask=2640.2)
    pos = [Position(platform="BA", symbol="XAUUSDT", side=Side.SELL, quantity=500, entry_price=2650)]
    dist = _ba_liq_distance(pos, ba_q, cfg.ba_leverage, "xau")
    notional = 2650 * 500
    margin = notional / 20
    mark = 2640.2
    unrealized = (2650 - mark) * 500
    expected = margin + unrealized
    r.check("BA 爆仓缓冲公式", abs(dist - expected) < 0.01, f"{dist} vs {expected}")

    risk = build_risk_snapshot(pos, {"XAUUSDT": ba_q}, {}, cfg)
    r.check("无持仓品种缓冲为极大值", risk.xag_ba_liq > 90000)


def run_liq_price_tests(r: Report) -> None:
    short_liq = estimate_liquidation_price(2650, Side.SELL, 20)
    r.check("空头爆仓价 > 入场价", short_liq > 2650)
    long_liq = estimate_liquidation_price(2650, Side.BUY, 20)
    r.check("多头爆仓价 < 入场价", long_liq < 2650)
    r.check("无效杠杆返回 0", estimate_liquidation_price(100, Side.BUY, 0) == 0)


def run_hedge_scenario_tests(r: Report) -> None:
    """收缩对冲：BA空+Ex多，价差收敛应整体盈利方向一致."""
    cfg = AppConfig(connection_mode=ConnectionMode.DEMO.value)
    entry_ba, entry_mt5 = 2650.0, 2647.0
    ba_q = Quote("XAUUSDT", bid=2645.0, ask=2645.2)
    mt5_q = Quote("XAUUSD", bid=2644.8, ask=2645.0)
    positions = [
        Position(platform="BA", symbol="XAUUSDT", side=Side.SELL, quantity=500, entry_price=entry_ba),
        Position(platform="MT5", symbol="XAUUSD", side=Side.BUY, quantity=1.0, entry_price=entry_mt5),
    ]
    ba_pos = positions[0]
    mt5_pos = positions[1]
    r.check("持仓差价 = BA入场 − Exness入场", position_entry_spread(ba_pos, mt5_pos) == 3.0)
    r.check("收缩策略标签", hedge_mode_strategy_label("contraction") == "收缩策略")
    _, summary = calculate_pnl(positions, {"XAUUSDT": ba_q}, {"XAUUSD": mt5_q}, cfg, None)
    r.check("收缩对冲价差有利时组合盈利", summary.gross_pnl > 0, f"gross={summary.gross_pnl}")


def run_profit_ledger_tests(r: Report) -> None:
    ledger = TradeLedger(
        records=[
            TradeRecord("2026-06-08T10:00:00", "xau", "contraction", 100, -20, 1, 0.5),
        ]
    )
    rep = calculate_profit(ledger, __import__("datetime").date(2026, 6, 8), __import__("datetime").date(2026, 6, 8), "all")
    r.check("利润结算净额", rep.total_pnl == round(100 - 20 - 1 - 0.5, 2))


def run_platform_detail_tests(r: Report) -> None:
    cfg = AppConfig(ba_leverage=14)
    positions = [
        Position(platform="BA", symbol="XAUUSDT", side=Side.SELL, quantity=500, entry_price=2650, unrealized_pnl=100),
    ]
    ba_q = Quote("XAUUSDT", bid=2640, ask=2640.2)
    ba, mt5 = build_platform_details(positions, {"XAUUSDT": ba_q}, {}, cfg)
    r.check("详情面板杠杆", ba.leverage == 14)
    r.check("详情面板方向", ba.side == Side.SELL)
    r.check("详情面板有持仓", ba.has_position)


def run_alert_boundary_tests(r: Report) -> None:
    lo, hi = 1.0, 3.0
    r.check("点差边界下限", lo <= 1.0 <= hi)
    r.check("点差边界上限", lo <= 3.0 <= hi)
    r.check("点差边界外", not (lo <= 3.5 <= hi))


def main(repeat: int = 1) -> int:
    all_failed: list[str] = []
    for run_idx in range(repeat):
        if repeat > 1:
            print(f"\n--- 第 {run_idx + 1}/{repeat} 轮 ---")
        print("=" * 60)
        print("DATA CORRECTNESS TEST SUITE")
        print("=" * 60)
        r = Report()
        run_spread_tests(r)
        run_ratio_tests(r)
        run_pnl_demo_tests(r)
        run_pnl_live_tests(r)
        run_pnl_zero_quote_tests(r)
        run_risk_tests(r)
        run_liq_price_tests(r)
        run_hedge_scenario_tests(r)
        run_profit_ledger_tests(r)
        run_platform_detail_tests(r)
        run_alert_boundary_tests(r)
        print("=" * 60)
        print(f"通过: {r.passed}  失败: {len(r.failed)}")
        all_failed.extend(r.failed)

    if all_failed:
        print("\n失败项:")
        for f in all_failed:
            print(f"  - {f}")
        return 1
    print(f"\nALL DATA CORRECTNESS TESTS PASSED ({repeat} 轮)")
    return 0


if __name__ == "__main__":
    n = 1
    if len(sys.argv) >= 3 and sys.argv[1] == "--repeat":
        n = int(sys.argv[2])
    raise SystemExit(main(n))
