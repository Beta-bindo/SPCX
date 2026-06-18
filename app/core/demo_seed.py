"""Inject demo positions to preview hedge health alerts in 演示模式."""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.core.models import Position, Side
from app.core.symbols import find_preset

if TYPE_CHECKING:
    from app.connectors.binance_connector import BinanceConnector
    from app.connectors.mt5_connector import MT5Connector


def seed_hedge_alert_preview(binance: "BinanceConnector", mt5: "MT5Connector") -> str:
    """Load preview positions: gold BA-only alert, silver Ex-only alert."""
    xau = find_preset("xau")
    xag = find_preset("xag")

    binance.replace_demo_positions(
        [
            Position(
                platform="BA",
                symbol=xau.symbol_ba,
                side=Side.SELL,
                quantity=500.0,
                entry_price=xau.demo_ba_base,
                unrealized_pnl=18.6,
            ),
        ]
    )
    mt5.replace_demo_positions(
        [
            Position(
                platform="MT5",
                symbol=xag.symbol_mt5,
                side=Side.BUY,
                quantity=1.0,
                entry_price=xag.demo_mt5_base,
                unrealized_pnl=6.8,
            ),
        ]
    )
    return (
        "黄金：仅 BA 有仓（收缩单边 · 红色告警）\n"
        "SPCXUSDT：仅 Ex 有仓（扩张单边 · 红色告警）\n"
        "两边均可点「补对冲」打开预填交易窗口"
    )


def seed_hedge_alert_mixed(binance: "BinanceConnector", mt5: "MT5Connector") -> str:
    """Alternate preview: gold qty skew (yellow), silver side mismatch (red)."""
    xau = find_preset("xau")
    xag = find_preset("xag")

    binance.replace_demo_positions(
        [
            Position(
                platform="BA",
                symbol=xau.symbol_ba,
                side=Side.SELL,
                quantity=500.0,
                entry_price=xau.demo_ba_base,
                unrealized_pnl=12.0,
            ),
            Position(
                platform="BA",
                symbol=xag.symbol_ba,
                side=Side.SELL,
                quantity=5000.0,
                entry_price=xag.demo_ba_base,
                unrealized_pnl=-8.0,
            ),
        ]
    )
    mt5.replace_demo_positions(
        [
            Position(
                platform="MT5",
                symbol=xau.symbol_mt5,
                side=Side.BUY,
                quantity=0.35,
                entry_price=xau.demo_mt5_base,
                unrealized_pnl=5.2,
            ),
            Position(
                platform="MT5",
                symbol=xag.symbol_mt5,
                side=Side.SELL,
                quantity=1.0,
                entry_price=xag.demo_mt5_base,
                unrealized_pnl=3.1,
            ),
        ]
    )
    return (
        "黄金：对冲数量不齐（黄色告警）\n"
        "SPCXUSDT：对冲方向异常（红色告警）"
    )
