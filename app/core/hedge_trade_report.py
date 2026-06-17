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
    "ba_open_spread",
    "ba_close_spread",
    "ba_pnl",
    "ex_open_spread",
    "ex_close_spread",
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
    "ba_open_spread": "BA开仓点数",
    "ba_close_spread": "平仓点数",
    "ba_pnl": "BA盈亏",
    "ex_open_spread": "EX开仓点数",
    "ex_close_spread": "EX平仓点数",
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


def _as_float(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


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


def _infer_direction_from_ba_side(side: str) -> str:
    side = side.upper()
    if side == "SELL":
        return "收缩"
    if side == "BUY":
        return "扩张"
    return "--"


@dataclass
class HedgeTradeRow:
    """一行对冲成交明细（计算器与运营上报共用）。"""

    ba_order_no: str = ""
    ex_order_no: str = ""
    product: str = ""
    direction: str = ""
    ba_qty: str = ""
    ex_qty: str = ""
    ba_open_spread: str = ""
    ba_close_spread: str = ""
    ba_pnl: str = ""
    ex_open_spread: str = ""
    ex_close_spread: str = ""
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
            "ba_open_spread": self.ba_open_spread or "",
            "ba_close_spread": self.ba_close_spread or "",
            "ba_pnl": self.ba_pnl or "",
            "ex_open_spread": self.ex_open_spread or "",
            "ex_close_spread": self.ex_close_spread or "",
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
    ba_open_spread: float | None = None,
    ba_close_spread: float | None = None,
    ex_open_spread: float | None = None,
    ex_close_spread: float | None = None,
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

    ba_open = (
        f"{ba_open_spread:+.3f}"
        if ba_open_spread is not None
        else ("--" if action == "close" else None)
    )
    ba_close = (
        f"{ba_close_spread:+.3f}"
        if ba_close_spread is not None
        else ("--" if action == "open" else None)
    )
    ex_open = (
        f"{ex_open_spread:+.3f}"
        if ex_open_spread is not None
        else ba_open if ba_open and ba_open != "--" else "--"
    )
    ex_close = (
        f"{ex_close_spread:+.3f}"
        if ex_close_spread is not None
        else ba_close if ba_close and ba_close != "--" else "--"
    )

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
        ba_open_spread=_dash(ba_open),
        ba_close_spread=_dash(ba_close),
        ba_pnl=_money(ba_pnl_val) if ba_pnl_val is not None else "--",
        ex_open_spread=_dash(ex_open),
        ex_close_spread=_dash(ex_close),
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
        for idx, (_raw, ms, prod) in enumerate(mt5_rows):
            if idx in used_mt5 or prod != product:
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
                direction=_infer_direction_from_ba_side(_as_text(ba_raw.get("side"))),
                ba_qty=_dash(ba_qty if ba_qty > 0 else None),
                ex_qty=_dash(ex_qty if ex_qty > 0 else None),
                ba_open_spread="--",
                ba_close_spread="--",
                ba_pnl=_money(ba_pnl) if ba_pnl != 0 or ba_order else "--",
                ex_open_spread="--",
                ex_close_spread="--",
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
                ba_open_spread="--",
                ba_close_spread="--",
                ba_pnl="--",
                ex_open_spread="--",
                ex_close_spread="--",
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
        ba_open_spread="--",
        ba_close_spread="--",
        ba_pnl="--",
        ex_open_spread="--",
        ex_close_spread="--",
        ba_charges=_money(income) if income != 0 else "--",
        ba_commission="--",
        order_time=order_time or "--",
        net_profit=_money(income),
        sort_ms=ms,
        record_key=_record_key("", "", order_time, suffix=f"income:{income_type}"),
    )


def fetch_hedge_trade_report(
    binance,
    mt5,
    config: AppConfig,
    start: date,
    end: date,
    symbol_filter: str = "all",
) -> HedgeTradeReport:
    """从 BA/EX 官方历史成交拉取并配对为对冲报表行。"""
    start_dt, end_dt = _local_range(start, end)
    start_ms = int(start_dt.timestamp() * 1000)
    end_ms = int(end_dt.timestamp() * 1000) - 1
    ba_symbols, mt5_symbols = _symbols_for_filter(symbol_filter)

    report = HedgeTradeReport()
    ba_rows: list[tuple[dict, int, str]] = []
    mt5_rows: list[tuple[dict, int, str]] = []

    if config.use_live_ba and binance is not None:
        try:
            for raw in binance.fetch_account_trade_history(ba_symbols, start_ms, end_ms):
                ms = int(_as_float(raw.get("time")))
                product = _product_for_ba_symbol(_as_text(raw.get("symbol")))
                ba_rows.append((raw, ms, product))
            for raw in binance.fetch_income_history_rows(ba_symbols, start_ms, end_ms):
                report.rows.append(_income_row(raw))
        except Exception as exc:  # noqa: BLE001
            report.errors.append(f"BA 官方历史成交读取失败: {exc}")

    if config.use_live_mt5 and mt5 is not None:
        try:
            for raw in mt5.fetch_history_deals(mt5_symbols, start_dt, end_dt):
                ms = int(_as_float(raw.get("time_msc"))) or int(
                    _as_float(raw.get("time")) * 1000
                )
                product = _product_for_mt5_symbol(_as_text(raw.get("symbol")))
                mt5_rows.append((raw, ms, product))
        except Exception as exc:  # noqa: BLE001
            report.errors.append(f"EX 官方历史成交读取失败: {exc}")

    report.rows.extend(_pair_ba_mt5(ba_rows, mt5_rows))
    report.rows.sort(key=lambda r: r.sort_ms, reverse=True)
    return report


# 兼容旧引用
OfficialProfitReport = HedgeTradeReport
OfficialProfitRow = HedgeTradeRow
OFFICIAL_FIELD_ORDER = FIELD_ORDER
OFFICIAL_FIELD_LABELS = FIELD_LABELS
fetch_official_profit_report = fetch_hedge_trade_report
