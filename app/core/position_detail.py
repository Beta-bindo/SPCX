"""按平台聚合的持仓明细，供实时盈亏面板展示。"""


from __future__ import annotations
from dataclasses import dataclass

from app.core.liquidation import (
    resolve_position_liq_price_distance,
    resolve_position_liquidation_price,
)
from app.core.models import AppConfig, Position, Quote, Side
from app.core.symbols import WATCHED_PRESETS, find_preset


def _display_liquidation_price(platform: str, pos: Position, leverage: int) -> float:
    """盈利情况的强平价显示口径：BA 只展示交易所返回值，MT5 可按账户模型估算。"""
    if platform == "BA":
        return pos.liquidation_price if pos.liquidation_price > 0 else 0.0
    return resolve_position_liquidation_price(pos, leverage)


@dataclass
class PlatformDetail:
    """单个平台的持仓汇总明细。"""

    platform: str
    pnl: float = 0.0
    estimated_fee: float = 0.0
    quantity: float = 0.0
    side: Side = Side.NONE
    liquidation_price: float = 0.0
    liq_buffer: float = 0.0       # 距强平价的价格距离（盈利情况「爆」，取最危险持仓）
    leverage: int = 0
    has_position: bool = False

    @property
    def net_pnl(self) -> float:
        """平台净盈亏 = 浮动盈亏 - 预估平仓手续费。"""
        return round(self.pnl - self.estimated_fee, 2)


def _aggregate_platform(
    platform: str,
    positions: list[Position],
    ba_quotes: dict[str, Quote],
    mt5_quotes: dict[str, Quote],
    config: AppConfig,
) -> PlatformDetail:
    """跨所有受监控品种聚合某平台的持仓（盈亏求和、强平距离取最小、爆仓价取均值）。"""
    detail = PlatformDetail(platform=platform)
    matched: list[tuple[Position, str]] = []
    for preset_id in WATCHED_PRESETS:
        preset = find_preset(preset_id)
        sym = preset.symbol_ba if platform == "BA" else preset.symbol_mt5
        pos = next((p for p in positions if p.platform == platform and p.symbol == sym), None)
        if pos:
            matched.append((pos, preset_id))

    if not matched:
        if platform == "BA":
            detail.leverage = config.ba_leverage
        else:
            detail.leverage = config.mt5_leverage
        return detail

    detail.has_position = True
    detail.pnl = round(sum(p.unrealized_pnl for p, _ in matched), 2)
    detail.estimated_fee = round(sum(p.estimated_fee for p, _ in matched), 4)
    detail.quantity = round(sum(p.quantity for p, _ in matched), 4)
    sides = {p.side for p, _ in matched}
    detail.side = sides.pop() if len(sides) == 1 else Side.NONE

    liq_prices: list[float] = []
    buffers: list[float] = []
    for pos, preset_id in matched:
        lev = pos.leverage if pos.leverage > 0 else (
            config.ba_leverage if platform == "BA" else config.mt5_leverage
        )
        liq_price = _display_liquidation_price(platform, pos, lev)
        if liq_price > 0:
            liq_prices.append(liq_price)
        quote = (
            ba_quotes.get(pos.symbol)
            if platform == "BA"
            else mt5_quotes.get(pos.symbol)
        )
        if platform == "BA":
            buffers.append(_resolve_price_distance(platform, pos, quote, config.ba_leverage))
        else:
            mt5_lev = pos.leverage if pos.leverage > 0 else config.mt5_leverage
            buffers.append(_resolve_price_distance(platform, pos, quote, mt5_lev))

    if liq_prices:
        detail.liquidation_price = round(sum(liq_prices) / len(liq_prices), 3)
    finite_buffers = [b for b in buffers if b != float("inf")]
    if finite_buffers:
        detail.liq_buffer = round(min(finite_buffers), 2)
    if matched:
        first_lev = matched[0][0].leverage
        detail.leverage = first_lev if first_lev > 0 else (
            config.ba_leverage if platform == "BA" else config.mt5_leverage
        )
    return detail


def _resolve_price_distance(
    platform: str, pos: Position, quote: Quote | None, leverage: int
) -> float:
    """求单个持仓距强平价的价格距离。"""
    liq_price = _display_liquidation_price(platform, pos, leverage)
    if liq_price <= 0:
        return float("inf")
    return resolve_position_liq_price_distance(pos, quote, liq_price)


def _detail_for_position(
    platform: str,
    pos: Position | None,
    preset_id: str,
    ba_quotes: dict[str, Quote],
    mt5_quotes: dict[str, Quote],
    config: AppConfig,
) -> PlatformDetail:
    """构造单个品种、单平台持仓的明细（无持仓则只带杠杆）。"""
    detail = PlatformDetail(platform=platform)
    lev = config.ba_leverage if platform == "BA" else config.mt5_leverage
    detail.leverage = lev
    if pos is None:
        return detail

    detail.has_position = True
    detail.pnl = round(pos.unrealized_pnl, 2)
    detail.estimated_fee = round(pos.estimated_fee, 4)
    detail.quantity = round(pos.quantity, 4)
    detail.side = pos.side
    pos_lev = pos.leverage if pos.leverage > 0 else lev
    detail.leverage = pos_lev
    liq_price = _display_liquidation_price(platform, pos, pos_lev)
    detail.liquidation_price = round(liq_price, 3) if liq_price > 0 else 0.0
    quote = ba_quotes.get(pos.symbol) if platform == "BA" else mt5_quotes.get(pos.symbol)
    buf = _resolve_price_distance(platform, pos, quote, pos_lev)
    if buf != float("inf"):
        detail.liq_buffer = round(buf, 2)
    return detail


def build_platform_details_for_preset(
    preset_id: str,
    positions: list[Position],
    ba_quotes: dict[str, Quote],
    mt5_quotes: dict[str, Quote],
    config: AppConfig,
) -> tuple[PlatformDetail, PlatformDetail]:
    """构造指定品种两端 (BA, MT5) 的持仓明细。"""
    preset = find_preset(preset_id)
    ba_pos = next(
        (p for p in positions if p.platform == "BA" and p.symbol == preset.symbol_ba),
        None,
    )
    mt5_pos = next(
        (p for p in positions if p.platform == "MT5" and p.symbol == preset.symbol_mt5),
        None,
    )
    ba = _detail_for_position("BA", ba_pos, preset_id, ba_quotes, mt5_quotes, config)
    mt5 = _detail_for_position("MT5", mt5_pos, preset_id, ba_quotes, mt5_quotes, config)
    return ba, mt5


def build_platform_details(
    positions: list[Position],
    ba_quotes: dict[str, Quote],
    mt5_quotes: dict[str, Quote],
    config: AppConfig,
) -> tuple[PlatformDetail, PlatformDetail]:
    """构造跨全部品种聚合的两端 (BA, MT5) 持仓明细。"""
    ba = _aggregate_platform("BA", positions, ba_quotes, mt5_quotes, config)
    mt5 = _aggregate_platform("MT5", positions, ba_quotes, mt5_quotes, config)
    return ba, mt5
