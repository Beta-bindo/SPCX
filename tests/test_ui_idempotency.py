"""Button and engine idempotency checks."""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from app.core.models import AppConfig, ConnectionMode
from app.core.spread_engine import SpreadEngine
from app.core.trade_result import HedgeTradeResult, LegResult
from app.main_window import MainWindow


def test_header_buttons_are_idempotent() -> None:
    app = QApplication.instance() or QApplication(sys.argv)
    window = MainWindow()

    window.save_btn.click()
    window.save_btn.click()
    window.start_btn.click()
    window.start_btn.click()
    assert window.engine.is_running
    window.stop_btn.click()
    window.stop_btn.click()
    assert not window.engine.is_running

    window.close()
    print("  ✓ 顶部保存/启动/停止重复点击")


def test_trading_guard_prevents_duplicate_orders() -> None:
    engine = SpreadEngine(AppConfig(connection_mode=ConnectionMode.DEMO.value))
    started: list[tuple[str, str]] = []
    logs: list[str] = []
    engine.trade_started.connect(lambda action, preset, _om: started.append((action, preset)))
    engine.log_message.connect(logs.append)

    engine._trading = True
    engine.open_hedge("xau")
    engine.close_hedge("xau")

    assert not started
    assert logs == ["交易进行中，请稍候", "交易进行中，请稍候"]
    print("  ✓ 交易中重复开平仓不会重复下单")


def test_close_compensation_log_uses_restore_word() -> None:
    engine = SpreadEngine(AppConfig(connection_mode=ConnectionMode.DEMO.value))
    logs: list[str] = []
    engine.log_message.connect(logs.append)

    result = HedgeTradeResult(
        action="close",
        success=False,
        legs=[
            LegResult(
                platform="BA",
                success=True,
                message="BA 平仓成功",
                compensated=True,
                compensation_message="BA 已按刚平仓量 250 市价恢复对冲",
                filled_quantity=250.0,
            ),
            LegResult(platform="MT5", success=False, message="Exness 平仓失败"),
        ],
        message="部分成功",
    )

    engine._log_trade_leg_details(result)

    assert any("↳ 恢复成功" in line for line in logs)
    assert not any("↳ 回滚成功" in line for line in logs)
    print("  ✓ 平仓补偿日志使用恢复文案")


def main() -> int:
    errors: list[str] = []
    for fn in (
        test_header_buttons_are_idempotent,
        test_trading_guard_prevents_duplicate_orders,
        test_close_compensation_log_uses_restore_word,
    ):
        try:
            fn()
        except Exception as exc:
            errors.append(f"{fn.__name__}: {exc}")

    if errors:
        print("UI IDEMPOTENCY TEST FAILED:")
        for err in errors:
            print(f"  - {err}")
        return 1
    print("ALL UI IDEMPOTENCY TESTS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
