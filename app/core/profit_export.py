from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

from app.core.paths import exports_dir
from app.core.official_profit import OFFICIAL_FIELD_LABELS, OfficialProfitReport
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


def _compat_headers(report: ProfitReport | OfficialProfitReport) -> list[str]:
    if isinstance(report, OfficialProfitReport):
        return report.headers
    return [
        "settled_at",
        "product",
        "direction",
        "ba_qty",
        "mt5_qty",
        "spread",
        "ba_pnl",
        "ex_pnl",
        "fee",
        "ba_charges",
        "profit",
    ]


def _compat_row_values(report: ProfitReport | OfficialProfitReport, row) -> list:
    if isinstance(report, OfficialProfitReport):
        return row.values(report.headers)
    return [
        getattr(row, "settled_at", ""),
        getattr(row, "product", ""),
        getattr(row, "direction", ""),
        getattr(row, "ba_qty", ""),
        getattr(row, "mt5_qty", ""),
        getattr(row, "spread", ""),
        getattr(row, "ba_pnl", ""),
        getattr(row, "ex_pnl", ""),
        getattr(row, "fee", ""),
        getattr(row, "ba_charges", ""),
        getattr(row, "profit", ""),
    ]


def export_profit_xlsx(
    report: ProfitReport | OfficialProfitReport,
    symbol_filter: str,
    start: date,
    end: date,
    path: Path | None = None,
) -> Path:
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    out = path or (EXPORT_DIR / export_filename(symbol_filter))
    headers = _compat_headers(report)
    grid: list[list[CellSpec]] = [
        _border_row([OFFICIAL_FIELD_LABELS.get(h, h) for h in headers], bold=True)
    ]

    for row in getattr(report, "rows", []) or []:
        grid.append(_border_row(_compat_row_values(report, row)))

    grid.append([])
    grid.append(_border_row(["汇总", "数值"], bold=True))
    if isinstance(report, OfficialProfitReport):
        grid.append(_border_row(["笔数", report.row_count]))
        grid.append(_border_row(["BA利润", report.ba_pnl]))
        grid.append(_border_row(["BA手续费", report.ba_commission]))
        grid.append(_border_row(["BA资金费", report.ba_funding_fee]))
        grid.append(_border_row(["BA返佣", report.ba_rebate]))
        grid.append(_border_row(["BA资费合计", report.ba_charges]))
        grid.append(_border_row(["Exness利润", report.mt5_profit]))
        grid.append(_border_row(["Exness手续费", report.mt5_commission]))
        grid.append(_border_row(["Exness杂费", report.mt5_fee + report.mt5_swap]))
        grid.append(_border_row(["总费用", round(report.ba_commission + report.mt5_charges, 4)]))
        grid.append(_border_row(["总利润", report.total_pnl]))
    else:
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
