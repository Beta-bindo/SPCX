"""Profit export tests."""

import sys
import tempfile
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.profit_calculator import ProfitReport, calculate_profit
from app.core.profit_export import export_filename, export_profit_xlsx
from app.core.trade_ledger import TradeLedger, TradeRecord
from app.core.xlsx_writer import write_xlsx


def test_export_filename():
    name = export_filename("all")
    assert name.startswith("利润_全部_")
    assert name.endswith(".xlsx")
    assert export_filename("xau").startswith("利润_黄金_")
    assert export_filename("xag").startswith("利润_白银_")
    print("  ✓ 导出文件名格式")


def test_xlsx_export():
    import zipfile

    ledger = TradeLedger(
        records=[
            TradeRecord(
                settled_at="2026-06-08T11:02:00",
                preset_id="xau",
                mode="contraction",
                ba_pnl=100.0,
                mt5_pnl=-20.0,
                ba_fee=1.0,
                mt5_fee=0.5,
            )
        ]
    )
    report = calculate_profit(ledger, date(2026, 6, 1), date(2026, 6, 8), "all")
    with tempfile.TemporaryDirectory() as tmp:
        out = export_profit_xlsx(report, "all", date(2026, 6, 1), date(2026, 6, 8), Path(tmp) / "test.xlsx")
        assert out.exists()
        assert out.stat().st_size > 100
        with zipfile.ZipFile(out) as zf:
            sheet = zf.read("xl/worksheets/sheet1.xml").decode()
        assert "查询条件" not in sheet
        assert "产品" in sheet and "总利润" in sheet
    print("  ✓ xlsx 导出")


def test_xlsx_writer():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "sheet.xlsx"
        write_xlsx(path, [["A", "B"], [1, 2]])
        assert path.exists()
    print("  ✓ xlsx 写入器")


if __name__ == "__main__":
    print("Profit export tests:")
    test_export_filename()
    test_xlsx_writer()
    test_xlsx_export()
    print("ALL PROFIT EXPORT TESTS PASSED")
