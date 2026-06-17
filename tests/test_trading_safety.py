from __future__ import annotations

from app.core.models import AppConfig, GoldOrderMode, HedgeMode, Position, Side
from app.core.trade_result import LegResult
from app.core.trading_service import close_hedge, open_hedge, spread_allows_add


class _FakeConnector:
    def __init__(
        self,
        platform: str,
        open_success: bool,
        *,
        needs_reconciliation: bool = False,
        open_filled: float | None = None,
    ) -> None:
        self.platform = platform
        self.open_success = open_success
        self.needs_reconciliation = needs_reconciliation
        self.open_filled = open_filled
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
        should_keep_waiting=None,
    ) -> LegResult:
        if should_keep_waiting is not None and not should_keep_waiting():
            return LegResult(
                platform=self.platform,
                success=False,
                message="BA Maker 开仓点差回落，未成交部分已撤单",
            )
        return LegResult(
            platform=self.platform,
            success=self.open_success,
            message="opened" if self.open_success else "failed",
            needs_reconciliation=self.needs_reconciliation,
            filled_quantity=(
                self.open_filled
                if self.open_filled is not None
                else 1.0 if self.open_success else 0.0
            ),
        )

    def close_hedge_leg(
        self, preset_id: str, order_mode: str, mode: str = "contraction", **kwargs
    ) -> LegResult:
        self.close_calls += 1
        return LegResult(platform=self.platform, success=True, message="rolled back")


class _FakeCloseConnector:
    def __init__(
        self,
        platform: str,
        close_success: bool,
        *,
        close_filled: float = 0.0,
        open_success: bool = True,
        close_needs_reconciliation: bool = False,
    ) -> None:
        self.platform = platform
        self.close_success = close_success
        self.close_filled = close_filled
        self.open_success = open_success
        self.close_needs_reconciliation = close_needs_reconciliation
        self.config = AppConfig(xau_trade_lots=1.0, xau_ba_qty_map=500.0, xau_mt5_lot_map=1.0)
        self.close_calls: list[dict] = []
        self.open_calls: list[dict] = []

    def close_hedge_leg(
        self,
        preset_id: str,
        order_mode: str,
        mode: str = "contraction",
        **kwargs,
    ) -> LegResult:
        self.close_calls.append(kwargs)
        on_fill_delta = kwargs.get("on_fill_delta")
        if self.close_success and on_fill_delta is not None and self.close_filled > 0:
            on_fill_delta(self.close_filled)
        return LegResult(
            platform=self.platform,
            success=self.close_success,
            message="closed" if self.close_success else "close failed",
            filled_quantity=self.close_filled if self.close_success else 0.0,
            needs_reconciliation=self.close_needs_reconciliation,
        )

    def open_hedge_leg(
        self,
        preset_id: str,
        mode: str,
        order_mode: str,
        **kwargs,
    ) -> LegResult:
        self.open_calls.append(
            {
                "preset_id": preset_id,
                "mode": mode,
                "order_mode": order_mode,
                **kwargs,
            }
        )
        return LegResult(
            platform=self.platform,
            success=self.open_success,
            message="restored" if self.open_success else "restore failed",
            filled_quantity=kwargs.get("qty_override") or kwargs.get("lots_override") or 0.0,
        )


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


def test_open_hedge_maker_spread_guard_cancels_before_ba_fill():
    ba = _FakeConnector("BA", open_success=True)
    mt5 = _FakeConnector("MT5", open_success=True)

    result = open_hedge(
        ba,
        mt5,
        "xau",
        HedgeMode.CONTRACTION.value,
        GoldOrderMode.MAKER.value,
        spread_guard=lambda: False,
    )

    assert result.success is False
    assert result.partial is False
    assert mt5.close_calls == 0
    assert "点差回落" in result.legs[0].message
    assert "跳过" in result.legs[1].message


def test_open_hedge_skips_rollback_for_unconfirmed_order_without_fill():
    ba = _FakeConnector("BA", open_success=False, needs_reconciliation=True)
    mt5 = _FakeConnector("MT5", open_success=False)

    result = open_hedge(ba, mt5, "xau", HedgeMode.CONTRACTION.value)

    assert result.success is False
    assert result.partial is True
    assert ba.close_calls == 0
    assert result.legs[0].compensated is False
    assert "状态待对账" in result.message
    assert "系统已尝试自动回滚" not in result.message


def test_open_hedge_skips_rollback_when_opposite_status_unknown():
    ba = _FakeConnector("BA", open_success=True)
    mt5 = _FakeConnector("MT5", open_success=False, needs_reconciliation=True)

    result = open_hedge(
        ba, mt5, "xau", HedgeMode.CONTRACTION.value, GoldOrderMode.MARKET.value
    )

    assert result.success is False
    assert result.partial is True
    assert ba.close_calls == 0
    assert result.legs[0].compensated is False
    assert "状态待对账" in result.message


def test_close_hedge_restores_ba_when_mt5_fails_after_maker_fill():
    ba = _FakeCloseConnector("BA", close_success=True, close_filled=250.0)
    mt5 = _FakeCloseConnector("MT5", close_success=False)

    result = close_hedge(
        ba, mt5, "xau", HedgeMode.CONTRACTION.value, GoldOrderMode.MAKER.value
    )

    assert result.success is False
    assert result.partial is True
    assert ba.open_calls
    assert ba.open_calls[0]["order_mode"] == GoldOrderMode.MARKET.value
    assert ba.open_calls[0]["qty_override"] == 250.0
    assert result.legs[0].compensated is True
    assert "Exness 补平失败" in result.legs[0].message
    assert "自动恢复对冲" in result.message


def test_close_hedge_skips_ba_restore_when_mt5_status_unknown_after_maker_fill():
    ba = _FakeCloseConnector("BA", close_success=True, close_filled=250.0)
    mt5 = _FakeCloseConnector(
        "MT5", close_success=False, close_needs_reconciliation=True
    )

    result = close_hedge(
        ba, mt5, "xau", HedgeMode.CONTRACTION.value, GoldOrderMode.MAKER.value
    )

    assert result.success is False
    assert result.partial is True
    assert ba.open_calls == []
    assert result.legs[0].compensated is False
    assert "状态待对账" in result.message
    assert "自动恢复对冲" not in result.message


def test_close_hedge_restores_mt5_when_binance_fails_market_close():
    ba = _FakeCloseConnector("BA", close_success=False)
    mt5 = _FakeCloseConnector("MT5", close_success=True, close_filled=0.5)

    result = close_hedge(
        ba, mt5, "xau", HedgeMode.EXPANSION.value, GoldOrderMode.MARKET.value
    )

    assert result.success is False
    assert result.partial is True
    assert mt5.open_calls
    assert mt5.open_calls[0]["order_mode"] == GoldOrderMode.MARKET.value
    assert mt5.open_calls[0]["lots_override"] == 0.5
    assert result.legs[1].compensated is True
    assert "自动恢复对冲" in result.message


def test_close_hedge_skips_mt5_restore_when_binance_status_unknown():
    ba = _FakeCloseConnector(
        "BA", close_success=False, close_needs_reconciliation=True
    )
    mt5 = _FakeCloseConnector("MT5", close_success=True, close_filled=0.5)

    result = close_hedge(
        ba, mt5, "xau", HedgeMode.EXPANSION.value, GoldOrderMode.MARKET.value
    )

    assert result.success is False
    assert result.partial is True
    assert mt5.open_calls == []
    assert result.legs[1].compensated is False
    assert "状态待对账" in result.message
    assert "自动恢复对冲" not in result.message


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
    test_open_hedge_maker_spread_guard_cancels_before_ba_fill()
    test_open_hedge_skips_rollback_for_unconfirmed_order_without_fill()
    test_open_hedge_skips_rollback_when_opposite_status_unknown()
    test_close_hedge_restores_ba_when_mt5_fails_after_maker_fill()
    test_close_hedge_skips_ba_restore_when_mt5_status_unknown_after_maker_fill()
    test_close_hedge_restores_mt5_when_binance_fails_market_close()
    test_close_hedge_skips_mt5_restore_when_binance_status_unknown()
    test_spread_allows_add_blocks_worse_contraction_spread()
    test_spread_allows_add_blocks_worse_expansion_spread()
    print("ALL TRADING SAFETY TESTS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
