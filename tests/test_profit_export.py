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


def test_calculate_profit_includes_ba_funding_and_rebate():
    ledger = TradeLedger(
        records=[
            TradeRecord(
                settled_at="2026-06-08T11:02:00",
                preset_id="xau",
                mode="contraction",
                action="close",
                ba_pnl=100.0,
                mt5_pnl=-20.0,
                ba_fee=1.0,
                mt5_fee=0.5,
                ba_funding_fee=-2.5,
                ba_rebate=0.8,
            )
        ]
    )
    report = calculate_profit(ledger, date(2026, 6, 1), date(2026, 6, 8), "all")
    assert report.ba_funding_fee == -2.5
    assert report.ba_rebate == 0.8
    assert report.ba_charges == -1.7
    assert report.total_pnl == round(100 - 20 - 1 - 0.5 - 2.5 + 0.8, 2)
    assert report.rows[0].ba_charges == -1.7
    print("  ✓ 利润统计含 BA 资金费与返佣")


def test_calculate_profit_allocates_opening_fee_to_close():
    ledger = TradeLedger(
        records=[
            TradeRecord(
                settled_at="2026-06-08T10:00:00",
                preset_id="xau",
                mode="contraction",
                action="open",
                ba_quantity=2.0,
                mt5_quantity=0.02,
                ba_fee=4.0,
                mt5_fee=0.4,
            ),
            TradeRecord(
                settled_at="2026-06-08T10:30:00",
                preset_id="xau",
                mode="contraction",
                action="close",
                ba_quantity=2.0,
                mt5_quantity=0.02,
                ba_pnl=20.0,
                mt5_pnl=-3.0,
                ba_fee=2.0,
                mt5_fee=0.2,
            ),
        ]
    )
    report = calculate_profit(ledger, date(2026, 6, 8), date(2026, 6, 8), "all")
    assert report.ba_fee == 6.0
    assert report.mt5_fee == 0.6
    assert report.rows[0].opening_fee == 4.4
    assert report.rows[0].fee == 6.6
    assert report.total_pnl == round(20 - 3 - 6.6, 2)
    print("  ✓ 利润统计扣开仓和平仓双边手续费")


def test_calculate_profit_allocates_opening_fee_after_prior_partial_close():
    ledger = TradeLedger(
        records=[
            TradeRecord(
                settled_at="2026-06-08T10:00:00",
                preset_id="xau",
                mode="contraction",
                action="open",
                ba_quantity=2.0,
                ba_fee=4.0,
            ),
            TradeRecord(
                settled_at="2026-06-08T11:00:00",
                preset_id="xau",
                mode="contraction",
                action="close",
                ba_quantity=1.0,
                ba_pnl=10.0,
                ba_fee=1.0,
            ),
            TradeRecord(
                settled_at="2026-06-09T11:00:00",
                preset_id="xau",
                mode="contraction",
                action="close",
                ba_quantity=1.0,
                ba_pnl=10.0,
                ba_fee=1.0,
            ),
        ]
    )
    report = calculate_profit(ledger, date(2026, 6, 9), date(2026, 6, 9), "all")
    assert len(report.records or []) == 1
    assert report.ba_fee == 3.0
    assert report.rows[0].opening_fee == 2.0
    assert report.total_pnl == 7.0
    print("  ✓ 跨日期部分平仓正确分摊开仓手续费")


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
    test_calculate_profit_includes_ba_funding_and_rebate()
    test_calculate_profit_allocates_opening_fee_to_close()
    test_calculate_profit_allocates_opening_fee_after_prior_partial_close()
    test_xlsx_export()
    print("ALL PROFIT EXPORT TESTS PASSED")
