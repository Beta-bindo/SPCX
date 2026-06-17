import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.models import AppConfig, Position, Quote, Side, SpreadSnapshot
from app.core.pnl_calculator import build_spread_snapshot, calculate_pnl
from app.core.position_detail import build_platform_details_for_preset


def test_spread_snapshot_exec_vs_mid():
    ba = Quote(symbol="XAUUSDT", bid=2652.0, ask=2652.3, is_simulated=True)
    mt5 = Quote(symbol="XAUUSD", bid=2649.0, ask=2649.2, is_simulated=True)
    snap = build_spread_snapshot(ba, mt5)
    assert snap is not None
    assert snap.exec_spread == 2652.0 - 2649.2
    assert abs(snap.mid_spread - (2652.0 - 2649.0)) < 0.01


def test_pnl_with_fees():
    ba = Quote(symbol="XAUUSDT", bid=2650.0, ask=2650.2, is_simulated=True)
    mt5 = Quote(symbol="XAUUSD", bid=2648.0, ask=2648.2, is_simulated=True)
    positions = [
        Position(platform="BA", symbol="XAUUSDT", side=Side.SELL, quantity=0.01, entry_price=2651.0),
        Position(platform="MT5", symbol="XAUUSD", side=Side.BUY, quantity=0.01, entry_price=2647.0),
    ]
    snap = build_spread_snapshot(ba, mt5)
    cfg = AppConfig(ba_fee_rate=0.0004, mt5_commission_per_lot=0, mt5_spread_points=0.25)
    updated, summary = calculate_pnl(
        positions,
        {"XAUUSDT": ba},
        {"XAUUSD": mt5},
        cfg,
        snap,
    )
    assert len(updated) == 2
    assert summary.gross_pnl != 0
    assert summary.total_fees > 0
    # 持仓上的 estimated_fee 仍为预估平仓单腿费用，用于平仓记账 fallback。
    assert all(p.estimated_fee > 0 for p in updated)
    assert summary.total_fees > round(sum(p.estimated_fee for p in updated), 4)
    # 汇总净盈亏扣买入+卖出的往返费用。
    assert summary.net_pnl == round(summary.gross_pnl - summary.total_fees, 2)
    print("PNL TEST PASSED")


def test_platform_detail_points_are_separate_from_summary_net():
    ba = Quote(symbol="XAUUSDT", bid=2650.0, ask=2650.2, is_simulated=True)
    mt5 = Quote(symbol="XAUUSD", bid=2648.0, ask=2648.2, is_simulated=True)
    positions = [
        Position(platform="BA", symbol="XAUUSDT", side=Side.SELL, quantity=0.01, entry_price=2651.0),
        Position(platform="MT5", symbol="XAUUSD", side=Side.BUY, quantity=0.01, entry_price=2647.0),
    ]
    cfg = AppConfig(ba_fee_rate=0.0004, mt5_commission_per_lot=0, mt5_spread_points=0.25)
    updated, summary = calculate_pnl(
        positions,
        {"XAUUSDT": ba},
        {"XAUUSD": mt5},
        cfg,
        build_spread_snapshot(ba, mt5),
    )

    ba_detail, mt5_detail = build_platform_details_for_preset(
        "xau",
        updated,
        {"XAUUSDT": ba},
        {"XAUUSD": mt5},
        cfg,
    )

    assert ba_detail.pnl == summary.ba_pnl
    assert mt5_detail.pnl == summary.mt5_pnl
    assert ba_detail.point_diff == round(2650.2 - 2651.0, 3)
    assert mt5_detail.point_diff == round(2648.0 - 2647.0, 3)
    assert summary.net_pnl == round(summary.gross_pnl - summary.total_fees, 2)
    assert summary.net_pnl < summary.gross_pnl


def test_ba_detail_liquidation_price_uses_exchange_value_only():
    ba = Quote(symbol="XAUUSDT", bid=2650.0, ask=2650.2, is_simulated=True)
    cfg = AppConfig(ba_leverage=20)

    ba_detail, _ = build_platform_details_for_preset(
        "xau",
        [
            Position(
                platform="BA",
                symbol="XAUUSDT",
                side=Side.SELL,
                quantity=0.01,
                entry_price=2651.0,
                liquidation_price=2800.1234,
            )
        ],
        {"XAUUSDT": ba},
        {},
        cfg,
    )
    assert ba_detail.liquidation_price == 2800.123

    ba_detail, _ = build_platform_details_for_preset(
        "xau",
        [
            Position(
                platform="BA",
                symbol="XAUUSDT",
                side=Side.SELL,
                quantity=0.01,
                entry_price=2651.0,
                liquidation_price=0.0,
            )
        ],
        {"XAUUSDT": ba},
        {},
        cfg,
    )
    assert ba_detail.liquidation_price == 0.0


def test_platform_detail_liq_buffer_displays_price_distance():
    cfg = AppConfig(ba_leverage=20, mt5_leverage=100)
    ba_quote = Quote(symbol="XAUUSDT", bid=2640.0, ask=2640.2, is_simulated=True)
    mt5_quote = Quote(symbol="XAUUSD", bid=2640.0, ask=2640.2, is_simulated=True)

    ba_detail, mt5_detail = build_platform_details_for_preset(
        "xau",
        [
            Position(
                platform="BA",
                symbol="XAUUSDT",
                side=Side.SELL,
                quantity=500,
                entry_price=2650.0,
                liquidation_price=2800.0,
            ),
            Position(
                platform="MT5",
                symbol="XAUUSD",
                side=Side.BUY,
                quantity=1.0,
                entry_price=2650.0,
                liquidation_price=2600.0,
            ),
        ],
        {"XAUUSDT": ba_quote},
        {"XAUUSD": mt5_quote},
        cfg,
    )

    assert ba_detail.liq_buffer == 159.8
    assert mt5_detail.liq_buffer == 40.0


def test_mt5_liq_buffer_falls_back_to_account_buffer_points():
    cfg = AppConfig(mt5_leverage=100)

    _ba_detail, mt5_detail = build_platform_details_for_preset(
        "xau",
        [
            Position(
                platform="MT5",
                symbol="XAUUSD",
                side=Side.SELL,
                quantity=0.04,
                entry_price=4358.0,
                liquidation_price=4344.906,
                mark_price=0.0,
                exchange_liq_buffer=56.0,
            )
        ],
        {},
        {},
        cfg,
    )

    assert mt5_detail.liq_buffer == 14.0


def test_mt5_liq_buffer_uses_abs_distance_to_model_liq_price():
    cfg = AppConfig(mt5_leverage=2000)
    mt5_quote = Quote(symbol="XAUUSD", bid=4358.533, ask=4358.700, is_simulated=False)

    _ba_detail, mt5_detail = build_platform_details_for_preset(
        "xau",
        [
            Position(
                platform="MT5",
                symbol="XAUUSD",
                side=Side.BUY,
                quantity=0.04,
                entry_price=4358.40,
                liquidation_price=4373.674,
                mark_price=4358.533,
            )
        ],
        {},
        {"XAUUSD": mt5_quote},
        cfg,
    )

    assert mt5_detail.liquidation_price == 4373.674
    assert mt5_detail.liq_buffer == 15.14


if __name__ == "__main__":
    test_spread_snapshot_exec_vs_mid()
    test_pnl_with_fees()
    test_platform_detail_points_are_separate_from_summary_net()
    test_ba_detail_liquidation_price_uses_exchange_value_only()
    test_platform_detail_liq_buffer_displays_price_distance()
    test_mt5_liq_buffer_falls_back_to_account_buffer_points()
    test_mt5_liq_buffer_uses_abs_distance_to_model_liq_price()
