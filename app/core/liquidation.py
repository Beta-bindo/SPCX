"""Exchange-aligned liquidation price and buffer helpers."""

from __future__ import annotations

from collections.abc import Callable

from app.core.models import Position, Quote, Side
from app.core.symbols import find_preset


def ba_isolated_liq_buffer(
    isolated_wallet: float, unrealized_pnl: float, maint_margin: float
) -> float:
    """Binance isolated: liquidate when wallet + PnL <= maintMargin."""
    return max(0.0, isolated_wallet + unrealized_pnl - maint_margin)


def ba_cross_account_liq_buffer(margin_balance: float, maint_margin: float) -> float:
    """Binance cross: totalMarginBalance - totalMaintMargin."""
    return max(0.0, margin_balance - maint_margin)


def liq_buffer_from_prices(
    side: Side,
    mark: float,
    liquidation_price: float,
    quantity: float,
    *,
    qty_unit: float = 1.0,
) -> float:
    """USDT distance to liquidation from exchange liq price and mark."""
    if liquidation_price <= 0 or mark <= 0 or quantity <= 0:
        return float("inf")
    qty = abs(quantity) * qty_unit
    if side == Side.BUY:
        return max(0.0, (mark - liquidation_price) * qty)
    if side == Side.SELL:
        return max(0.0, (liquidation_price - mark) * qty)
    return float("inf")


def mt5_stopout_equity(margin: float, stop_out_pct: float) -> float:
    return margin * (stop_out_pct / 100.0)


def mt5_account_liq_buffer(equity: float, margin: float, stop_out_pct: float) -> float:
    """Ex/MT5 account buffer until stop-out (margin_so_so)."""
    return max(0.0, equity - mt5_stopout_equity(margin, stop_out_pct))


def calc_liquidation_price_from_profit(
    side: Side,
    entry_price: float,
    equity_without_position: float,
    margin: float,
    stop_out_pct: float,
    profit_calc: Callable[[float], float],
    *,
    max_iterations: int = 64,
) -> float:
    """Binary-search close price where account equity hits MT5 stop-out level."""
    if entry_price <= 0 or margin <= 0:
        return 0.0
    target_equity = mt5_stopout_equity(margin, stop_out_pct)

    def equity_at(close_price: float) -> float:
        return equity_without_position + profit_calc(close_price)

    if side == Side.BUY:
        lo = max(entry_price * 0.01, 1e-6)
        hi = entry_price
        if equity_at(hi) <= target_equity:
            return round(hi, 3)
        if equity_at(lo) > target_equity:
            return round(lo, 3)
        for _ in range(max_iterations):
            mid = (lo + hi) / 2.0
            if equity_at(mid) > target_equity:
                hi = mid
            else:
                lo = mid
            if abs(hi - lo) < 1e-4:
                break
        return round(hi, 3)

    if side == Side.SELL:
        lo = entry_price
        hi = entry_price * 3.0
        if equity_at(lo) <= target_equity:
            return round(lo, 3)
        if equity_at(hi) > target_equity:
            return round(hi, 3)
        for _ in range(max_iterations):
            mid = (lo + hi) / 2.0
            if equity_at(mid) > target_equity:
                lo = mid
            else:
                hi = mid
            if abs(hi - lo) < 1e-4:
                break
        return round(lo, 3)
    return 0.0


def estimate_liquidation_price(entry: float, side: Side, leverage: int, mmr: float = 0.004) -> float:
    """Demo / fallback when exchange liq price unavailable."""
    if entry <= 0 or leverage <= 0 or side == Side.NONE:
        return 0.0
    if side == Side.BUY:
        return entry * (1 - 1 / leverage + mmr)
    return entry * (1 + 1 / leverage - mmr)


def resolve_mark_price(pos: Position, quote: Quote | None) -> float:
    if pos.mark_price > 0:
        return pos.mark_price
    if quote is None:
        return 0.0
    if pos.side == Side.SELL:
        return quote.ask if quote.ask > 0 else quote.mid
    if pos.side == Side.BUY:
        return quote.bid if quote.bid > 0 else quote.mid
    return quote.mid


def resolve_position_liq_buffer(
    pos: Position,
    quote: Quote | None,
    preset_id: str,
    leverage: int,
) -> float:
    """Prefer exchange buffer, then liq-price distance, then legacy margin estimate."""
    if pos.exchange_liq_buffer is not None:
        return max(0.0, pos.exchange_liq_buffer)

    preset = find_preset(preset_id)
    mark = resolve_mark_price(pos, quote)
    if pos.liquidation_price > 0 and mark > 0:
        unit = preset.ba_qty_unit if pos.platform == "BA" else preset.mt5_oz_per_lot
        buf = liq_buffer_from_prices(
            pos.side,
            mark,
            pos.liquidation_price,
            pos.quantity,
            qty_unit=unit,
        )
        if buf != float("inf"):
            return buf

    if not quote or quote.mid <= 0:
        return float("inf")

    notional = pos.entry_price * pos.quantity * (
        preset.ba_qty_unit if pos.platform == "BA" else preset.mt5_oz_per_lot
    )
    margin = notional / max(leverage, 1)
    if pos.platform == "BA":
        mark_px = quote.ask if pos.side == Side.SELL else quote.bid
        unit = preset.ba_qty_unit
    else:
        mark_px = quote.bid if pos.side == Side.BUY else quote.ask
        unit = preset.mt5_oz_per_lot
    if mark_px <= 0:
        return float("inf")
    if pos.side == Side.SELL:
        unrealized = (pos.entry_price - mark_px) * pos.quantity * unit
    elif pos.side == Side.BUY:
        unrealized = (mark_px - pos.entry_price) * pos.quantity * unit
    else:
        unrealized = 0.0
    return max(0.0, margin + unrealized)


def resolve_position_liquidation_price(
    pos: Position,
    leverage: int,
) -> float:
    if pos.liquidation_price > 0:
        return pos.liquidation_price
    lev = pos.leverage if pos.leverage > 0 else leverage
    return estimate_liquidation_price(pos.entry_price, pos.side, lev)
