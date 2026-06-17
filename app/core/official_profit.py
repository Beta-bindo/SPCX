"""Official-history profit report built from BA user trades and MT5 deals."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime, time as dt_time, timedelta
from typing import Any

from app.core.models import AppConfig
from app.core.symbols import find_preset


BA_REBATE_TYPES = {"COMMISSION_REBATE", "API_REBATE", "FEE_RETURN"}

OFFICIAL_FIELD_ORDER = [
    "platform",
    "recordType",
    "time",
    "product",
    "symbol",
    "orderNo",
    "tradeNo",
    "sideType",
    "entry",
    "price",
    "quantity",
    "quoteQty",
    "realizedPnl",
    "profit",
    "commission",
    "commissionAsset",
    "fee",
    "swap",
    "incomeType",
    "income",
    "fundingFee",
    "rebate",
    "positionSide",
    "maker",
    "buyer",
    "position_id",
    "reason",
    "comment",
    "external_id",
    "net",
]

OFFICIAL_FIELD_LABELS = {
    "platform": "平台",
    "recordType": "官方类型",
    "time": "time",
    "product": "产品",
    "symbol": "symbol",
    "orderNo": "订单号(orderId/order)",
    "tradeNo": "成交号(id/ticket)",
    "sideType": "side/type",
    "entry": "entry",
    "price": "price",
    "quantity": "qty/volume",
    "quoteQty": "quoteQty",
    "realizedPnl": "realizedPnl",
    "profit": "profit",
    "commission": "commission",
    "commissionAsset": "commissionAsset",
    "fee": "fee",
    "swap": "swap",
    "incomeType": "incomeType",
    "income": "income",
    "fundingFee": "FUNDING_FEE",
    "rebate": "rebate",
    "positionSide": "positionSide",
    "maker": "maker",
    "buyer": "buyer",
    "position_id": "position_id",
    "reason": "reason",
    "comment": "comment",
    "external_id": "external_id",
    "net": "net",
}


def _as_float(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


def _money(value: float) -> str:
    return f"{value:.4f}"


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


@dataclass
class OfficialProfitRow:
    """One official BA trade/income row or MT5 deal row."""

    fields: dict[str, str]
    sort_ms: int = 0
    raw: dict[str, Any] = field(default_factory=dict)

    def values(self, headers: list[str]) -> list[str]:
        return [self.fields.get(h, "") for h in headers]


@dataclass
class OfficialProfitReport:
    """Official-history report shown in the profit calculator."""

    rows: list[OfficialProfitRow] = field(default_factory=list)
    headers: list[str] = field(default_factory=lambda: list(OFFICIAL_FIELD_ORDER))
    ba_pnl: float = 0.0
    ba_commission: float = 0.0
    ba_funding_fee: float = 0.0
    ba_rebate: float = 0.0
    mt5_profit: float = 0.0
    mt5_commission: float = 0.0
    mt5_fee: float = 0.0
    mt5_swap: float = 0.0
    total_pnl: float = 0.0
    errors: list[str] = field(default_factory=list)

    @property
    def ba_charges(self) -> float:
        return round(self.ba_funding_fee + self.ba_rebate, 4)

    @property
    def mt5_charges(self) -> float:
        return round(self.mt5_commission + self.mt5_fee + self.mt5_swap, 4)

    @property
    def row_count(self) -> int:
        return len(self.rows)

    @property
    def records(self) -> list[OfficialProfitRow]:
        return self.rows

    @property
    def ba_fee(self) -> float:
        return self.ba_commission

    @property
    def mt5_pnl(self) -> float:
        return self.mt5_profit


def _active_headers(rows: list[OfficialProfitRow]) -> list[str]:
    active = {
        key
        for row in rows
        for key, value in row.fields.items()
        if value not in ("", None)
    }
    headers = [key for key in OFFICIAL_FIELD_ORDER if key in active]
    return headers or list(OFFICIAL_FIELD_ORDER[:8])


def _ba_trade_row(raw: dict[str, Any]) -> tuple[OfficialProfitRow, float, float]:
    ms = int(_as_float(raw.get("time")))
    realized = _as_float(raw.get("realizedPnl"))
    commission = abs(_as_float(raw.get("commission")))
    net = round(realized - commission, 4)
    fields = {
        "platform": "BA",
        "recordType": "userTrades",
        "time": _format_ms(ms),
        "product": _product_for_ba_symbol(_as_text(raw.get("symbol"))),
        "symbol": _as_text(raw.get("symbol")),
        "orderNo": _as_text(raw.get("orderId")),
        "tradeNo": _as_text(raw.get("id")),
        "sideType": _as_text(raw.get("side")),
        "price": _as_text(raw.get("price")),
        "quantity": _as_text(raw.get("qty")),
        "quoteQty": _as_text(raw.get("quoteQty")),
        "realizedPnl": _as_text(raw.get("realizedPnl")),
        "commission": _as_text(raw.get("commission")),
        "commissionAsset": _as_text(raw.get("commissionAsset")),
        "positionSide": _as_text(raw.get("positionSide")),
        "maker": _as_text(raw.get("maker")),
        "buyer": _as_text(raw.get("buyer")),
        "net": _money(net),
    }
    return OfficialProfitRow(fields=fields, sort_ms=ms, raw=dict(raw)), realized, commission


def _ba_income_row(raw: dict[str, Any]) -> tuple[OfficialProfitRow, float, float]:
    ms = int(_as_float(raw.get("time")))
    income_type = _as_text(raw.get("incomeType"))
    income = _as_float(raw.get("income"))
    fields = {
        "platform": "BA",
        "recordType": "income",
        "time": _format_ms(ms),
        "product": _product_for_ba_symbol(_as_text(raw.get("symbol"))),
        "symbol": _as_text(raw.get("symbol")),
        "incomeType": income_type,
        "income": _as_text(raw.get("income")),
        "fundingFee": _as_text(raw.get("income")) if income_type == "FUNDING_FEE" else "",
        "rebate": _as_text(raw.get("income")) if income_type in BA_REBATE_TYPES else "",
        "net": _money(income),
    }
    funding = income if income_type == "FUNDING_FEE" else 0.0
    rebate = income if income_type in BA_REBATE_TYPES else 0.0
    return OfficialProfitRow(fields=fields, sort_ms=ms, raw=dict(raw)), funding, rebate


def _mt5_deal_row(raw: dict[str, Any]) -> tuple[OfficialProfitRow, float, float, float, float]:
    ms = int(_as_float(raw.get("time_msc"))) or int(_as_float(raw.get("time")) * 1000)
    profit = _as_float(raw.get("profit"))
    commission = _as_float(raw.get("commission"))
    fee = _as_float(raw.get("fee"))
    swap = _as_float(raw.get("swap"))
    net = round(profit + commission + fee + swap, 4)
    fields = {
        "platform": "EX",
        "recordType": "history_deals",
        "time": _format_ms(ms),
        "product": _product_for_mt5_symbol(_as_text(raw.get("symbol"))),
        "symbol": _as_text(raw.get("symbol")),
        "orderNo": _as_text(raw.get("order")),
        "tradeNo": _as_text(raw.get("ticket")),
        "sideType": _as_text(raw.get("type")),
        "entry": _as_text(raw.get("entry")),
        "price": _as_text(raw.get("price")),
        "quantity": _as_text(raw.get("volume")),
        "profit": _as_text(raw.get("profit")),
        "commission": _as_text(raw.get("commission")),
        "fee": _as_text(raw.get("fee")),
        "swap": _as_text(raw.get("swap")),
        "position_id": _as_text(raw.get("position_id")),
        "reason": _as_text(raw.get("reason")),
        "comment": _as_text(raw.get("comment")),
        "external_id": _as_text(raw.get("external_id")),
        "net": _money(net),
    }
    return OfficialProfitRow(fields=fields, sort_ms=ms, raw=dict(raw)), profit, commission, fee, swap


def _official_key(row: OfficialProfitRow) -> str:
    fields = row.fields
    platform = fields.get("platform", "")
    record_type = fields.get("recordType", "")
    symbol = fields.get("symbol", "")
    trade_no = fields.get("tradeNo", "")
    order_no = fields.get("orderNo", "")
    if platform == "BA" and record_type == "userTrades" and trade_no:
        return "|".join((platform, record_type, symbol, trade_no))
    if platform == "EX" and record_type == "history_deals" and trade_no:
        return "|".join((platform, record_type, trade_no))
    raw_id = _as_text(
        row.raw.get("tranId")
        or row.raw.get("incomeId")
        or row.raw.get("id")
        or row.raw.get("ticket")
    )
    if raw_id:
        return "|".join((platform, record_type, symbol, raw_id))
    parts = (
        platform,
        record_type,
        symbol,
        fields.get("time", ""),
        fields.get("incomeType", ""),
        fields.get("income", ""),
        order_no,
        trade_no,
    )
    return "|".join(parts)


def _preset_from_official_product(product: str, fallback: str = "") -> str:
    if product == "黄金":
        return "xau"
    if product == "白银":
        return "xag"
    return fallback


def _source_attr(source_record: Any, name: str, fallback: Any = "") -> Any:
    if source_record is None:
        return fallback
    return getattr(source_record, name, fallback)


def official_report_to_trade_payloads(
    report: OfficialProfitReport,
    *,
    source_record: Any | None = None,
) -> list[dict]:
    """Convert official rows into trade-upload payloads.

    The old upload endpoint still accepts ledger-like numeric summary fields; the
    official fields below keep one backend row per exchange-provided record.
    """
    payloads: list[dict] = []
    source_settled_at = _source_attr(source_record, "settled_at", "")
    fallback_preset = _source_attr(source_record, "preset_id", "")
    fallback_mode = _source_attr(source_record, "mode", "")
    fallback_action = _source_attr(source_record, "action", "close") or "close"
    fallback_direction = _source_attr(source_record, "direction", "")
    fallback_spread = _as_float(_source_attr(source_record, "spread", 0.0))
    fallback_ba_side = _source_attr(source_record, "ba_side", "")
    fallback_mt5_side = _source_attr(source_record, "mt5_side", "")

    for row in report.rows:
        fields = row.fields
        platform = fields.get("platform", "")
        record_type = fields.get("recordType", "")
        official_time = fields.get("time", "")
        net = _as_float(fields.get("net"))
        commission = _as_float(fields.get("commission"))
        fee = _as_float(fields.get("fee"))
        swap = _as_float(fields.get("swap"))
        product = fields.get("product", "")
        preset_id = _preset_from_official_product(product, fallback_preset)

        ba_pnl = 0.0
        mt5_pnl = 0.0
        ba_fee = 0.0
        mt5_fee = 0.0
        ba_funding_fee = 0.0
        ba_rebate = 0.0
        ba_price = 0.0
        ex_price = 0.0
        ba_quantity = 0.0
        mt5_quantity = 0.0

        if platform == "BA":
            ba_pnl = _as_float(fields.get("realizedPnl"))
            ba_fee = abs(commission) if record_type == "userTrades" else 0.0
            ba_funding_fee = _as_float(fields.get("fundingFee"))
            ba_rebate = _as_float(fields.get("rebate"))
            ba_price = _as_float(fields.get("price"))
            ba_quantity = _as_float(fields.get("quantity"))
        elif platform == "EX":
            mt5_pnl = _as_float(fields.get("profit"))
            mt5_fee = -(commission + fee + swap)
            ex_price = _as_float(fields.get("price"))
            mt5_quantity = _as_float(fields.get("quantity"))

        payloads.append(
            {
                "report_source": "official",
                "settled_at": official_time or source_settled_at,
                "preset_id": preset_id,
                "mode": fallback_mode,
                "action": fallback_action,
                "spread": fallback_spread,
                "ba_price": ba_price,
                "ex_price": ex_price,
                "ba_quantity": ba_quantity,
                "mt5_quantity": mt5_quantity,
                "ba_side": fallback_ba_side,
                "mt5_side": fallback_mt5_side,
                "direction": fallback_direction,
                "ba_pnl": round(ba_pnl, 4),
                "mt5_pnl": round(mt5_pnl, 4),
                "ba_fee": round(ba_fee, 4),
                "mt5_fee": round(mt5_fee, 4),
                "ba_funding_fee": round(ba_funding_fee, 4),
                "ba_rebate": round(ba_rebate, 4),
                "net_pnl": round(net, 4),
                "official_platform": platform,
                "official_record_type": record_type,
                "official_key": _official_key(row),
                "official_time": official_time,
                "official_product": product,
                "official_symbol": fields.get("symbol", ""),
                "official_order_no": fields.get("orderNo", ""),
                "official_trade_no": fields.get("tradeNo", ""),
                "official_side_type": fields.get("sideType", ""),
                "official_entry": fields.get("entry", ""),
                "official_price": fields.get("price", ""),
                "official_quantity": fields.get("quantity", ""),
                "official_quote_qty": fields.get("quoteQty", ""),
                "official_realized_pnl": fields.get("realizedPnl", ""),
                "official_profit": fields.get("profit", ""),
                "official_commission": fields.get("commission", ""),
                "official_commission_asset": fields.get("commissionAsset", ""),
                "official_fee": fields.get("fee", ""),
                "official_swap": fields.get("swap", ""),
                "official_income_type": fields.get("incomeType", ""),
                "official_income": fields.get("income", ""),
                "official_funding_fee": fields.get("fundingFee", ""),
                "official_rebate": fields.get("rebate", ""),
                "official_position_side": fields.get("positionSide", ""),
                "official_maker": fields.get("maker", ""),
                "official_buyer": fields.get("buyer", ""),
                "official_position_id": fields.get("position_id", ""),
                "official_reason": fields.get("reason", ""),
                "official_comment": fields.get("comment", ""),
                "official_external_id": fields.get("external_id", ""),
                "official_net": round(net, 4),
                "official_raw_json": json.dumps(
                    row.raw or fields, ensure_ascii=False, default=str
                ),
            }
        )
    return payloads


def fetch_official_profit_report(
    binance,
    mt5,
    config: AppConfig,
    start: date,
    end: date,
    symbol_filter: str = "all",
) -> OfficialProfitReport:
    """Fetch official BA user trades/income and MT5 history deals for the date range."""
    start_dt, end_dt = _local_range(start, end)
    start_ms = int(start_dt.timestamp() * 1000)
    end_ms = int(end_dt.timestamp() * 1000) - 1
    ba_symbols, mt5_symbols = _symbols_for_filter(symbol_filter)

    report = OfficialProfitReport()
    rows: list[OfficialProfitRow] = []

    if config.use_live_ba and binance is not None:
        try:
            for raw in binance.fetch_account_trade_history(ba_symbols, start_ms, end_ms):
                row, pnl, commission = _ba_trade_row(raw)
                rows.append(row)
                report.ba_pnl += pnl
                report.ba_commission += commission
            for raw in binance.fetch_income_history_rows(ba_symbols, start_ms, end_ms):
                row, funding, rebate = _ba_income_row(raw)
                rows.append(row)
                report.ba_funding_fee += funding
                report.ba_rebate += rebate
        except Exception as exc:  # noqa: BLE001 - show a useful message in the report
            report.errors.append(f"BA 官方历史成交读取失败: {exc}")

    if config.use_live_mt5 and mt5 is not None:
        try:
            for raw in mt5.fetch_history_deals(mt5_symbols, start_dt, end_dt):
                row, profit, commission, fee, swap = _mt5_deal_row(raw)
                rows.append(row)
                report.mt5_profit += profit
                report.mt5_commission += commission
                report.mt5_fee += fee
                report.mt5_swap += swap
        except Exception as exc:  # noqa: BLE001
            report.errors.append(f"EX 官方历史成交读取失败: {exc}")

    rows.sort(key=lambda r: r.sort_ms, reverse=True)
    report.rows = rows
    report.headers = _active_headers(rows)
    report.ba_pnl = round(report.ba_pnl, 2)
    report.ba_commission = round(report.ba_commission, 4)
    report.ba_funding_fee = round(report.ba_funding_fee, 4)
    report.ba_rebate = round(report.ba_rebate, 4)
    report.mt5_profit = round(report.mt5_profit, 2)
    report.mt5_commission = round(report.mt5_commission, 4)
    report.mt5_fee = round(report.mt5_fee, 4)
    report.mt5_swap = round(report.mt5_swap, 4)
    report.total_pnl = round(
        report.ba_pnl
        - report.ba_commission
        + report.ba_funding_fee
        + report.ba_rebate
        + report.mt5_profit
        + report.mt5_charges,
        2,
    )
    return report
