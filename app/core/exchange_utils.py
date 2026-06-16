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


# 常见交易所英文错误 → 中文说明（按子串匹配，命中即给出中文并附原文便于排查）
_EXCHANGE_ERROR_RULES: tuple[tuple[str, str], ...] = (
    # —— 币安合约 ——
    ("post only order will be rejected", "Maker(Post-Only)委托会立即吃单成交，已被拒绝（价格已穿过盘口，未挂成）"),
    ("could not be executed as maker", "无法以 Maker 方式成交（价格会立即吃单）"),
    ("reduceonly order is rejected", "只减仓(reduceOnly)委托被拒绝（无对应可减持仓）"),
    ("isolated balance insufficient", "逐仓可用保证金不足"),
    ("margin is insufficient", "保证金不足"),
    ("not enough margin", "保证金不足"),
    ("insufficient balance", "余额不足"),
    ("account has insufficient balance", "账户余额不足"),
    ("order's notional must be no smaller", "委托名义金额低于最小限制"),
    ("quantity less than", "数量小于最小下单量"),
    ("quantity greater than", "数量超过最大下单量"),
    ("price less than", "价格低于允许范围"),
    ("price greater than", "价格高于允许范围"),
    ("precision is over the maximum", "数量/价格精度超出限制"),
    ("order would immediately trigger", "委托会立即触发，已被拒绝"),
    ("position side does not match", "持仓方向(双向持仓模式)不匹配"),
    ("sign tradfi-perps agreement", "需先签署 TradFi-Perps 合约协议后才能交易"),
    ("timestamp for this request", "请求时间戳超出服务器时间窗（请校准本机系统时间）"),
    ("too many requests", "请求过于频繁（已被限频）"),
    ("way too many requests", "请求过于频繁（已被限频/封禁）"),
    ("market is closed", "该品种当前休市"),
    # —— MT5 / Exness ——
    ("unsupported filling mode", "不支持的成交模式"),
    ("autotrading disabled", "MT5 终端未开启自动交易(Algo Trading)"),
    ("no money", "账户资金不足"),
    ("not enough money", "账户资金不足"),
    ("invalid price", "价格无效"),
    ("invalid stops", "挂单/止损价距现价过近（无效）"),
    ("invalid volume", "下单手数无效"),
    ("requote", "报价已变动（requote）"),
    ("off quotes", "暂无可用报价（off quotes）"),
    ("price changed", "价格已变动"),
    ("trade timeout", "交易请求超时"),
    ("trade is disabled", "该品种禁止交易"),
    ("market closed", "该品种当前休市"),
    ("position not found", "未找到对应持仓"),
)


def translate_exchange_error(message: object) -> str:
    """把交易所返回的常见英文错误翻成中文。

    命中已知规则：返回「中文说明（原文：...）」，方便用户看懂同时保留原文排查；
    未命中：原样返回。
    """
    text = str(message) if message is not None else ""
    if not text:
        return text
    low = text.lower()
    for needle, zh in _EXCHANGE_ERROR_RULES:
        if needle in low:
            return f"{zh}（原文：{text}）"
    return text


def get_mt5_filling_mode(symbol_info) -> int:
    """按品种支持的成交模式选出下单用的 filling 类型。

    注意：symbol_info.filling_mode 是「品种支持模式」位掩码
    (SYMBOL_FILLING_FOK=1, SYMBOL_FILLING_IOC=2)，与下单用的 ORDER_FILLING_*
    枚举(FOK=0 / IOC=1 / RETURN=2)取值体系不同，二者不能混用做位与，
    否则会选到品种不支持的模式，导致 retcode=10030 Unsupported filling mode。
    """
    import MetaTrader5 as mt5

    supported = symbol_info.filling_mode
    fok_bit = getattr(mt5, "SYMBOL_FILLING_FOK", 1)
    ioc_bit = getattr(mt5, "SYMBOL_FILLING_IOC", 2)
    if supported & fok_bit:
        return mt5.ORDER_FILLING_FOK
    if supported & ioc_bit:
        return mt5.ORDER_FILLING_IOC
    return mt5.ORDER_FILLING_RETURN
