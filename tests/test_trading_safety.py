from __future__ import annotations

from app.core.models import GoldOrderMode, HedgeMode, Position, Side
from app.core.trade_result import LegResult
from app.core.trading_service import open_hedge, spread_allows_add


class _FakeConnector:
    def __init__(
        self,
        platform: str,
        open_success: bool,
        *,
        needs_reconciliation: bool = False,
    ) -> None:
        self.platform = platform
        self.open_success = open_success
        self.needs_reconciliation = needs_reconciliation
        self.close_calls = 0

    def get_positions(self, force=False):
        return []

    def open_hedge_leg(
        self,
        preset_id: str,
        mode: str,
        order_mode: str,
        *,
        on_fill_delta=None,
        lots_override=None,
    ) -> LegResult:
        return LegResult(
            platform=self.platform,
            success=self.open_success,
            message="opened" if self.open_success else "failed",
            needs_reconciliation=self.needs_reconciliation,
            filled_quantity=1.0 if self.open_success else 0.0,
        )

    def close_hedge_leg(
        self, preset_id: str, order_mode: str, mode: str = "contraction", **kwargs
    ) -> LegResult:
        self.close_calls += 1
        return LegResult(platform=self.platform, success=True, message="rolled back")


def test_open_hedge_rolls_back_binance_when_mt5_fails():
    ba = _FakeConnector("BA", open_success=True)
    mt5 = _FakeConnector("MT5", open_success=False)

    result = open_hedge(ba, mt5, "xau", HedgeMode.CONTRACTION.value)

    assert result.success is False
    assert result.partial is True
    assert ba.close_calls == 1
    assert result.legs[0].compensated is True
    assert "自动回滚" in result.message


def test_open_hedge_rolls_back_mt5_when_binance_fails_market():
    # 市价为并行下单：两腿同时下，BA 失败需回滚已成交的 EX
    ba = _FakeConnector("BA", open_success=False)
    mt5 = _FakeConnector("MT5", open_success=True)

    result = open_hedge(
        ba, mt5, "xau", HedgeMode.EXPANSION.value, GoldOrderMode.MARKET.value
    )

    assert result.success is False
    assert result.partial is True
    assert mt5.close_calls == 1
    assert result.legs[1].compensated is True
    assert "自动回滚" in result.message


def test_open_hedge_skips_mt5_when_binance_fails_maker():
    # Maker/限价为顺序下单：BA 未成交则不下 EX，也无需回滚
    ba = _FakeConnector("BA", open_success=False)
    mt5 = _FakeConnector("MT5", open_success=True)

    result = open_hedge(
        ba, mt5, "xau", HedgeMode.CONTRACTION.value, GoldOrderMode.MAKER.value
    )

    assert result.success is False
    assert mt5.close_calls == 0
    assert result.legs[1].success is False
    assert "跳过" in result.legs[1].message


def test_open_hedge_rolls_back_unconfirmed_order():
    ba = _FakeConnector("BA", open_success=False, needs_reconciliation=True)
    mt5 = _FakeConnector("MT5", open_success=False)

    result = open_hedge(ba, mt5, "xau", HedgeMode.CONTRACTION.value)

    assert result.success is False
    assert result.partial is True
    assert ba.close_calls == 1
    assert result.legs[0].compensated is True
    assert "自动回滚" in result.message


def test_spread_allows_add_blocks_worse_contraction_spread():
    positions = [
        Position(
            platform="BA",
            symbol="XAUUSDT",
            side=Side.SELL,
            quantity=500,
            entry_price=2650.0,
        ),
        Position(
            platform="MT5",
            symbol="XAUUSD",
            side=Side.BUY,
            quantity=1.0,
            entry_price=2648.0,
        ),
    ]
    ok, reason = spread_allows_add(
        "xau", positions, spread=1.5, mode=HedgeMode.CONTRACTION.value
    )
    assert ok is False
    assert reason is not None
    assert "暂不加仓" in reason

    ok2, _ = spread_allows_add(
        "xau", positions, spread=2.5, mode=HedgeMode.CONTRACTION.value
    )
    assert ok2 is True


def test_spread_allows_add_blocks_worse_expansion_spread():
    positions = [
        Position(
            platform="BA",
            symbol="XAUUSDT",
            side=Side.BUY,
            quantity=500,
            entry_price=2650.0,
        ),
        Position(
            platform="MT5",
            symbol="XAUUSD",
            side=Side.SELL,
            quantity=1.0,
            entry_price=2652.0,
        ),
    ]
    ok, reason = spread_allows_add(
        "xau", positions, spread=-1.5, mode=HedgeMode.EXPANSION.value
    )
    assert ok is False
    assert reason is not None

    ok2, _ = spread_allows_add(
        "xau", positions, spread=-2.5, mode=HedgeMode.EXPANSION.value
    )
    assert ok2 is True


def main() -> int:
    test_open_hedge_rolls_back_binance_when_mt5_fails()
    test_open_hedge_rolls_back_mt5_when_binance_fails_market()
    test_open_hedge_skips_mt5_when_binance_fails_maker()
    test_open_hedge_rolls_back_unconfirmed_order()
    test_spread_allows_add_blocks_worse_contraction_spread()
    test_spread_allows_add_blocks_worse_expansion_spread()
    print("ALL TRADING SAFETY TESTS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
