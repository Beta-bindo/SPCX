"""Order mode resolution for gold/silver connectors and auto trade."""

from __future__ import annotations

from app.core.models import GoldOrderMode
from app.core.order_mode import (
    auto_trade_lane,
    auto_trade_order_mode,
    order_mode_log_label,
    resolve_execution_flags,
)


def test_resolve_execution_flags_gold():
    assert resolve_execution_flags("xau", GoldOrderMode.MARKET.value) == (False, False)
    assert resolve_execution_flags("xau", GoldOrderMode.MAKER.value) == (True, True)
    assert resolve_execution_flags("xau", GoldOrderMode.LIMIT.value) == (True, False)


def test_resolve_execution_flags_silver():
    assert resolve_execution_flags("xag", GoldOrderMode.MARKET.value) == (False, False)
    assert resolve_execution_flags("xag", GoldOrderMode.MAKER.value) == (True, True)
    assert resolve_execution_flags("xag", GoldOrderMode.LIMIT.value) == (True, False)


def test_auto_trade_order_mode():
    assert auto_trade_order_mode("xau", "maker") == GoldOrderMode.MAKER.value
    assert auto_trade_order_mode("xau", "market") == GoldOrderMode.MARKET.value
    assert auto_trade_order_mode("xag", "market") == GoldOrderMode.MARKET.value


def test_auto_trade_lane():
    assert auto_trade_lane("xau", GoldOrderMode.MARKET.value) == "market"
    assert auto_trade_lane("xau", GoldOrderMode.MAKER.value) == "maker"
    assert auto_trade_lane("xag", GoldOrderMode.MARKET.value) == "market"
    assert auto_trade_lane("xag", GoldOrderMode.MAKER.value) == "market"


def test_order_mode_log_label():
    assert order_mode_log_label("xau", GoldOrderMode.MARKET.value) == "市价"
    assert order_mode_log_label("xau", GoldOrderMode.MAKER.value) == "Maker"
    assert order_mode_log_label("xag", GoldOrderMode.LIMIT.value) == "限价"


def main() -> int:
    tests = [
        test_resolve_execution_flags_gold,
        test_resolve_execution_flags_silver,
        test_auto_trade_order_mode,
        test_auto_trade_lane,
        test_order_mode_log_label,
    ]
    for fn in tests:
        fn()
        print(f"  ok {fn.__name__}")
    print("ALL ORDER MODE TESTS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
