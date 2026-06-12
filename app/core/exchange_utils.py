from __future__ import annotations

import math


def _step(value: float, step: float) -> float:
    if step <= 0:
        return value
    precision = max(0, int(round(-math.log10(step)))) if step < 1 else 0
    adjusted = math.floor(value / step) * step
    return round(adjusted, precision)


def format_binance_qty(quantity: float, step_size: float) -> str:
    qty = max(step_size, _step(quantity, step_size))
    precision = max(0, int(round(-math.log10(step_size)))) if step_size < 1 else 8
    return f"{qty:.{precision}f}".rstrip("0").rstrip(".") or str(step_size)


_BINANCE_SYMBOL_META: dict[str, tuple[float, float]] = {}


def get_binance_symbol_meta(client, symbol: str) -> tuple[float, float]:
    """Return (lot_step, price_tick) with in-process cache."""
    cached = _BINANCE_SYMBOL_META.get(symbol)
    if cached is not None:
        return cached
    info = client.futures_exchange_info()
    lot_step = 0.001
    price_tick = 0.01
    for item in info.get("symbols", []):
        if item.get("symbol") != symbol:
            continue
        for filt in item.get("filters", []):
            if filt.get("filterType") == "LOT_SIZE":
                lot_step = float(filt.get("stepSize", lot_step))
            elif filt.get("filterType") == "PRICE_FILTER":
                price_tick = float(filt.get("tickSize", price_tick))
        break
    _BINANCE_SYMBOL_META[symbol] = (lot_step, price_tick)
    return lot_step, price_tick


def get_binance_lot_step(client, symbol: str) -> float:
    return get_binance_symbol_meta(client, symbol)[0]


def get_binance_price_tick(client, symbol: str) -> float:
    return get_binance_symbol_meta(client, symbol)[1]


def read_binance_symbol_leverage(client, symbol: str) -> int | None:
    try:
        for pos in client.futures_position_information(symbol=symbol):
            lev = int(float(pos.get("leverage", 0) or 0))
            if lev > 0:
                return lev
    except Exception:
        return None
    return None


def format_binance_price(price: float, tick_size: float) -> str:
    px = _step(price, tick_size)
    precision = max(0, int(round(-math.log10(tick_size)))) if tick_size < 1 else 2
    return f"{px:.{precision}f}"


def get_mt5_filling_mode(symbol_info) -> int:
    import MetaTrader5 as mt5

    filling = symbol_info.filling_mode
    if filling & mt5.ORDER_FILLING_FOK:
        return mt5.ORDER_FILLING_FOK
    if filling & mt5.ORDER_FILLING_IOC:
        return mt5.ORDER_FILLING_IOC
    return mt5.ORDER_FILLING_RETURN
