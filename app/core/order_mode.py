"""Map UI order mode to connector limit/maker flags."""

from __future__ import annotations

from app.core.models import GoldOrderMode

LANE_MAKER = "maker"
LANE_MARKET = "market"


def resolve_execution_flags(preset_id: str, order_mode: str) -> tuple[bool, bool]:
    """Return (use_limit, maker_only) for BA/Exness hedge legs."""
    _ = preset_id
    if order_mode == GoldOrderMode.MARKET.value:
        return False, False
    if order_mode == GoldOrderMode.MAKER.value:
        return True, True
    return True, False


def auto_trade_order_mode(preset_id: str, lane: str) -> str:
    """Auto-trade lane -> connector order mode."""
    if lane == LANE_MARKET or preset_id == "xag":
        return GoldOrderMode.MARKET.value
    return GoldOrderMode.MAKER.value


def auto_trade_lane(preset_id: str, order_mode: str) -> str:
    if order_mode == GoldOrderMode.MARKET.value:
        return LANE_MARKET
    if preset_id == "xag":
        return LANE_MARKET
    return LANE_MAKER


def order_mode_log_label(preset_id: str, order_mode: str) -> str:
    if order_mode == GoldOrderMode.MARKET.value:
        return "市价"
    if order_mode == GoldOrderMode.MAKER.value:
        return "Maker"
    return "限价"
