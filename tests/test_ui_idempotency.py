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


def main() -> int:
    errors: list[str] = []
    for fn in (test_header_buttons_are_idempotent, test_trading_guard_prevents_duplicate_orders):
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
