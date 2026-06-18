"""利润计算器 xlsx 导出。"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from app.core.hedge_trade_report import FIELD_LABELS, HedgeTradeReport
from app.core.paths import exports_dir
from app.core.xlsx_writer import CellSpec, write_styled_xlsx

EXPORT_DIR = exports_dir()


def export_filename(symbol_filter: str) -> str:
    sym = {"all": "全部", "xau": "黄金", "xag": "SPCXUSDT"}.get(symbol_filter, symbol_filter)
    stamp = date.today().isoformat()
    return f"利润明细_{sym}_{stamp}.xlsx"


def _border_row(values: list, bold: bool = False) -> list[CellSpec]:
    return [CellSpec(v, border=True, bold=bold) for v in values]


def export_profit_xlsx(
    report: HedgeTradeReport,
    symbol_filter: str,
    start: date,
    end: date,
    path: Path | None = None,
) -> Path:
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    out = path or (EXPORT_DIR / export_filename(symbol_filter))
    headers = report.headers
    grid: list[list[CellSpec]] = [
        _border_row([FIELD_LABELS.get(h, h) for h in headers], bold=True)
    ]

    for row in report.rows:
        grid.append(_border_row(row.values(headers)))

    grid.append([])
    grid.append(_border_row(["汇总", "数值"], bold=True))
    grid.append(_border_row(["笔数", report.row_count]))
    grid.append(_border_row(["BA盈亏", report.ba_pnl]))
    grid.append(_border_row(["EX盈亏", report.ex_pnl]))
    grid.append(_border_row(["BA手续费", report.ba_commission]))
    grid.append(_border_row(["BA资费", report.ba_charges_total]))
    grid.append(_border_row(["净利润", report.total_pnl]))

    write_styled_xlsx(out, grid)
    return out
