from __future__ import annotations

from dataclasses import dataclass

from app.core.models import AppConfig, Position, Quote, Side, SpreadSnapshot
from app.core.symbols import find_preset, preset_for_ba_symbol


@dataclass
class PnlSummary:
    ba_pnl: float = 0.0
    mt5_pnl: float = 0.0
    gross_pnl: float = 0.0
    ba_fee: float = 0.0
    mt5_fee: float = 0.0
    total_fees: float = 0.0
    net_pnl: float = 0.0
    exec_spread: float = 0.0
    mid_spread: float = 0.0


def build_spread_snapshot(ba: Quote, mt5: Quote, preset_id: str = "xau") -> SpreadSnapshot | None:
    if ba.bid <= 0 or ba.ask <= 0 or mt5.bid <= 0 or mt5.ask <= 0:
        return None
    ba_mid = (ba.bid + ba.ask) / 2
    mt5_mid = (mt5.bid + mt5.ask) / 2
    is_simulated = ba.is_simulated or mt5.is_simulated
    return SpreadSnapshot(
        preset_id=preset_id,
        ba_bid=ba.bid,
        ba_ask=ba.ask,
        mt5_bid=mt5.bid,
        mt5_ask=mt5.ask,
        ba_mid=ba_mid,
        mt5_mid=mt5_mid,
        mid_spread=ba_mid - mt5_mid,
        exec_spread=ba.bid - mt5.ask,
        ba_platform_spread=ba.ask - ba.bid,
        mt5_platform_spread=mt5.ask - mt5.bid,
        is_simulated=is_simulated,
        timestamp=ba.timestamp or mt5.timestamp,
    )


def _mark_price(side: Side, quote: Quote) -> float:
    if side == Side.BUY:
        return quote.bid
    if side == Side.SELL:
        return quote.ask
    return quote.mid


def _position_pnl(position: Position, mark: float, multiplier: float) -> float:
    if mark <= 0:
        return position.unrealized_pnl
    if position.side == Side.BUY:
        return (mark - position.entry_price) * position.quantity * multiplier
    if position.side == Side.SELL:
        return (position.entry_price - mark) * position.quantity * multiplier
    return 0.0


def _use_exchange_pnl(platform: str, config: AppConfig) -> bool:
    if platform == "BA":
        return config.use_live_ba
    if platform == "MT5":
        return config.use_live_mt5
    return False


def _estimate_ba_fee(notional: float, fee_rate: float, legs: int = 1) -> float:
    return notional * fee_rate * legs


def _estimate_mt5_fee(
    lots: float,
    commission_per_lot: float,
    spread_points: float,
    point_value: float,
    legs: int = 2,
) -> float:
    commission = lots * commission_per_lot * legs
    spread_cost = lots * spread_points * point_value
    return commission + spread_cost


def _fee_legs_for_display() -> int:
    """Realtime panel shows estimated close cost (single leg), not round-trip."""
    return 1


def _preset_for_position(pos: Position) -> str:
    if pos.platform == "BA":
        return preset_for_ba_symbol(pos.symbol)
    for pid in ("xau", "xag"):
        preset = find_preset(pid)
        if preset.symbol_mt5 == pos.symbol:
            return pid
    return "xau"


def _quote_ready(quote: Quote) -> bool:
    return quote.bid > 0 and quote.ask > 0


def calculate_pnl(
    positions: list[Position],
    ba_quotes: dict[str, Quote],
    mt5_quotes: dict[str, Quote],
    config: AppConfig,
    primary_spread: SpreadSnapshot | None,
) -> tuple[list[Position], PnlSummary]:
    updated: list[Position] = []
    summary = PnlSummary()
    if primary_spread:
        summary.exec_spread = primary_spread.exec_spread
        summary.mid_spread = primary_spread.mid_spread

    for pos in positions:
        preset_id = _preset_for_position(pos)
        preset = find_preset(preset_id)
        if pos.platform == "BA":
            quote = ba_quotes.get(pos.symbol, Quote(symbol=pos.symbol))
            mark = _mark_price(pos.side, quote)
            multiplier = preset.ba_qty_unit
            if _use_exchange_pnl("BA", config) and not _quote_ready(quote):
                pnl = pos.unrealized_pnl
            else:
                pnl = _position_pnl(pos, mark, multiplier)
            price_for_fee = mark if mark > 0 else pos.entry_price
            notional = price_for_fee * pos.quantity * multiplier
            pos.current_price = mark if mark > 0 else pos.entry_price
            pos.unrealized_pnl = round(pnl, 2)
            pos.estimated_fee = round(
                _estimate_ba_fee(notional, config.ba_fee_rate, _fee_legs_for_display()),
                4,
            )
            summary.ba_pnl += pos.unrealized_pnl
            summary.ba_fee += pos.estimated_fee
        elif pos.platform == "MT5":
            quote = mt5_quotes.get(pos.symbol, Quote(symbol=pos.symbol))
            mark = _mark_price(pos.side, quote)
            multiplier = preset.mt5_oz_per_lot
            if _use_exchange_pnl("MT5", config) and not _quote_ready(quote):
                pnl = pos.unrealized_pnl
            else:
                pnl = _position_pnl(pos, mark, multiplier)
            pos.current_price = mark if mark > 0 else pos.entry_price
            pos.unrealized_pnl = round(pnl, 2)
            pos.estimated_fee = round(
                _estimate_mt5_fee(
                    pos.quantity,
                    config.mt5_commission_per_lot,
                    config.mt5_spread_points,
                    config.mt5_point_value,
                    legs=_fee_legs_for_display(),
                ),
                4,
            )
            summary.mt5_pnl += pos.unrealized_pnl
            summary.mt5_fee += pos.estimated_fee
        updated.append(pos)

    summary.gross_pnl = round(summary.ba_pnl + summary.mt5_pnl, 2)
    summary.total_fees = round(summary.ba_fee + summary.mt5_fee, 4)
    summary.net_pnl = round(summary.gross_pnl - summary.total_fees, 2)
    summary.ba_pnl = round(summary.ba_pnl, 2)
    summary.mt5_pnl = round(summary.mt5_pnl, 2)
    summary.ba_fee = round(summary.ba_fee, 4)
    summary.mt5_fee = round(summary.mt5_fee, 4)
    return updated, summary
