"""Demo market paired quote tests."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.core.demo_market import (
    demo_tick_time,
    generate_all_demo_pairs,
    generate_demo_pair,
    spread_is_sane,
)
from app.core.models import AppConfig, ConnectionMode
from app.core.pnl_calculator import build_spread_snapshot
from app.connectors.binance_connector import BinanceConnector
from app.connectors.mt5_connector import MT5Connector


def test_demo_gold_spread_stays_realistic():
    spreads = []
    for step in range(300):
        t = demo_tick_time(step * 0.5, 0.8)
        ba, mt5 = generate_demo_pair("xau", t)
        snap = build_spread_snapshot(ba, mt5, "xau")
        assert snap is not None
        spreads.append(snap.mid_spread)
        assert spread_is_sane("xau", snap.mid_spread)
    assert min(spreads) >= 0.8
    assert max(spreads) <= 3.2


def test_demo_connectors_use_same_tick():
    cfg = AppConfig(connection_mode=ConnectionMode.DEMO.value, ba_refresh_interval_sec=0.8)
    ba = BinanceConnector(cfg)
    mt5 = MT5Connector(cfg)
    ba._emit_demo_quotes()
    mt5._emit_demo_quotes()
    qba = ba._quotes["XAUUSDT"]
    qmt = mt5._quotes["XAUUSD"]
    snap = build_spread_snapshot(qba, qmt, "xau")
    assert snap is not None
    assert spread_is_sane("xau", snap.mid_spread)
    assert 0.8 <= snap.mid_spread <= 3.2


def test_demo_all_pairs():
    pairs = generate_all_demo_pairs(demo_tick_time(100.0, 0.8))
    assert set(pairs) == {"xau", "xag"}
    for preset_id, (ba, mt5) in pairs.items():
        snap = build_spread_snapshot(ba, mt5, preset_id)
        assert snap is not None
        assert spread_is_sane(preset_id, snap.mid_spread)
