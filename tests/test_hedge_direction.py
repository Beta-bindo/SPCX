"""Verify BA/Ex hedge directions for all presets and order modes."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.connectors.binance_connector import BinanceConnector
from app.connectors.mt5_connector import MT5Connector
from app.core.models import AppConfig, ConnectionMode, GoldOrderMode, HedgeMode, Quote, Side
from app.core.trade_anchor import hedge_sides
from app.core.trading_service import close_hedge, detect_hedge_mode, open_hedge


def _setup_demo(preset_id: str) -> tuple[BinanceConnector, MT5Connector]:
    cfg = AppConfig(connection_mode=ConnectionMode.DEMO.value)
    ba = BinanceConnector(cfg)
    mt5 = MT5Connector(cfg)
    if preset_id == "xau":
        ba._quotes["XAUUSDT"] = Quote("XAUUSDT", 2650, 2650.2, is_simulated=True)
        mt5._quotes["XAUUSD"] = Quote("XAUUSD", 2649, 2649.2, is_simulated=True)
    else:
        ba._quotes["XAGUSDT"] = Quote("XAGUSDT", 30, 30.02, is_simulated=True)
        mt5._quotes["XAGUSD"] = Quote("XAGUSD", 29.9, 30.1, is_simulated=True)
    return ba, mt5


def _expected_sides(mode: str) -> tuple[Side, Side]:
    ba_side, mt5_side = hedge_sides(mode)
    return Side(ba_side), Side(mt5_side)


def test_hedge_sides_mapping():
    assert hedge_sides(HedgeMode.CONTRACTION.value) == ("SELL", "BUY")
    assert hedge_sides(HedgeMode.EXPANSION.value) == ("BUY", "SELL")


def test_open_hedge_direction_matrix():
    order_modes = (
        GoldOrderMode.MARKET.value,
        GoldOrderMode.MAKER.value,
        GoldOrderMode.LIMIT.value,
    )
    for preset_id in ("xau", "xag"):
        for mode in (HedgeMode.CONTRACTION.value, HedgeMode.EXPANSION.value):
            exp_ba, exp_mt5 = _expected_sides(mode)
            for order_mode in order_modes:
                ba, mt5 = _setup_demo(preset_id)
                result = open_hedge(ba, mt5, preset_id, mode, order_mode)
                assert result.success, (
                    f"{preset_id}/{mode}/{order_mode}: {result.message}"
                )
                ba_pos = ba.get_positions()[0]
                mt5_pos = mt5.get_positions()[0]
                assert ba_pos.side == exp_ba, (
                    f"{preset_id}/{mode}/{order_mode} BA got {ba_pos.side}, want {exp_ba}"
                )
                assert mt5_pos.side == exp_mt5, (
                    f"{preset_id}/{mode}/{order_mode} Ex got {mt5_pos.side}, want {exp_mt5}"
                )
                assert detect_hedge_mode(preset_id, ba.get_positions() + mt5.get_positions()) == mode
                close = close_hedge(ba, mt5, preset_id, mode, order_mode)
                assert close.success
                assert not ba.get_positions() and not mt5.get_positions()


def test_expansion_is_opposite_of_contraction():
    """BA 扩张时 Ex 必为收缩方向，反之亦然。"""
    for preset_id in ("xau", "xag"):
        ba_c, mt5_c = _setup_demo(preset_id)
        open_hedge(ba_c, mt5_c, preset_id, HedgeMode.CONTRACTION.value)
        ba_side_c = ba_c.get_positions()[0].side
        ex_side_c = mt5_c.get_positions()[0].side

        ba_e, mt5_e = _setup_demo(preset_id)
        open_hedge(ba_e, mt5_e, preset_id, HedgeMode.EXPANSION.value)
        ba_side_e = ba_e.get_positions()[0].side
        ex_side_e = mt5_e.get_positions()[0].side

        assert ba_side_c != ba_side_e
        assert ex_side_c != ex_side_e
        assert ba_side_c == ex_side_e or ba_side_e == ex_side_c  # opposite legs


if __name__ == "__main__":
    test_hedge_sides_mapping()
    test_open_hedge_direction_matrix()
    test_expansion_is_opposite_of_contraction()
    print("ALL HEDGE DIRECTION TESTS PASSED")
