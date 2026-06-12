"""Pre-release checks: memory smoke, import graph, no test leakage."""

from __future__ import annotations

import gc
import sys
import tracemalloc
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def check_imports_and_memory() -> list[str]:
    errors: list[str] = []
    tracemalloc.start()
    snap_before = tracemalloc.take_snapshot()

    from app.core.config import AppConfig
    from app.core.liquidation import resolve_position_liq_buffer
    from app.core.models import ConnectionMode, Position, Quote, Side
    from app.core.risk import build_risk_snapshot
    from app.core.spread_engine import SpreadEngine
    from app.core.trading_service import open_hedge, hedge_strategy_label_for_leg
    from app.connectors.binance_connector import BinanceConnector
    from app.connectors.mt5_connector import MT5Connector

    cfg = AppConfig(connection_mode=ConnectionMode.DEMO.value)
    ba = BinanceConnector(cfg)
    mt5 = MT5Connector(cfg)
    ba._quotes["XAUUSDT"] = Quote("XAUUSDT", 2650, 2650.2, is_simulated=True)
    mt5._quotes["XAUUSD"] = Quote("XAUUSD", 2649, 2649.2, is_simulated=True)
    open_hedge(ba, mt5, "xau")
    positions = ba.get_positions() + mt5.get_positions()
    build_risk_snapshot(
        positions,
        {"XAUUSDT": ba._quotes["XAUUSDT"]},
        {"XAUUSD": mt5._quotes["XAUUSD"]},
        cfg,
    )
    assert hedge_strategy_label_for_leg("BA", Side.SELL) == "收缩"
    resolve_position_liq_buffer(positions[0], ba._quotes["XAUUSDT"], "xau", 20)

    del ba, mt5, positions, cfg
    gc.collect()

    snap_after = tracemalloc.take_snapshot()
    stats = snap_after.compare_to(snap_before, "lineno")
    growth_mb = sum(s.size_diff for s in stats[:20]) / (1024 * 1024)
    tracemalloc.stop()
    if growth_mb > 50:
        errors.append(f"内存增长异常: {growth_mb:.1f} MB")
    return errors


def check_no_test_modules_importable_from_app() -> list[str]:
    errors: list[str] = []
    for name in list(sys.modules):
        if name == "tests" or name.startswith("tests.") or name.startswith("pytest"):
            errors.append(f"运行时导入了测试模块: {name}")
    return errors


def main() -> int:
    problems: list[str] = []
    problems.extend(check_imports_and_memory())
    problems.extend(check_no_test_modules_importable_from_app())
    if problems:
        for p in problems:
            print(f"[FAIL] {p}")
        return 1
    print("PRE-RELEASE CHECK OK: 核心导入/内存冒烟通过，未加载 tests/pytest")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
