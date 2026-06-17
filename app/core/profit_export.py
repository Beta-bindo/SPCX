from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

from app.core.paths import exports_dir
from app.core.profit_calculator import ProfitReport
from app.core.xlsx_writer import CellSpec, write_styled_xlsx

EXPORT_DIR = exports_dir()

SYMBOL_NAMES = {"all": "全部", "xau": "黄金", "xag": "白银"}


def export_filename(symbol_filter: str, when: datetime | None = None) -> str:
    when = when or datetime.now()
    product = SYMBOL_NAMES.get(symbol_filter, "全部")
    return f"利润_{product}_{when.strftime('%Y%m%d_%H%M%S')}.xlsx"


def _border_row(values: list, bold: bool = False) -> list[CellSpec]:
    return [CellSpec(v, border=True, bold=bold) for v in values]


def export_profit_xlsx(
    report: ProfitReport,
    symbol_filter: str,
    start: date,
    end: date,
    path: Path | None = None,
) -> Path:
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    out = path or (EXPORT_DIR / export_filename(symbol_filter))

    grid: list[list[CellSpec]] = [
        _border_row(
            [
                "结算时间",
                "产品",
                "方向",
                "BA数量",
                "EX手数",
                "点差",
                "BA盈亏",
                "EX盈亏",
                "手续费(开+平)",
                "BA资费",
                "净利润",
            ],
            bold=True,
        ),
    ]

    for row in report.rows:
        grid.append(
            _border_row(
                [
                    row.settled_at,
                    row.product,
                    row.direction,
                    row.ba_qty,
                    row.mt5_qty,
                    row.spread,
                    row.ba_pnl,
                    row.ex_pnl,
                    row.fee,
                    row.ba_charges,
                    row.profit,
                ]
            )
        )

    grid.append([])
    grid.append(_border_row(["汇总", "数值"], bold=True))
    grid.append(_border_row(["笔数", len(report.records or [])]))
    grid.append(_border_row(["BA利润", report.ba_pnl]))
    grid.append(_border_row(["BA手续费", report.ba_fee]))
    grid.append(_border_row(["BA资金费", report.ba_funding_fee]))
    grid.append(_border_row(["BA返佣", report.ba_rebate]))
    grid.append(_border_row(["BA资费合计", report.ba_charges]))
    grid.append(_border_row(["Exness利润", report.mt5_pnl]))
    grid.append(_border_row(["Exness手续费", report.mt5_fee]))
    grid.append(_border_row(["总手续费(开+平)", round(report.ba_fee + report.mt5_fee, 4)]))
    grid.append(_border_row(["总利润", report.total_pnl]))

    write_styled_xlsx(
        out, grid, col_widths=[20, 12, 16, 12, 12, 10, 12, 12, 12, 12, 12]
    )
    return out
