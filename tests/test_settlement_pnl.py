"""Settlement PnL should use fresh mark-to-market before close."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.core.models import AppConfig, ConnectionMode, Position, Quote, Side
from app.core.pnl_calculator import calculate_pnl


class SettlementPnlTests(unittest.TestCase):
    def test_demo_close_pnl_is_nonzero_when_price_moves(self):
        config = AppConfig(connection_mode=ConnectionMode.DEMO.value)
        ba_quote = Quote(symbol="XAUUSDT", bid=2651.0, ask=2651.2, is_simulated=True)
        mt5_quote = Quote(symbol="XAUUSD", bid=2648.0, ask=2648.2, is_simulated=True)
        positions = [
            Position(
                platform="BA",
                symbol="XAUUSDT",
                side=Side.SELL,
                quantity=500.0,
                entry_price=2650.0,
            ),
            Position(
                platform="MT5",
                symbol="XAUUSD",
                side=Side.BUY,
                quantity=1.0,
                entry_price=2647.0,
            ),
        ]
        updated, summary = calculate_pnl(
            positions,
            {"XAUUSDT": ba_quote},
            {"XAUUSD": mt5_quote},
            config,
            None,
        )
        ba = next(p for p in updated if p.platform == "BA")
        mt5 = next(p for p in updated if p.platform == "MT5")
        self.assertNotEqual(ba.unrealized_pnl, 0.0)
        self.assertNotEqual(mt5.unrealized_pnl, 0.0)
        self.assertNotEqual(summary.net_pnl, 0.0)


if __name__ == "__main__":
    unittest.main()
