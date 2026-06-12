"""Tests for log level filtering and trade log formatting."""

from __future__ import annotations

from app.core.app_log import (
    LogLevel,
    hedge_action_label,
    should_log,
    trade_leg_success_msg,
)
from app.core.models import HedgeMode


def test_should_log_quiet_shows_trade_not_info():
    assert should_log("quiet", LogLevel.ERROR) is True
    assert should_log("quiet", LogLevel.TRADE) is True
    assert should_log("quiet", LogLevel.INFO) is False
    assert should_log("quiet", LogLevel.DEBUG) is False


def test_should_log_verbose_shows_all():
    assert should_log("verbose", LogLevel.DEBUG) is True


def test_hedge_action_label():
    assert hedge_action_label("open", HedgeMode.CONTRACTION.value) == "开仓收缩"
    assert hedge_action_label("open", HedgeMode.EXPANSION.value, adding=True) == "加仓扩张"
    assert hedge_action_label("close", HedgeMode.EXPANSION.value) == "平仓扩张"


def test_trade_leg_success_msg():
    text = trade_leg_success_msg(
        "BA",
        "open",
        HedgeMode.CONTRACTION.value,
        "12345",
        qty="500",
        order_type="限价",
    )
    assert "【BA】" in text
    assert "开仓收缩成功" in text
    assert "订单 12345" in text
    assert "数量 500" in text


def test_trade_close_leg_msg():
    text = trade_leg_success_msg("Exness", "close", HedgeMode.EXPANSION.value, "99", lots="1.0")
    assert "【Exness】平仓扩张成功" in text
    assert "订单 99" in text


def main() -> int:
    test_should_log_quiet_shows_trade_not_info()
    test_should_log_verbose_shows_all()
    test_hedge_action_label()
    test_trade_leg_success_msg()
    test_trade_close_leg_msg()
    print("ALL APP LOG TESTS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
