"""Tests for hedge exposure detection."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.core.hedge_health import (
    analyze_hedge_health,
    combine_hedge_health,
    format_hedge_banner,
    suggest_hedge_repair,
)
from app.core.models import AppConfig, GoldOrderMode, HedgeMode, Position, Side


class HedgeHealthTests(unittest.TestCase):
    def test_ba_only_is_alert(self):
        health = analyze_hedge_health(
            "xau",
            [
                Position(
                    platform="BA",
                    symbol="XAUUSDT",
                    side=Side.SELL,
                    quantity=500.0,
                    entry_price=2650.0,
                )
            ],
        )
        self.assertEqual(health.level, "alert")
        self.assertEqual(health.code, "ba_only")
        self.assertIn("仅 BA", health.title)
        self.assertIn("Ex", health.action)

    def test_hedged_contraction_is_ok(self):
        health = analyze_hedge_health(
            "xau",
            [
                Position(platform="BA", symbol="XAUUSDT", side=Side.SELL, quantity=500.0),
                Position(platform="MT5", symbol="XAUUSD", side=Side.BUY, quantity=1.0),
            ],
            AppConfig(),
        )
        self.assertTrue(health.is_ok)
        self.assertEqual(health.code, "hedged")

    def test_side_mismatch_is_alert(self):
        health = analyze_hedge_health(
            "xau",
            [
                Position(platform="BA", symbol="XAUUSDT", side=Side.SELL, quantity=500.0),
                Position(platform="MT5", symbol="XAUUSD", side=Side.SELL, quantity=1.0),
            ],
        )
        self.assertEqual(health.code, "side_mismatch")

    def test_combine_prefers_alert(self):
        ok = analyze_hedge_health("xag", [])
        bad = analyze_hedge_health(
            "xau",
            [Position(platform="BA", symbol="XAUUSDT", side=Side.SELL, quantity=500.0)],
        )
        combined = combine_hedge_health(ok, bad)
        self.assertEqual(combined.code, "ba_only")
        self.assertTrue(format_hedge_banner(combined))

    def test_suggest_repair_for_ba_only(self):
        positions = [
            Position(
                platform="BA",
                symbol="XAUUSDT",
                side=Side.SELL,
                quantity=500.0,
                entry_price=2650.0,
            )
        ]
        health = analyze_hedge_health("xau", positions)
        repair = suggest_hedge_repair("xau", positions, health)
        self.assertIsNotNone(repair)
        self.assertEqual(repair.mode, HedgeMode.CONTRACTION.value)
        self.assertEqual(repair.order_mode, GoldOrderMode.MARKET.value)

    def test_suggest_repair_none_when_ok(self):
        health = analyze_hedge_health(
            "xau",
            [
                Position(platform="BA", symbol="XAUUSDT", side=Side.SELL, quantity=500.0),
                Position(platform="MT5", symbol="XAUUSD", side=Side.BUY, quantity=1.0),
            ],
            AppConfig(),
        )
        self.assertIsNone(suggest_hedge_repair("xau", [], health))


if __name__ == "__main__":
    unittest.main()
