import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.models import AppConfig, Position, Quote, Side, SpreadSnapshot
from app.core.pnl_calculator import build_spread_snapshot, calculate_pnl


def test_spread_snapshot_exec_vs_mid():
    ba = Quote(symbol="XAUUSDT", bid=2652.0, ask=2652.3, is_simulated=True)
    mt5 = Quote(symbol="XAUUSD", bid=2649.0, ask=2649.2, is_simulated=True)
    snap = build_spread_snapshot(ba, mt5)
    assert snap is not None
    assert snap.exec_spread == 2652.0 - 2649.2
    assert abs(snap.mid_spread - (2652.15 - 2649.1)) < 0.01


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
    # 单腿预估手续费；净盈亏 = 毛盈亏 - 预估平仓费
    assert summary.net_pnl == round(summary.gross_pnl - summary.total_fees, 2)
    print("PNL TEST PASSED")


if __name__ == "__main__":
    test_spread_snapshot_exec_vs_mid()
    test_pnl_with_fees()
