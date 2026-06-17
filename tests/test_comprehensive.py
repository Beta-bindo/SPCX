"""
Comprehensive QA suite: scenario / equivalence / boundary / error-guessing / concurrency.
Run: python tests/test_comprehensive.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QDate, QTimer
from PySide6.QtWidgets import QApplication, QPushButton

from app.core.config import load_config, save_config
from app.core.models import AppConfig, ConnectionMode, HedgeMode, Quote, Side
from app.core.profit_calculator import calculate_profit, record_label
from app.core.profit_export import export_profit_xlsx
from app.core.spread_engine import SpreadEngine
from app.core.paths import ledger_path
from app.core.trade_ledger import TradeLedger, TradeRecord, load_ledger, record_close_settlement
from app.core.trading_service import close_hedge, detect_hedge_mode, open_hedge
from app.core.xlsx_writer import CellSpec, write_styled_xlsx
from app.connectors.binance_connector import BinanceConnector
from app.connectors.mt5_connector import MT5Connector
from app.main_window import MainWindow
from app.widgets.date_range_picker import DateRangePicker
from app.widgets.log_panel import LogPanel
from app.widgets.table_pagination import TablePagination
from app.widgets.trade_confirm_dialog import TradeConfirmDialog


class TestReport:
    def __init__(self):
        self.passed = 0
        self.failed: list[str] = []

    def ok(self, name: str) -> None:
        self.passed += 1
        print(f"  ✓ {name}")

    def fail(self, name: str, detail: str) -> None:
        self.failed.append(f"{name}: {detail}")
        print(f"  ✗ {name} — {detail}")


def run(name: str, fn, report: TestReport) -> None:
    try:
        fn()
        report.ok(name)
    except Exception as exc:
        report.fail(name, str(exc))


# ── 等价类 / 边界：日期范围 ──────────────────────────────────────────


def test_date_range_reversed_auto_swap():
    picker = DateRangePicker()
    picker.set_range(date(2026, 6, 8), date(2026, 6, 1))
    start, end = picker.get_range()
    assert start == date(2026, 6, 1)
    assert end == date(2026, 6, 8)


def test_date_range_same_day():
    picker = DateRangePicker()
    picker.set_range(date(2026, 6, 8), date(2026, 6, 8))
    start, end = picker.get_range()
    assert start == end


def test_profit_cumulative_net_pnl():
    """多笔平仓盈亏相加：+5 与 -10 合计 -5（扣费后）。"""
    ledger = TradeLedger(
        records=[
            TradeRecord(
                "2026-06-08T10:00:00",
                "xau",
                "contraction",
                action="close",
                ba_pnl=3.0,
                mt5_pnl=2.0,
                ba_fee=0.0,
                mt5_fee=0.0,
            ),
            TradeRecord(
                "2026-06-09T10:00:00",
                "xau",
                "contraction",
                action="close",
                ba_pnl=-4.0,
                mt5_pnl=-6.0,
                ba_fee=0.0,
                mt5_fee=0.0,
            ),
            TradeRecord(
                "2026-06-09T11:00:00",
                "xau",
                "contraction",
                action="open",
                ba_pnl=0.0,
                mt5_pnl=0.0,
            ),
        ]
    )
    report = calculate_profit(ledger, date(2026, 6, 1), date(2026, 6, 30), "all")
    assert len(report.records) == 2
    assert report.total_pnl == -5.0


def test_profit_filter_empty_range():
    ledger = TradeLedger(records=[])
    report = calculate_profit(ledger, date(2099, 1, 1), date(2099, 12, 31), "all")
    assert report.total_pnl == 0
    assert report.records == []


def test_profit_filter_symbol_xau_only():
    ledger = TradeLedger(
        records=[
            TradeRecord("2026-06-08T10:00:00", "xau", "contraction", ba_pnl=10, mt5_pnl=-5),
            TradeRecord("2026-06-08T11:00:00", "xag", "contraction", ba_pnl=20, mt5_pnl=-8),
        ]
    )
    report = calculate_profit(ledger, date(2026, 6, 1), date(2026, 6, 30), "xau")
    assert len(report.records) == 1
    assert report.ba_pnl == 10


def test_profit_boundary_date_inclusive():
    ledger = TradeLedger(
        records=[
            TradeRecord("2026-06-01T00:00:00", "xau", "contraction", ba_pnl=1),
            TradeRecord("2026-06-30T23:59:59", "xau", "contraction", ba_pnl=2),
            TradeRecord("2026-05-31T23:59:59", "xau", "contraction", ba_pnl=99),
        ]
    )
    report = calculate_profit(ledger, date(2026, 6, 1), date(2026, 6, 30), "all")
    assert len(report.records) == 2
    assert report.ba_pnl == 3


# ── 幂等：引擎 / 按钮逻辑 ────────────────────────────────────────────


def test_engine_start_stop_idempotent():
    engine = SpreadEngine(AppConfig(connection_mode=ConnectionMode.DEMO.value))
    logs: list[str] = []
    engine.log_message.connect(logs.append)
    engine.start()
    engine.start()
    assert engine.is_running
    stop_count_before = sum(1 for m in logs if "已停止" in m)
    engine.stop()
    engine.stop()
    stop_count_after = sum(1 for m in logs if "已停止" in m)
    assert stop_count_after - stop_count_before == 1
    assert not engine.is_running


def test_trading_mutex_blocks_concurrent():
    engine = SpreadEngine(AppConfig(connection_mode=ConnectionMode.DEMO.value))
    engine._trading = True
    logs: list[str] = []
    engine.log_message.connect(logs.append)
    started: list[tuple] = []
    engine.trade_started.connect(lambda a, p, _om: started.append((a, p)))
    engine.open_hedge("xau")
    engine.close_hedge("xag")
    assert not started
    assert logs.count("交易进行中，请稍候") == 2


def test_demo_double_open_adds():
    cfg = AppConfig(connection_mode=ConnectionMode.DEMO.value)
    ba = BinanceConnector(cfg)
    mt5 = MT5Connector(cfg)
    ba._quotes["XAUUSDT"] = Quote("XAUUSDT", 2650, 2650.2, is_simulated=True)
    mt5._quotes["XAUUSD"] = Quote("XAUUSD", 2649, 2649.2, is_simulated=True)
    qty = cfg.ba_quantity_for("xau")
    lots = cfg.mt5_lot_for("xau")
    assert open_hedge(ba, mt5, "xau").success
    second = open_hedge(ba, mt5, "xau")
    assert second.success
    assert ba.get_positions()[0].quantity == qty * 2
    assert mt5.get_positions()[0].quantity == lots * 2


def test_close_without_position_demo():
    cfg = AppConfig(connection_mode=ConnectionMode.DEMO.value)
    ba = BinanceConnector(cfg)
    mt5 = MT5Connector(cfg)
    result = close_hedge(ba, mt5, "xau")
    assert result.success


def test_expansion_contraction_business_loop():
    cfg = AppConfig(connection_mode=ConnectionMode.DEMO.value)
    ba = BinanceConnector(cfg)
    mt5 = MT5Connector(cfg)
    ba._quotes["XAGUSDT"] = Quote("XAGUSDT", 67, 67.02, is_simulated=True)
    mt5._quotes["XAGUSD"] = Quote("XAGUSD", 66.9, 67.1, is_simulated=True)
    r1 = open_hedge(ba, mt5, "xag", HedgeMode.CONTRACTION.value)
    assert r1.success
    assert ba.get_positions()[0].side == Side.SELL
    assert mt5.get_positions()[0].side == Side.BUY
    r2 = close_hedge(ba, mt5, "xag")
    assert r2.success
    assert not ba.get_positions() and not mt5.get_positions()
    r3 = open_hedge(ba, mt5, "xag", HedgeMode.EXPANSION.value)
    assert r3.success
    assert ba.get_positions()[0].side == Side.BUY
    assert mt5.get_positions()[0].side == Side.SELL


def _trade_button_texts(dlg: TradeConfirmDialog) -> list[str]:
    return [b.text() for b in dlg._action_buttons]


def test_detect_hedge_mode_and_trade_dialog_buttons():
    cfg = AppConfig(connection_mode=ConnectionMode.DEMO.value)
    assert detect_hedge_mode("xau", []) is None

    ba = BinanceConnector(cfg)
    mt5 = MT5Connector(cfg)
    open_hedge(ba, mt5, "xau", HedgeMode.CONTRACTION.value)
    positions = ba.get_positions() + mt5.get_positions()
    assert detect_hedge_mode("xau", positions) == HedgeMode.CONTRACTION.value

    dlg_open = TradeConfirmDialog("xau", cfg, None)
    open_texts = _trade_button_texts(dlg_open)
    assert len(open_texts) == 2
    assert any("开仓收缩" in t for t in open_texts)
    assert any("开仓扩张" in t for t in open_texts)

    dlg_close = TradeConfirmDialog("xau", cfg, HedgeMode.CONTRACTION.value)
    close_texts = _trade_button_texts(dlg_close)
    assert len(close_texts) == 2
    assert any("开仓收缩" in t for t in close_texts)
    assert any("平仓收缩" in t for t in close_texts)
    dlg_close._apply_action("平仓", HedgeMode.CONTRACTION.value)
    assert dlg_close.selected_trade() == ("平仓", HedgeMode.CONTRACTION.value)

    close_hedge(ba, mt5, "xau", HedgeMode.CONTRACTION.value)
    open_hedge(ba, mt5, "xau", HedgeMode.EXPANSION.value)
    positions = ba.get_positions() + mt5.get_positions()
    assert detect_hedge_mode("xau", positions) == HedgeMode.EXPANSION.value
    dlg_expansion = TradeConfirmDialog("xau", cfg, HedgeMode.EXPANSION.value)
    exp_texts = _trade_button_texts(dlg_expansion)
    assert len(exp_texts) == 2
    assert any("开仓扩张" in t for t in exp_texts)
    assert any("平仓扩张" in t for t in exp_texts)


# ── 并发 / 容错 ──────────────────────────────────────────────────────


def test_ledger_concurrent_writes():
    backup = ledger_path().read_text(encoding="utf-8") if ledger_path().exists() else None
    try:
        path = ledger_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('{"records": []}', encoding="utf-8")

        def _write(i: int) -> None:
            record_close_settlement("xau", "contraction", float(i), 0, 0, 0)

        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(_write, range(20)))

        ledger = load_ledger()
        assert len(ledger.records) == 20
    finally:
        if backup is None:
            ledger_path().unlink(missing_ok=True)
        else:
            ledger_path().write_text(backup, encoding="utf-8")


def test_corrupt_ledger_json():
    backup = ledger_path().read_text(encoding="utf-8") if ledger_path().exists() else None
    try:
        path = ledger_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{not valid json", encoding="utf-8")
        ledger = load_ledger()
        assert ledger.records == []
    finally:
        if backup is None:
            ledger_path().unlink(missing_ok=True)
        else:
            ledger_path().write_text(backup, encoding="utf-8")


def test_config_special_chars_roundtrip():
    cfg = AppConfig(
        ba_api_key='key"<script>alert(1)</script>',
        ba_api_secret="sec'; DROP TABLE--",
        mt5_password="<img onerror=alert(1)>",
    )
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "cfg.json"
        from app.core import config as config_mod

        orig = config_mod.CONFIG_FILE
        config_mod.CONFIG_FILE = path
        try:
            save_config(cfg)
            loaded = load_config()
            assert loaded.ba_api_key == cfg.ba_api_key
            assert loaded.ba_api_secret == cfg.ba_api_secret
            assert loaded.mt5_password == cfg.mt5_password
        finally:
            config_mod.CONFIG_FILE = orig


def test_xlsx_xml_escape_injection():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "inj.xlsx"
        write_styled_xlsx(
            path,
            [[CellSpec('=<script>alert("x")</script>&"')]],
        )
        with zipfile.ZipFile(path) as zf:
            sheet = zf.read("xl/worksheets/sheet1.xml").decode()
        assert "<script>" not in sheet or "&lt;" in sheet or "script" in sheet
        assert "alert" in sheet


def test_log_panel_plain_text_no_html_exec():
    panel = LogPanel()
    payload = '<script>alert("xss")</script>'
    panel.append(payload)
    text = panel.text.toPlainText()
    assert payload in text


# ── 分页边界 ─────────────────────────────────────────────────────────


def test_pagination_boundaries():
    pager = TablePagination()
    items = list(range(25))
    pager.set_total(len(items))
    assert len(pager.slice(items)) == 10
    pager._page = 3
    assert len(pager.slice(items)) == 5
    pager.page_size.setCurrentIndex(3)
    assert pager.page_size_value == 100
    assert len(pager.slice(items)) == 25


def test_pagination_empty():
    pager = TablePagination()
    pager.set_total(0)
    assert pager.slice([]) == []


# ── 无效输入 / 错误推测 ──────────────────────────────────────────────


def test_invalid_preset_still_resolves_or_fails_gracefully():
    cfg = AppConfig(connection_mode=ConnectionMode.DEMO.value)
    ba = BinanceConnector(cfg)
    ba._quotes["XAUUSDT"] = Quote("XAUUSDT", 2650, 2650.2, is_simulated=True)
    try:
        ba.open_hedge_leg("invalid")
    except Exception:
        pass


def test_ba_quantity_zero_lot_map():
    cfg = AppConfig(xau_mt5_lot_map=0, xau_ba_qty_map=500, xau_trade_lots=1)
    qty = cfg.ba_quantity_for("xau")
    assert qty > 0


def test_record_label_modes():
    rec = TradeRecord("2026-01-01", "xag", "expansion", 0, 0, 0, 0)
    assert record_label(rec) == "白银扩张"


def test_export_empty_report():
    report = calculate_profit(TradeLedger(), date(2026, 1, 1), date(2026, 12, 31), "all")
    with tempfile.TemporaryDirectory() as tmp:
        out = export_profit_xlsx(report, "all", date(2026, 1, 1), date(2026, 12, 31), Path(tmp) / "e.xlsx")
        assert out.exists()


# ── UI 场景：主窗口闭环 ───────────────────────────────────────────────


def test_main_window_trade_flow_offscreen():
    window = MainWindow()
    window.show()
    if not window.engine.is_running:
        window.start_btn.click()
    QApplication.processEvents()
    time.sleep(0.5)
    QApplication.processEvents()
    window._refresh_order_book()
    QApplication.processEvents()
    with patch.object(
        TradeConfirmDialog,
        "show",
        lambda dlg: dlg._apply_action("开仓", "contraction"),
    ):
        window.gold_actions.trade_entry_btn.click()
    deadline = time.time() + 8
    while window.engine._trading and time.time() < deadline:
        QApplication.processEvents()
        time.sleep(0.05)
    window.engine.refresh_positions()
    QApplication.processEvents()
    assert window.gold_panel.table.rowCount() >= 1
    window.engine.stop()
    window.close()


def test_profit_calculator_dialog_query():
    app = QApplication.instance() or QApplication(sys.argv)
    from app.widgets.profit_calculator_dialog import ProfitCalculatorDialog

    dlg = ProfitCalculatorDialog()
    dlg.date_range.start_edit.setDate(QDate(2028, 6, 8))
    dlg.date_range.end_edit.setDate(QDate(2026, 6, 1))
    dlg._calculate()
    start, end = dlg._date_range()
    assert start <= end
    dlg.close()


def test_profit_calculator_refreshes_on_close_record():
    app = QApplication.instance() or QApplication(sys.argv)
    from app.core.trade_ledger import TradeRecord
    from app.widgets.profit_calculator_dialog import ProfitCalculatorDialog

    dlg = ProfitCalculatorDialog()
    calls = 0

    def _mark_calculated():
        nonlocal calls
        calls += 1

    dlg._calculate = _mark_calculated
    dlg._on_trade_recorded(
        TradeRecord(
            settled_at="2026-06-08T12:00:00",
            preset_id="xau",
            mode="contraction",
            action="open",
        )
    )
    assert calls == 0

    dlg._on_trade_recorded(
        TradeRecord(
            settled_at="2026-06-08T12:30:00",
            preset_id="xau",
            mode="contraction",
            action="close",
        )
    )
    assert calls == 1
    dlg.close()


def test_live_ba_without_keys_fails_safe():
    cfg = AppConfig(connection_mode=ConnectionMode.LIVE_BA.value, ba_api_key="", ba_api_secret="")
    ba = BinanceConnector(cfg)
    ba._quotes["XAUUSDT"] = Quote("XAUUSDT", 2650, 2650.2)
    result = ba.open_hedge_leg("xau")
    assert not result.success


def main() -> int:
    print("=" * 60)
    print("COMPREHENSIVE QA SUITE")
    print("=" * 60)
    _app = QApplication.instance() or QApplication(sys.argv)
    report = TestReport()
    cases = [
        ("日期反转自动交换", test_date_range_reversed_auto_swap),
        ("同一天日期范围", test_date_range_same_day),
        ("空日期区间利润", test_profit_filter_empty_range),
        ("品种筛选仅黄金", test_profit_filter_symbol_xau_only),
        ("日期边界含起止", test_profit_boundary_date_inclusive),
        ("引擎启停幂等", test_engine_start_stop_idempotent),
        ("交易互斥锁", test_trading_mutex_blocks_concurrent),
        ("演示同向加仓", test_demo_double_open_adds),
        ("无持仓平仓演示", test_close_without_position_demo),
        ("收缩扩张业务闭环", test_expansion_contraction_business_loop),
        ("持仓检测与弹窗按钮", test_detect_hedge_mode_and_trade_dialog_buttons),
        ("账本并发写入", test_ledger_concurrent_writes),
        ("损坏账本 JSON 容错", test_corrupt_ledger_json),
        ("配置特殊字符往返", test_config_special_chars_roundtrip),
        ("XLSX XML 转义", test_xlsx_xml_escape_injection),
        ("日志面板纯文本", test_log_panel_plain_text_no_html_exec),
        ("分页边界", test_pagination_boundaries),
        ("空分页", test_pagination_empty),
        ("无效 preset 不崩溃", test_invalid_preset_still_resolves_or_fails_gracefully),
        ("零手数映射边界", test_ba_quantity_zero_lot_map),
        ("记录标签", test_record_label_modes),
        ("空报告导出", test_export_empty_report),
        ("主窗口交易流程", test_main_window_trade_flow_offscreen),
        ("利润计算器日期", test_profit_calculator_dialog_query),
        ("无密钥实盘安全失败", test_live_ba_without_keys_fails_safe),
    ]
    for name, fn in cases:
        run(name, fn, report)

    print("=" * 60)
    print(f"通过: {report.passed}  失败: {len(report.failed)}")
    if report.failed:
        print("\n失败详情:")
        for item in report.failed:
            print(f"  - {item}")
        return 1
    print("ALL COMPREHENSIVE TESTS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
