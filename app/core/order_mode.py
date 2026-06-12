"""下单模式与执行标志、自动交易 lane 之间的映射工具。

UI 三种下单模式（市价 / Maker / 限价）需翻译为连接器使用的两个布尔标志，
自动交易则按 lane（maker / market）选择对应模式。
"""

from __future__ import annotations

from app.core.models import GoldOrderMode

LANE_MAKER = "maker"
LANE_MARKET = "market"


def resolve_execution_flags(preset_id: str, order_mode: str) -> tuple[bool, bool]:
    """下单模式 → (是否限价单, 是否只做 Maker)。

    市价 → (False, False)；Maker → (True, True)；限价 → (True, False)。
    """
    _ = preset_id
    if order_mode == GoldOrderMode.MARKET.value:
        return False, False
    if order_mode == GoldOrderMode.MAKER.value:
        return True, True
    return True, False


def auto_trade_order_mode(preset_id: str, lane: str) -> str:
    """自动交易 lane → 下单模式（市价 lane 或白银一律市价，否则 Maker）。"""
    if lane == LANE_MARKET or preset_id == "xag":
        return GoldOrderMode.MARKET.value
    return GoldOrderMode.MAKER.value


def auto_trade_lane(preset_id: str, order_mode: str) -> str:
    """下单模式 → 自动交易 lane（auto_trade_order_mode 的逆映射）。"""
    if order_mode == GoldOrderMode.MARKET.value:
        return LANE_MARKET
    if preset_id == "xag":
        return LANE_MARKET
    return LANE_MAKER


def order_mode_log_label(preset_id: str, order_mode: str) -> str:
    """下单模式 → 日志/界面中文标签。"""
    if order_mode == GoldOrderMode.MARKET.value:
        return "市价"
    if order_mode == GoldOrderMode.MAKER.value:
        return "Maker"
    return "限价"
