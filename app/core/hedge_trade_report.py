"""对冲成交报表：统一利润计算器展示与运营上报字段。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time as dt_time, timedelta
from typing import Any

from app.core.models import AppConfig
from app.core.symbols import find_preset


BA_REBATE_TYPES = {"COMMISSION_REBATE", "API_REBATE", "FEE_RETURN"}
PAIR_WINDOW_MS = 120_000

FIELD_ORDER = [
    "ba_order_no",
    "ex_order_no",
    "product",
    "direction",
    "ba_qty",
    "ex_qty",
    "ba_open_price",
    "ba_close_price",
    "ba_pnl",
    "ex_open_price",
    "ex_close_price",
    "ba_charges",
    "ba_commission",
    "order_time",
    "net_profit",
]

FIELD_LABELS = {
    "ba_order_no": "BA订单号",
    "ex_order_no": "EX订单号",
    "product": "产品",
    "direction": "方向",
    "ba_qty": "BA数量",
    "ex_qty": "EX数量",
    "ba_open_price": "BA开仓成交价",
    "ba_close_price": "BA平仓成交价",
    "ba_pnl": "BA盈亏",
    "ex_open_price": "EX开仓成交价",
    "ex_close_price": "EX平仓成交价",
    "ba_charges": "BA资费",
    "ba_commission": "BA手续费",
    "order_time": "下单时间",
    "net_profit": "净利润",
}


def _dash(value: Any) -> str:
    if value is None:
        return "--"
    if isinstance(value, str):
        text = value.strip()
        return text if text else "--"
    if isinstance(value, (int, float)):
        return f"{value:g}"
    return str(value)


def _money(value: float | None) -> str:
    if value is None:
        return "--"
    return f"{value:+.4f}"


def _price(value: float | None) -> str:
    if value is None or value <= 0:
        return "--"
    return f"{value:.4f}"


def _as_float(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    text = _as_text(value).strip().lower()
    return text in {"1", "true", "yes", "y"}


def _local_range(start: date, end: date) -> tuple[datetime, datetime]:
    start_dt = datetime.combine(start, dt_time.min)
    end_dt = datetime.combine(end + timedelta(days=1), dt_time.min)
    return start_dt, end_dt


def _format_ms(ms: int) -> str:
    if ms <= 0:
        return ""
    return datetime.fromtimestamp(ms / 1000).strftime("%Y-%m-%d %H:%M:%S")


def _product_for_ba_symbol(symbol: str) -> str:
    for preset_id in ("xau", "xag"):
        preset = find_preset(preset_id)
        if preset.symbol_ba == symbol:
            return "黄金" if preset_id == "xau" else "白银"
    return ""


def _product_for_mt5_symbol(symbol: str) -> str:
    for preset_id in ("xau", "xag"):
        preset = find_preset(preset_id)
        if preset.symbol_mt5 == symbol:
            return "黄金" if preset_id == "xau" else "白银"
    return ""


def _symbols_for_filter(symbol_filter: str) -> tuple[list[str], list[str]]:
    presets = ("xau", "xag") if symbol_filter == "all" else (symbol_filter,)
    ba_symbols: list[str] = []
    mt5_symbols: list[str] = []
    for preset_id in presets:
        if preset_id not in ("xau", "xag"):
            continue
        preset = find_preset(preset_id)
        ba_symbols.append(preset.symbol_ba)
        mt5_symbols.append(preset.symbol_mt5)
    return ba_symbols, mt5_symbols


def _mode_label(mode: str) -> str:
    if mode == "contraction":
        return "收缩"
    if mode == "expansion":
        return "扩张"
    return ""


def _infer_direction_from_ba_side(side: str, *, is_close: bool = False) -> str:
    side = side.upper()
    if side == ("BUY" if is_close else "SELL"):
        return "收缩"
    if side == ("SELL" if is_close else "BUY"):
        return "扩张"
    return "--"


def _order_map_key(raw: dict) -> tuple[str, str]:
    return _as_text(raw.get("symbol")), _as_text(raw.get("orderId") or raw.get("order"))


def _weighted_price(rows: list[dict], price_key: str, qty_key: str) -> float:
    qty_total = 0.0
    notional = 0.0
    for raw in rows:
        qty = abs(_as_float(raw.get(qty_key)))
        price = _as_float(raw.get(price_key))
        if qty <= 0 or price <= 0:
            continue
        qty_total += qty
        notional += price * qty
    if qty_total <= 0:
        return 0.0
    return notional / qty_total


def _ba_trade_order_price(rows: list[dict], order: dict | None) -> float:
    if order:
        avg = _as_float(order.get("avgPrice"))
        if avg > 0:
            return avg
    qty_total = sum(abs(_as_float(raw.get("qty"))) for raw in rows)
    quote_total = sum(abs(_as_float(raw.get("quoteQty"))) for raw in rows)
    if qty_total > 0 and quote_total > 0:
        return quote_total / qty_total
    return _weighted_price(rows, "price", "qty")


def _ba_order_is_close(order: dict | None, realized_pnl: float) -> bool:
    if order is not None and "reduceOnly" in order:
        return _as_bool(order.get("reduceOnly"))
    return abs(realized_pnl) > 1e-12


def _mt5_entry_is_close(entry: Any) -> bool:
    text = _as_text(entry).upper()
    if text in {"1", "2", "3", "OUT", "INOUT", "OUT_BY"}:
        return True
    return False


def _aggregate_ba_trade_orders(
    trade_rows: list[dict],
    order_rows: list[dict],
) -> list[tuple[dict, int, str]]:
    orders = {_order_map_key(raw): raw for raw in order_rows}
    grouped: dict[tuple[str, str], list[dict]] = {}
    for raw in trade_rows:
        key = _order_map_key(raw)
        if not key[1]:
            key = (key[0], _as_text(raw.get("id")))
        grouped.setdefault(key, []).append(raw)

    out: list[tuple[dict, int, str]] = []
    for key, rows in grouped.items():
        first = rows[0]
        order = orders.get(key)
        qty = sum(abs(_as_float(raw.get("qty"))) for raw in rows)
        realized = sum(_as_float(raw.get("realizedPnl")) for raw in rows)
        commission = sum(_as_float(raw.get("commission")) for raw in rows)
        quote_qty = sum(abs(_as_float(raw.get("quoteQty"))) for raw in rows)
        price = _ba_trade_order_price(rows, order)
        ms = max(int(_as_float(raw.get("time"))) for raw in rows)
        product = _product_for_ba_symbol(_as_text(first.get("symbol")))
        raw = {
            **first,
            "orderId": key[1],
            "qty": qty,
            "quoteQty": quote_qty,
            "price": price,
            "realizedPnl": realized,
            "commission": commission,
            "time": ms,
            "_report_is_close": _ba_order_is_close(order, realized),
        }
        if order:
            for name in (
                "avgPrice",
                "origQty",
                "executedQty",
                "type",
                "origType",
                "side",
                "reduceOnly",
                "timeInForce",
                "status",
                "updateTime",
            ):
                if name in order:
                    raw[name] = order[name]
        out.append((raw, ms, product))
    return out


def _aggregate_mt5_deal_orders(rows: list[dict]) -> list[tuple[dict, int, str]]:
    grouped: dict[tuple[str, str], list[dict]] = {}
    for raw in rows:
        key = (_as_text(raw.get("symbol")), _as_text(raw.get("order") or raw.get("ticket")))
        grouped.setdefault(key, []).append(raw)

    out: list[tuple[dict, int, str]] = []
    for _key, deals in grouped.items():
        first = deals[0]
        volume = sum(abs(_as_float(raw.get("volume"))) for raw in deals)
        profit = sum(_as_float(raw.get("profit")) for raw in deals)
        commission = sum(_as_float(raw.get("commission")) for raw in deals)
        fee = sum(_as_float(raw.get("fee")) for raw in deals)
        swap = sum(_as_float(raw.get("swap")) for raw in deals)
        price = _weighted_price(deals, "price", "volume")
        ms = max(
            int(_as_float(raw.get("time_msc"))) or int(_as_float(raw.get("time")) * 1000)
            for raw in deals
        )
        product = _product_for_mt5_symbol(_as_text(first.get("symbol")))
        raw = {
            **first,
            "volume": volume,
            "price": price,
            "profit": profit,
            "commission": commission,
            "fee": fee,
            "swap": swap,
            "time_msc": ms,
            "_report_is_close": _mt5_entry_is_close(first.get("entry")),
        }
        out.append((raw, ms, product))
    return out


@dataclass
class HedgeTradeRow:
    """一行对冲成交明细（计算器与运营上报共用）。"""

    ba_order_no: str = ""
    ex_order_no: str = ""
    product: str = ""
    direction: str = ""
    ba_qty: str = ""
    ex_qty: str = ""
    ba_open_price: str = ""
    ba_close_price: str = ""
    ba_pnl: str = ""
    ex_open_price: str = ""
    ex_close_price: str = ""
    ba_charges: str = ""
    ba_commission: str = ""
    order_time: str = ""
    net_profit: str = ""
    sort_ms: int = 0
    record_key: str = ""

    def values(self, headers: list[str] | None = None) -> list[str]:
        keys = headers or FIELD_ORDER
        data = {key: _dash(getattr(self, key, "")) for key in FIELD_ORDER}
        return [data.get(key, "--") for key in keys]

    def to_payload(self) -> dict:
        """运营上报 payload（不含用户/机器码，由服务端写入 device_id）。"""
        return {
            "ba_order_no": self.ba_order_no or "",
            "ex_order_no": self.ex_order_no or "",
            "product": self.product or "",
            "direction": self.direction or "",
            "ba_qty": self.ba_qty or "",
            "ex_qty": self.ex_qty or "",
            "ba_open_price": self.ba_open_price or "",
            "ba_close_price": self.ba_close_price or "",
            "ba_pnl": self.ba_pnl or "",
            "ex_open_price": self.ex_open_price or "",
            "ex_close_price": self.ex_close_price or "",
            "ba_charges": self.ba_charges or "",
            "ba_commission": self.ba_commission or "",
            "order_time": self.order_time or "",
            "net_profit": self.net_profit or "",
            "record_key": self.record_key or "",
        }


@dataclass
class HedgeTradeReport:
    rows: list[HedgeTradeRow] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def headers(self) -> list[str]:
        return list(FIELD_ORDER)

    @property
    def row_count(self) -> int:
        return len(self.rows)

    @property
    def ba_pnl(self) -> float:
        total = 0.0
        for row in self.rows:
            if row.ba_pnl and row.ba_pnl != "--":
                total += _as_float(row.ba_pnl)
        return round(total, 2)

    @property
    def ex_pnl(self) -> float:
        total = 0.0
        for row in self.rows:
            ex_part = 0.0
            if row.net_profit and row.net_profit != "--":
                ex_part = _as_float(row.net_profit) - _as_float(row.ba_pnl)
            total += ex_part
        return round(total, 2)

    @property
    def ba_commission(self) -> float:
        total = 0.0
        for row in self.rows:
            if row.ba_commission and row.ba_commission != "--":
                total += abs(_as_float(row.ba_commission))
        return round(total, 4)

    @property
    def ba_charges_total(self) -> float:
        total = 0.0
        for row in self.rows:
            if row.ba_charges and row.ba_charges != "--":
                total += _as_float(row.ba_charges)
        return round(total, 4)

    @property
    def total_pnl(self) -> float:
        total = 0.0
        for row in self.rows:
            if row.net_profit and row.net_profit != "--":
                total += _as_float(row.net_profit)
        return round(total, 2)


def _record_key(
    ba_order_no: str,
    ex_order_no: str,
    order_time: str,
    *,
    suffix: str = "",
) -> str:
    parts = [ba_order_no or "", ex_order_no or "", order_time or "", suffix]
    return "|".join(parts)


def build_row_from_settlement(
    *,
    preset_id: str,
    mode: str,
    action: str,
    ba_order_no: str = "",
    ex_order_no: str = "",
    ba_qty: float = 0.0,
    ex_qty: float = 0.0,
    ba_open_price: float | None = None,
    ba_close_price: float | None = None,
    ex_open_price: float | None = None,
    ex_close_price: float | None = None,
    ba_pnl: float | None = None,
    ex_pnl: float | None = None,
    ba_charges: float | None = None,
    ba_commission: float | None = None,
    order_time: str | None = None,
) -> HedgeTradeRow:
    """由实盘成交结算上下文构造一行（字段取得到就填，否则 --）。"""
    product = "黄金" if preset_id == "xau" else "白银" if preset_id == "xag" else ""
    direction = _mode_label(mode) or "--"
    settled = order_time or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    sort_ms = 0
    try:
        sort_ms = int(datetime.fromisoformat(settled.replace(" ", "T")).timestamp() * 1000)
    except (TypeError, ValueError):
        sort_ms = int(datetime.now().timestamp() * 1000)

    ba_open = _price(ba_open_price) if ba_open_price is not None else "--"
    ba_close = _price(ba_close_price) if ba_close_price is not None else "--"
    ex_open = _price(ex_open_price) if ex_open_price is not None else "--"
    ex_close = _price(ex_close_price) if ex_close_price is not None else "--"

    ba_pnl_val = ba_pnl
    ex_pnl_val = ex_pnl
    net = None
    if ba_pnl_val is not None or ex_pnl_val is not None:
        net = round((ba_pnl_val or 0.0) + (ex_pnl_val or 0.0), 2)
        if ba_charges is not None:
            net = round(net + ba_charges, 2)
        if ba_commission is not None and (ba_pnl_val is None or ba_pnl_val == 0):
            net = round(net - abs(ba_commission), 2)

    return HedgeTradeRow(
        ba_order_no=ba_order_no or "--",
        ex_order_no=ex_order_no or "--",
        product=product or "--",
        direction=direction,
        ba_qty=_dash(ba_qty if ba_qty > 0 else None),
        ex_qty=_dash(ex_qty if ex_qty > 0 else None),
        ba_open_price=_dash(ba_open),
        ba_close_price=_dash(ba_close),
        ba_pnl=_money(ba_pnl_val) if ba_pnl_val is not None else "--",
        ex_open_price=_dash(ex_open),
        ex_close_price=_dash(ex_close),
        ba_charges=_money(ba_charges) if ba_charges is not None else "--",
        ba_commission=_money(-abs(ba_commission)) if ba_commission is not None else "--",
        order_time=settled,
        net_profit=_money(net) if net is not None else "--",
        sort_ms=sort_ms,
        record_key=_record_key(ba_order_no, ex_order_no, settled),
    )


def _pair_ba_mt5(
    ba_rows: list[tuple[dict, int, str]],
    mt5_rows: list[tuple[dict, int, str]],
) -> list[HedgeTradeRow]:
    """按产品+时间窗口配对 BA userTrades 与 MT5 deals。"""
    used_mt5: set[int] = set()
    out: list[HedgeTradeRow] = []

    for ba_raw, ba_ms, product in sorted(ba_rows, key=lambda x: x[1]):
        best_idx = -1
        best_diff = PAIR_WINDOW_MS + 1
        ba_is_close = bool(ba_raw.get("_report_is_close"))
        for idx, (_raw, ms, prod) in enumerate(mt5_rows):
            if idx in used_mt5 or prod != product:
                continue
            if bool(_raw.get("_report_is_close")) != ba_is_close:
                continue
            diff = abs(ms - ba_ms)
            if diff <= PAIR_WINDOW_MS and diff < best_diff:
                best_diff = diff
                best_idx = idx

        mt5_raw: dict | None = None
        if best_idx >= 0:
            used_mt5.add(best_idx)
            mt5_raw, _, _ = mt5_rows[best_idx]

        ba_order = _as_text(ba_raw.get("orderId"))
        ex_order = _as_text(mt5_raw.get("order")) if mt5_raw else ""
        ba_qty = _as_float(ba_raw.get("qty"))
        ex_qty = _as_float(mt5_raw.get("volume")) if mt5_raw else 0.0
        ba_price = _as_float(ba_raw.get("avgPrice")) or _as_float(ba_raw.get("price"))
        ex_price = _as_float(mt5_raw.get("price")) if mt5_raw else 0.0
        ex_is_close = bool(mt5_raw.get("_report_is_close")) if mt5_raw else ba_is_close
        ba_pnl = _as_float(ba_raw.get("realizedPnl"))
        ex_pnl = _as_float(mt5_raw.get("profit")) if mt5_raw else 0.0
        ba_commission = abs(_as_float(ba_raw.get("commission")))
        ex_commission = 0.0
        ex_fee = 0.0
        ex_swap = 0.0
        if mt5_raw:
            ex_commission = _as_float(mt5_raw.get("commission"))
            ex_fee = _as_float(mt5_raw.get("fee"))
            ex_swap = _as_float(mt5_raw.get("swap"))
        ex_charges = ex_commission + ex_fee + ex_swap
        net = round(ba_pnl - ba_commission + ex_pnl + ex_charges, 2)
        order_time = _format_ms(ba_ms)

        out.append(
            HedgeTradeRow(
                ba_order_no=ba_order or "--",
                ex_order_no=ex_order or "--",
                product=product or "--",
                direction=_infer_direction_from_ba_side(
                    _as_text(ba_raw.get("side")),
                    is_close=ba_is_close,
                ),
                ba_qty=_dash(ba_qty if ba_qty > 0 else None),
                ex_qty=_dash(ex_qty if ex_qty > 0 else None),
                ba_open_price=_price(None if ba_is_close else ba_price),
                ba_close_price=_price(ba_price if ba_is_close else None),
                ba_pnl=_money(ba_pnl) if ba_pnl != 0 or ba_order else "--",
                ex_open_price=_price(None if ex_is_close else ex_price),
                ex_close_price=_price(ex_price if ex_is_close else None),
                ba_charges="--",
                ba_commission=_money(-ba_commission) if ba_commission else "--",
                order_time=order_time or "--",
                net_profit=_money(net),
                sort_ms=ba_ms,
                record_key=_record_key(ba_order, ex_order, order_time),
            )
        )

    for idx, (mt5_raw, ms, product) in enumerate(mt5_rows):
        if idx in used_mt5:
            continue
        ex_order = _as_text(mt5_raw.get("order"))
        ex_qty = _as_float(mt5_raw.get("volume"))
        ex_price = _as_float(mt5_raw.get("price"))
        ex_is_close = bool(mt5_raw.get("_report_is_close"))
        ex_pnl = _as_float(mt5_raw.get("profit"))
        ex_commission = _as_float(mt5_raw.get("commission"))
        ex_fee = _as_float(mt5_raw.get("fee"))
        ex_swap = _as_float(mt5_raw.get("swap"))
        net = round(ex_pnl + ex_commission + ex_fee + ex_swap, 2)
        order_time = _format_ms(ms)
        out.append(
            HedgeTradeRow(
                ba_order_no="--",
                ex_order_no=ex_order or "--",
                product=product or "--",
                direction="--",
                ba_qty="--",
                ex_qty=_dash(ex_qty if ex_qty > 0 else None),
                ba_open_price="--",
                ba_close_price="--",
                ba_pnl="--",
                ex_open_price=_price(None if ex_is_close else ex_price),
                ex_close_price=_price(ex_price if ex_is_close else None),
                ba_charges="--",
                ba_commission="--",
                order_time=order_time or "--",
                net_profit=_money(net),
                sort_ms=ms,
                record_key=_record_key("", ex_order, order_time, suffix="ex-only"),
            )
        )
    return out


def _income_row(raw: dict) -> HedgeTradeRow:
    ms = int(_as_float(raw.get("time")))
    income_type = _as_text(raw.get("incomeType"))
    income = _as_float(raw.get("income"))
    product = _product_for_ba_symbol(_as_text(raw.get("symbol")))
    order_time = _format_ms(ms)
    return HedgeTradeRow(
        ba_order_no="--",
        ex_order_no="--",
        product=product or "--",
        direction="--",
        ba_qty="--",
        ex_qty="--",
        ba_open_price="--",
        ba_close_price="--",
        ba_pnl="--",
        ex_open_price="--",
        ex_close_price="--",
        ba_charges=_money(income) if income != 0 else "--",
        ba_commission="--",
        order_time=order_time or "--",
        net_profit=_money(income),
        sort_ms=ms,
        record_key=_record_key("", "", order_time, suffix=f"income:{income_type}"),
    )


def _clean_order_no(value: Any) -> str:
    text = _as_text(value).strip()
    return "" if text in {"", "--"} else text


def _split_order_ids(value: Any) -> list[str]:
    text = _clean_order_no(value)
    if not text:
        return []
    return [part.strip() for part in text.replace(";", ",").split(",") if part.strip()]


def _parse_sort_ms(value: Any) -> int:
    text = _as_text(value).strip()
    if not text or text == "--":
        return 0
    try:
        return int(datetime.fromisoformat(text.replace(" ", "T")).timestamp() * 1000)
    except ValueError:
        return 0


def _index_by_order(
    rows: list[tuple[dict, int, str]],
    field: str,
) -> dict[str, tuple[dict, int, str]]:
    out: dict[str, tuple[dict, int, str]] = {}
    for raw, ms, product in rows:
        oid = _clean_order_no(raw.get(field))
        if oid:
            out[oid] = (raw, ms, product)
    return out


def _find_order(
    index: dict[str, tuple[dict, int, str]],
    order_text: Any,
) -> tuple[dict, int, str] | None:
    for oid in _split_order_ids(order_text):
        found = index.get(oid)
        if found:
            return found
    return None


def _anchor_action(anchor: dict, ba_raw: dict | None, mt5_raw: dict | None) -> str:
    action = _as_text(anchor.get("action")).lower()
    if action in {"open", "close"}:
        return action
    if ba_raw is not None:
        return "close" if bool(ba_raw.get("_report_is_close")) else "open"
    if mt5_raw is not None:
        return "close" if bool(mt5_raw.get("_report_is_close")) else "open"
    if _clean_order_no(anchor.get("ba_close_price")) or _clean_order_no(
        anchor.get("ex_close_price")
    ):
        return "close"
    return "open"


def _row_from_anchor(
    anchor: dict,
    ba_match: tuple[dict, int, str] | None,
    mt5_match: tuple[dict, int, str] | None,
) -> HedgeTradeRow:
    ba_raw = ba_match[0] if ba_match else None
    mt5_raw = mt5_match[0] if mt5_match else None
    ba_ms = ba_match[1] if ba_match else 0
    mt5_ms = mt5_match[1] if mt5_match else 0
    action = _anchor_action(anchor, ba_raw, mt5_raw)
    is_close = action == "close"

    ba_price = _as_float(ba_raw.get("avgPrice") if ba_raw else None) or _as_float(
        ba_raw.get("price") if ba_raw else None
    )
    ex_price = _as_float(mt5_raw.get("price") if mt5_raw else None)
    ba_pnl_known = ba_raw is not None
    ex_pnl_known = mt5_raw is not None
    ba_pnl = _as_float(ba_raw.get("realizedPnl") if ba_raw else None)
    ex_pnl = _as_float(mt5_raw.get("profit") if mt5_raw else None)
    ba_commission = abs(_as_float(ba_raw.get("commission") if ba_raw else None))
    ex_charges = 0.0
    if mt5_raw is not None:
        ex_charges = (
            _as_float(mt5_raw.get("commission"))
            + _as_float(mt5_raw.get("fee"))
            + _as_float(mt5_raw.get("swap"))
        )

    official_net: float | None = None
    if ba_pnl_known or ex_pnl_known:
        official_net = round(ba_pnl - ba_commission + ex_pnl + ex_charges, 2)

    order_time = _as_text(anchor.get("order_time")) or _format_ms(max(ba_ms, mt5_ms))
    sort_ms = _parse_sort_ms(order_time) or max(ba_ms, mt5_ms)
    product = (
        _as_text(anchor.get("product"))
        or (ba_match[2] if ba_match else "")
        or (mt5_match[2] if mt5_match else "")
        or "--"
    )
    direction = (
        _mode_label(_as_text(anchor.get("mode")))
        or _as_text(anchor.get("direction"))
        or "--"
    )

    return HedgeTradeRow(
        ba_order_no=_clean_order_no(anchor.get("ba_order_no"))
        or (ba_raw and _as_text(ba_raw.get("orderId")))
        or "--",
        ex_order_no=_clean_order_no(anchor.get("ex_order_no"))
        or (mt5_raw and _as_text(mt5_raw.get("order")))
        or "--",
        product=product,
        direction=direction,
        ba_qty=_dash(_as_float(ba_raw.get("qty")) if ba_raw else anchor.get("ba_qty")),
        ex_qty=_dash(_as_float(mt5_raw.get("volume")) if mt5_raw else anchor.get("ex_qty")),
        ba_open_price=_price(None if is_close else ba_price),
        ba_close_price=_price(ba_price if is_close else None),
        ba_pnl=_money(ba_pnl) if ba_pnl_known else _dash(anchor.get("ba_pnl")),
        ex_open_price=_price(None if is_close else ex_price),
        ex_close_price=_price(ex_price if is_close else None),
        ba_charges=_dash(anchor.get("ba_charges")),
        ba_commission=(
            _money(-ba_commission) if ba_commission else _dash(anchor.get("ba_commission"))
        ),
        order_time=order_time or "--",
        net_profit=(
            _money(official_net) if official_net is not None else _dash(anchor.get("net_profit"))
        ),
        sort_ms=sort_ms,
        record_key=_as_text(anchor.get("record_key"))
        or _record_key(
            _clean_order_no(anchor.get("ba_order_no")),
            _clean_order_no(anchor.get("ex_order_no")),
            order_time,
        ),
    )


def _rows_from_anchors(
    anchors: list[dict],
    ba_rows: list[tuple[dict, int, str]],
    mt5_rows: list[tuple[dict, int, str]],
) -> tuple[list[HedgeTradeRow], set[str], set[str]]:
    ba_by_order = _index_by_order(ba_rows, "orderId")
    mt5_by_order = _index_by_order(mt5_rows, "order")
    out: list[HedgeTradeRow] = []
    used_ba: set[str] = set()
    used_mt5: set[str] = set()
    seen: set[tuple] = set()
    for anchor in anchors:
        key = (
            _as_text(anchor.get("record_key")),
            _as_text(anchor.get("action")),
            _as_text(anchor.get("ba_order_no")),
            _as_text(anchor.get("ex_order_no")),
            _as_text(anchor.get("order_time")),
        )
        if key in seen:
            continue
        seen.add(key)
        ba_match = _find_order(ba_by_order, anchor.get("ba_order_no"))
        mt5_match = _find_order(mt5_by_order, anchor.get("ex_order_no"))
        if ba_match is None and mt5_match is None:
            out.append(_row_from_anchor(anchor, None, None))
            continue
        out.append(_row_from_anchor(anchor, ba_match, mt5_match))
        for oid in _split_order_ids(anchor.get("ba_order_no")):
            if oid in ba_by_order:
                used_ba.add(oid)
        for oid in _split_order_ids(anchor.get("ex_order_no")):
            if oid in mt5_by_order:
                used_mt5.add(oid)
    return out, used_ba, used_mt5


def fetch_hedge_trade_report(
    binance,
    mt5,
    config: AppConfig,
    start: date,
    end: date,
    symbol_filter: str = "all",
    anchors: list[dict] | None = None,
) -> HedgeTradeReport:
    """从 BA/EX 官方历史成交拉取报表；优先按本地订单号锚点精确配对。"""
    start_dt, end_dt = _local_range(start, end)
    start_ms = int(start_dt.timestamp() * 1000)
    end_ms = int(end_dt.timestamp() * 1000) - 1
    ba_symbols, mt5_symbols = _symbols_for_filter(symbol_filter)

    report = HedgeTradeReport()
    ba_trade_rows: list[dict] = []
    ba_order_rows: list[dict] = []
    mt5_deal_rows: list[dict] = []

    if config.use_live_ba and binance is not None:
        try:
            ba_trade_rows = binance.fetch_account_trade_history(
                ba_symbols, start_ms, end_ms
            )
            fetch_orders = getattr(binance, "fetch_order_history_rows", None)
            if fetch_orders is not None:
                ba_order_rows = fetch_orders(ba_symbols, start_ms, end_ms)
            for raw in binance.fetch_income_history_rows(ba_symbols, start_ms, end_ms):
                report.rows.append(_income_row(raw))
        except Exception as exc:  # noqa: BLE001
            report.errors.append(f"BA 官方历史成交读取失败: {exc}")

    if config.use_live_mt5 and mt5 is not None:
        try:
            mt5_deal_rows = mt5.fetch_history_deals(mt5_symbols, start_dt, end_dt)
        except Exception as exc:  # noqa: BLE001
            report.errors.append(f"EX 官方历史成交读取失败: {exc}")

    ba_rows = _aggregate_ba_trade_orders(ba_trade_rows, ba_order_rows)
    mt5_rows = _aggregate_mt5_deal_orders(mt5_deal_rows)
    if anchors:
        anchored_rows, used_ba, used_mt5 = _rows_from_anchors(anchors, ba_rows, mt5_rows)
        report.rows.extend(anchored_rows)
        ba_rows = [
            item for item in ba_rows if _clean_order_no(item[0].get("orderId")) not in used_ba
        ]
        mt5_rows = [
            item for item in mt5_rows if _clean_order_no(item[0].get("order")) not in used_mt5
        ]
    report.rows.extend(_pair_ba_mt5(ba_rows, mt5_rows))
    report.rows.sort(key=lambda r: r.sort_ms, reverse=True)
    return report
