"""收益统计：按日期/品种汇总成交流水，生成报表行与汇总文本。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from app.core.trade_ledger import TradeLedger, TradeRecord


def record_label(rec: TradeRecord) -> str:
    """记录 → "黄金收缩"这类品种+方向标签。"""
    label = "黄金" if rec.preset_id == "xau" else "白银"
    mode = "收缩" if rec.mode == "contraction" else "扩张"
    return f"{label}{mode}"


@dataclass
class ProfitRow:
    """报表中的一行（对应一条平仓结算记录）。"""

    settled_at: str
    product: str
    direction: str
    ba_qty: float
    mt5_qty: float
    spread: float
    ba_pnl: float
    ex_pnl: float
    fee: float
    opening_fee: float
    ba_funding_fee: float
    ba_rebate: float
    profit: float

    @property
    def ba_charges(self) -> float:
        """BA 资费净额 = 资金费 + 返佣。"""
        return round(self.ba_funding_fee + self.ba_rebate, 4)


@dataclass
class ProfitReport:
    """某时段的收益汇总（两端盈亏/手续费/合计）与明细记录。"""

    ba_pnl: float = 0.0
    ba_fee: float = 0.0
    ba_funding_fee: float = 0.0
    ba_rebate: float = 0.0
    mt5_pnl: float = 0.0
    mt5_fee: float = 0.0
    total_pnl: float = 0.0
    records: list[TradeRecord] | None = None
    opening_ba_fees: list[float] | None = None
    opening_mt5_fees: list[float] | None = None

    @property
    def ba_charges(self) -> float:
        """BA 资费净额 = 资金费 + 返佣。"""
        return round(self.ba_funding_fee + self.ba_rebate, 4)

    @property
    def summary_text(self) -> str:
        """多行汇总文本，用于结算弹窗/日志展示。"""
        lines = [
            "交易记录汇总",
            f"  BA 净盈亏 ${self.ba_pnl:.2f} · 手续费 ${self.ba_fee:.4f}",
            f"  BA 资金费 ${self.ba_funding_fee:+.4f} · 返佣 ${self.ba_rebate:+.4f}",
            f"  Exness 净盈亏 ${self.mt5_pnl:.2f} · 手续费 ${self.mt5_fee:.4f}",
            f"  合计利润 ${self.total_pnl:.2f}",
        ]
        if self.records:
            lines.append("")
            for row in self.rows:
                lines.append(
                    f"  {row.settled_at[:10]} {row.product} · "
                    f"BA ${row.ba_pnl:+.2f} · Ex ${row.ex_pnl:+.2f} · 净 ${row.profit:+.2f}"
                )
        else:
            lines.append("")
            lines.append("  （该时段无已结算平仓记录）")
        return "\n".join(lines)

    @property
    def rows(self) -> list[ProfitRow]:
        """把明细记录转为表格行（供导出/表格展示）。"""
        if not self.records:
            return []
        rows: list[ProfitRow] = []
        opening_ba = self.opening_ba_fees or []
        opening_mt5 = self.opening_mt5_fees or []
        for idx, rec in enumerate(self.records):
            open_fee = round(
                (opening_ba[idx] if idx < len(opening_ba) else 0.0)
                + (opening_mt5[idx] if idx < len(opening_mt5) else 0.0),
                4,
            )
            total_fee = round(rec.total_fees + open_fee, 4)
            profit = round(
                rec.gross_pnl - total_fee + rec.ba_funding_fee + rec.ba_rebate,
                2,
            )
            rows.append(
                ProfitRow(
                    settled_at=rec.settled_at.replace("T", " "),
                    product=record_label(rec),
                    direction=rec.direction,
                    ba_qty=rec.ba_quantity,
                    mt5_qty=rec.mt5_quantity,
                    spread=rec.spread,
                    ba_pnl=rec.ba_pnl,
                    ex_pnl=rec.mt5_pnl,
                    fee=total_fee,
                    opening_fee=open_fee,
                    ba_funding_fee=rec.ba_funding_fee,
                    ba_rebate=rec.ba_rebate,
                    profit=profit,
                )
            )
        return rows


@dataclass
class _FeeLot:
    """待分摊的开仓手续费；有数量时按数量比例分摊，无数量时挂到下一笔平仓。"""

    quantity: float | None
    fee: float


def _record_day(rec: TradeRecord) -> date | None:
    try:
        return date.fromisoformat(rec.settled_at[:10])
    except (TypeError, ValueError):
        return None


def _queue_key(rec: TradeRecord) -> tuple[str, str]:
    return rec.preset_id, rec.mode


def _append_fee_lot(queue: list[_FeeLot], quantity: float, fee: float) -> None:
    if abs(fee) < 1e-12:
        return
    queue.append(_FeeLot(quantity if quantity > 0 else None, round(fee, 4)))


def _allocate_open_fee(queue: list[_FeeLot], close_qty: float) -> float:
    """从同策略开仓费队列中按平仓数量 FIFO 分摊。"""
    allocated = 0.0
    if close_qty > 0:
        remaining_qty = close_qty
        while remaining_qty > 1e-12 and queue:
            lot = queue[0]
            if lot.quantity is None or lot.quantity <= 1e-12:
                allocated += lot.fee
                queue.pop(0)
                continue
            qty = min(remaining_qty, lot.quantity)
            ratio = qty / lot.quantity
            fee_part = lot.fee * ratio
            allocated += fee_part
            lot.quantity = round(lot.quantity - qty, 10)
            lot.fee = round(lot.fee - fee_part, 4)
            remaining_qty -= qty
            if lot.quantity <= 1e-12 or abs(lot.fee) <= 1e-12:
                queue.pop(0)
        return round(allocated, 4)

    # 旧流水可能没有数量字段；这种情况下至少把最近一笔开仓费带进下一笔平仓。
    if queue:
        lot = queue.pop(0)
        allocated += lot.fee
    return round(allocated, 4)


def _settled_records_with_open_fees(
    ledger: TradeLedger,
    start: date,
    end: date,
    preset_id: str | None,
) -> tuple[list[TradeRecord], list[float], list[float]]:
    """返回区间内平仓记录，并把对应开仓手续费按 FIFO 分摊到平仓记录。"""
    ba_fee_queues: dict[tuple[str, str], list[_FeeLot]] = {}
    mt5_fee_queues: dict[tuple[str, str], list[_FeeLot]] = {}
    close_records: list[TradeRecord] = []
    opening_ba_fees: list[float] = []
    opening_mt5_fees: list[float] = []

    ordered = sorted(ledger.records, key=lambda r: r.settled_at)
    for rec in ordered:
        rec_day = _record_day(rec)
        if rec_day is None or rec_day > end:
            continue
        if preset_id and rec.preset_id != preset_id:
            continue
        key = _queue_key(rec)
        if rec.action == "open":
            _append_fee_lot(
                ba_fee_queues.setdefault(key, []),
                rec.ba_quantity,
                rec.ba_fee,
            )
            _append_fee_lot(
                mt5_fee_queues.setdefault(key, []),
                rec.mt5_quantity,
                rec.mt5_fee,
            )
            continue
        if rec.action != "close":
            continue

        open_ba_fee = _allocate_open_fee(
            ba_fee_queues.setdefault(key, []),
            rec.ba_quantity,
        )
        open_mt5_fee = _allocate_open_fee(
            mt5_fee_queues.setdefault(key, []),
            rec.mt5_quantity,
        )
        if rec_day < start:
            continue
        close_records.append(rec)
        opening_ba_fees.append(open_ba_fee)
        opening_mt5_fees.append(open_mt5_fee)

    return close_records, opening_ba_fees, opening_mt5_fees


def calculate_profit(
    ledger: TradeLedger,
    start: date,
    end: date | None = None,
    symbol_filter: str = "all",
) -> ProfitReport:
    """symbol_filter: all | xau | xag. 统计已平仓结算，并扣对应开仓+平仓手续费。"""
    preset = None if symbol_filter == "all" else symbol_filter
    final_day = end or date.today()
    records, opening_ba_fees, opening_mt5_fees = _settled_records_with_open_fees(
        ledger,
        start,
        final_day,
        preset,
    )
    ba_pnl = sum(r.ba_pnl for r in records)
    mt5_pnl = sum(r.mt5_pnl for r in records)
    ba_fee = sum(r.ba_fee for r in records) + sum(opening_ba_fees)
    ba_funding_fee = sum(r.ba_funding_fee for r in records)
    ba_rebate = sum(r.ba_rebate for r in records)
    mt5_fee = sum(r.mt5_fee for r in records) + sum(opening_mt5_fees)
    total_pnl = 0.0
    for idx, rec in enumerate(records):
        open_fee = (
            (opening_ba_fees[idx] if idx < len(opening_ba_fees) else 0.0)
            + (opening_mt5_fees[idx] if idx < len(opening_mt5_fees) else 0.0)
        )
        total_pnl += (
            rec.gross_pnl
            - rec.total_fees
            - open_fee
            + rec.ba_funding_fee
            + rec.ba_rebate
        )
    return ProfitReport(
        ba_pnl=round(ba_pnl, 2),
        ba_fee=round(ba_fee, 4),
        ba_funding_fee=round(ba_funding_fee, 4),
        ba_rebate=round(ba_rebate, 4),
        mt5_pnl=round(mt5_pnl, 2),
        mt5_fee=round(mt5_fee, 4),
        total_pnl=round(total_pnl, 2),
        records=records,
        opening_ba_fees=opening_ba_fees,
        opening_mt5_fees=opening_mt5_fees,
    )
