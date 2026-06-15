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
        """把明细记录转为表格行（供导出/表格展示）。"""
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
                ba_funding_fee=rec.ba_funding_fee,
                ba_rebate=rec.ba_rebate,
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
    ba_funding_fee = sum(r.ba_funding_fee for r in records)
    ba_rebate = sum(r.ba_rebate for r in records)
    mt5_fee = sum(r.mt5_fee for r in records)
    return ProfitReport(
        ba_pnl=round(ba_pnl, 2),
        ba_fee=round(ba_fee, 4),
        ba_funding_fee=round(ba_funding_fee, 4),
        ba_rebate=round(ba_rebate, 4),
        mt5_pnl=round(mt5_pnl, 2),
        mt5_fee=round(mt5_fee, 4),
        total_pnl=round(sum(r.net_pnl for r in records), 2),
        records=records,
    )
