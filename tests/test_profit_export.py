"""Profit export tests."""

import sys
import tempfile
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.hedge_trade_report import HedgeTradeReport, HedgeTradeRow, build_row_from_settlement
from app.core.profit_export import export_filename, export_profit_xlsx
from app.core.xlsx_writer import write_xlsx


def _sample_row(**kwargs) -> HedgeTradeRow:
    row = build_row_from_settlement(
        preset_id="xau",
        mode="contraction",
        action="close",
        ba_order_no="7001",
        ex_order_no="8001",
        ba_qty=500.0,
        ex_qty=1.0,
        ba_open_price=3.0,
        ba_close_price=1.5,
        ba_pnl=10.0,
        ex_pnl=-3.0,
        ba_charges=-2.5,
        ba_commission=1.0,
        order_time="2026-06-08 11:02:00",
    )
    for key, value in kwargs.items():
        setattr(row, key, value)
    return row


def test_export_filename():
    name = export_filename("all")
    assert name.startswith("利润明细_全部_")
    assert name.endswith(".xlsx")
    assert export_filename("xau").startswith("利润明细_黄金_")
    assert export_filename("xag").startswith("利润明细_白银_")
    print("  ✓ 导出文件名格式")


def test_hedge_report_totals():
    report = HedgeTradeReport(
        rows=[
            _sample_row(),
            _sample_row(
                ba_pnl="+5.0000",
                net_profit="+3.5000",
                ba_charges="+0.8000",
                ba_commission="-0.2000",
            ),
        ]
    )
    assert report.ba_pnl == 15.0
    assert report.ba_charges_total == -1.7
    assert report.total_pnl == 8.0
    print("  ✓ 对冲报表汇总")


def test_build_row_from_settlement_open():
    row = build_row_from_settlement(
        preset_id="xau",
        mode="contraction",
        action="open",
        ba_order_no="7001",
        ex_order_no="8001",
        ba_qty=500.0,
        ex_qty=1.0,
        ba_open_price=3.125,
        ba_commission=0.25,
    )
    assert row.product == "黄金"
    assert row.direction == "收缩"
    assert row.ba_close_price == "--"
    assert row.ba_open_price == "3.1250"
    assert row.record_key
    payload = row.to_payload()
    assert payload["product"] == "黄金"
    assert "record_key" in payload
    print("  ✓ 实盘结算行构造与上报 payload")


def test_xlsx_export():
    import zipfile

    report = HedgeTradeReport(rows=[_sample_row()])
    with tempfile.TemporaryDirectory() as tmp:
        out = export_profit_xlsx(
            report, "all", date(2026, 6, 1), date(2026, 6, 8), Path(tmp) / "test.xlsx"
        )
        assert out.exists()
        assert out.stat().st_size > 100
        with zipfile.ZipFile(out) as zf:
            sheet = zf.read("xl/worksheets/sheet1.xml").decode()
        assert "BA订单号" in sheet
        assert "净利润" in sheet
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
    test_hedge_report_totals()
    test_build_row_from_settlement_open()
    test_xlsx_export()
    print("ALL PROFIT EXPORT TESTS PASSED")
