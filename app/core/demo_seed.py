"""Inject demo positions to preview hedge health alerts in 演示模式."""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.core.models import Position, Side
from app.core.symbols import active_preset_ids, find_preset, preset_display_name

if TYPE_CHECKING:
    from app.connectors.binance_connector import BinanceConnector
    from app.connectors.mt5_connector import MT5Connector


def seed_hedge_alert_preview(binance: "BinanceConnector", mt5: "MT5Connector") -> str:
    """Load preview positions for the currently selected symbols."""
    preset_ids = active_preset_ids()
    first_id = preset_ids[0] if preset_ids else "xau"
    second_id = preset_ids[1] if len(preset_ids) > 1 else None
    first = find_preset(first_id)
    second = find_preset(second_id) if second_id else None

    ba_positions = [
        Position(
            platform="BA",
            symbol=first.symbol_ba,
            side=Side.SELL,
            quantity=500.0,
            entry_price=first.demo_ba_base,
            unrealized_pnl=18.6,
        )
    ]
    mt5_positions = (
        [
            Position(
                platform="MT5",
                symbol=second.symbol_mt5,
                side=Side.BUY,
                quantity=1.0,
                entry_price=second.demo_mt5_base,
                unrealized_pnl=6.8,
            )
        ]
        if second is not None
        else []
    )
    binance.replace_demo_positions(ba_positions)
    mt5.replace_demo_positions(mt5_positions)
    lines = [
        f"{preset_display_name(first_id)}：仅 BA 有仓（收缩单边 · 红色告警）",
    ]
    if second_id:
        lines.append(f"{preset_display_name(second_id)}：仅 Ex 有仓（扩张单边 · 红色告警）")
    lines.append("可点「补对冲」打开预填交易窗口")
    return "\n".join(lines)


def seed_hedge_alert_mixed(binance: "BinanceConnector", mt5: "MT5Connector") -> str:
    """Alternate preview: qty skew on first selected symbol, side mismatch on second."""
    preset_ids = active_preset_ids()
    first_id = preset_ids[0] if preset_ids else "xau"
    second_id = preset_ids[1] if len(preset_ids) > 1 else None
    first = find_preset(first_id)
    second = find_preset(second_id) if second_id else None

    ba_positions = [
        Position(
            platform="BA",
            symbol=first.symbol_ba,
            side=Side.SELL,
            quantity=500.0,
            entry_price=first.demo_ba_base,
            unrealized_pnl=12.0,
        )
    ]
    mt5_positions = [
        Position(
            platform="MT5",
            symbol=first.symbol_mt5,
            side=Side.BUY,
            quantity=0.35,
            entry_price=first.demo_mt5_base,
            unrealized_pnl=5.2,
        )
    ]
    if second is not None:
        ba_positions.append(
            Position(
                platform="BA",
                symbol=second.symbol_ba,
                side=Side.SELL,
                quantity=5000.0,
                entry_price=second.demo_ba_base,
                unrealized_pnl=-8.0,
            )
        )
        mt5_positions.append(
            Position(
                platform="MT5",
                symbol=second.symbol_mt5,
                side=Side.SELL,
                quantity=1.0,
                entry_price=second.demo_mt5_base,
                unrealized_pnl=3.1,
            )
        )
    binance.replace_demo_positions(ba_positions)
    mt5.replace_demo_positions(mt5_positions)
    lines = [
        f"{preset_display_name(first_id)}：对冲数量不齐（黄色告警）",
    ]
    if second_id:
        lines.append(f"{preset_display_name(second_id)}：对冲方向异常（红色告警）")
    return "\n".join(lines)
