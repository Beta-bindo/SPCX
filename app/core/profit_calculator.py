from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from app.core.trade_ledger import TradeLedger, TradeRecord


def record_label(rec: TradeRecord) -> str:
    label = "黄金" if rec.preset_id == "xau" else "白银"
    mode = "收缩" if rec.mode == "contraction" else "扩张"
    return f"{label}{mode}"


@dataclass
class ProfitRow:
    settled_at: str
    product: str
    direction: str
    ba_qty: float
    mt5_qty: float
    spread: float
    ba_pnl: float
    ex_pnl: float
    fee: float
    profit: float


@dataclass
class ProfitReport:
    ba_pnl: float = 0.0
    ba_fee: float = 0.0
    mt5_pnl: float = 0.0
    mt5_fee: float = 0.0
    total_pnl: float = 0.0
    records: list[TradeRecord] | None = None

    @property
    def summary_text(self) -> str:
        lines = [
            "交易记录汇总",
            f"  BA 净盈亏 ${self.ba_pnl:.2f} · 手续费 ${self.ba_fee:.4f}",
            f"  Exness 净盈亏 ${self.mt5_pnl:.2f} · 手续费 ${self.mt5_fee:.4f}",
            f"  合计利润 ${self.total_pnl:.2f}",
        ]
        if self.records:
            lines.append("")
            for rec in self.records:
                lines.append(
                    f"  {rec.settled_at[:10]} {record_label(rec)} · "
                    f"BA ${rec.ba_pnl:+.2f} · Ex ${rec.mt5_pnl:+.2f} · 净 ${rec.net_pnl:+.2f}"
                )
        else:
            lines.append("")
            lines.append("  （该时段无已结算平仓记录）")
        return "\n".join(lines)

    @property
    def rows(self) -> list[ProfitRow]:
        if not self.records:
            return []
        return [
            ProfitRow(
                settled_at=rec.settled_at.replace("T", " "),
                product=record_label(rec),
                direction=rec.direction,
                ba_qty=rec.ba_quantity,
                mt5_qty=rec.mt5_quantity,
                spread=rec.spread,
                ba_pnl=rec.ba_pnl,
                ex_pnl=rec.mt5_pnl,
                fee=rec.total_fees,
                profit=rec.net_pnl,
            )
            for rec in self.records
        ]


def calculate_profit(
    ledger: TradeLedger,
    start: date,
    end: date | None = None,
    symbol_filter: str = "all",
) -> ProfitReport:
    """symbol_filter: all | xau | xag. 仅统计已平仓结算（action=close）。"""
    preset = None if symbol_filter == "all" else symbol_filter
    records = [r for r in ledger.filter(start, end, preset) if r.action == "close"]
    ba_pnl = sum(r.ba_pnl for r in records)
    mt5_pnl = sum(r.mt5_pnl for r in records)
    ba_fee = sum(r.ba_fee for r in records)
    mt5_fee = sum(r.mt5_fee for r in records)
    gross = ba_pnl + mt5_pnl
    fees = ba_fee + mt5_fee
    return ProfitReport(
        ba_pnl=round(ba_pnl, 2),
        ba_fee=round(ba_fee, 4),
        mt5_pnl=round(mt5_pnl, 2),
        mt5_fee=round(mt5_fee, 4),
        total_pnl=round(gross - fees, 2),
        records=records,
    )
