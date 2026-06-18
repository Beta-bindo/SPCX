"""按平台聚合的持仓明细，供实时盈亏面板展示。"""


from __future__ import annotations
from dataclasses import dataclass

from app.core.liquidation import (
    resolve_position_liq_abs_price_distance,
    resolve_position_liq_price_distance,
    resolve_position_liquidation_price,
)
from app.core.models import AppConfig, Position, Quote, Side
from app.core.symbols import active_preset_ids, find_preset


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
    point_diff: float = 0.0      # 当前指数 - 持仓均价（按平仓方向取价）
    estimated_fee: float = 0.0
    quantity: float = 0.0
    side: Side = Side.NONE
    liquidation_price: float = 0.0
    liq_buffer: float | None = None  # 距强平价的价格距离；None 表示未知，0 表示已到/越过强平价
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
    for preset_id in active_preset_ids():
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

    point_diffs: list[tuple[float, float]] = []
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
        point_diff = _resolve_point_diff(pos, quote)
        if point_diff is not None:
            point_diffs.append((point_diff, pos.quantity))
        if platform == "BA":
            buffers.append(
                _resolve_price_distance(platform, pos, quote, config.ba_leverage, preset_id)
            )
        else:
            mt5_lev = pos.leverage if pos.leverage > 0 else config.mt5_leverage
            buffers.append(_resolve_price_distance(platform, pos, quote, mt5_lev, preset_id))

    if point_diffs:
        total_qty = sum(qty for _diff, qty in point_diffs)
        if total_qty > 0:
            detail.point_diff = round(
                sum(diff * qty for diff, qty in point_diffs) / total_qty,
                3,
            )
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


def _resolve_point_diff(pos: Position, quote: Quote | None) -> float | None:
    """当前可平仓价 - 持仓均价；无实时价时用持仓当前价兜底。"""
    if pos.entry_price <= 0:
        return None
    mark = 0.0
    if quote is not None:
        if pos.side == Side.BUY:
            mark = quote.bid if quote.bid > 0 else quote.mid
        elif pos.side == Side.SELL:
            mark = quote.ask if quote.ask > 0 else quote.mid
        else:
            mark = quote.mid
    if mark <= 0:
        mark = pos.current_price if pos.current_price > 0 else pos.mark_price
    if mark <= 0:
        return None
    return mark - pos.entry_price


def _resolve_price_distance(
    platform: str, pos: Position, quote: Quote | None, leverage: int, preset_id: str
) -> float:
    """求单个持仓距强平价的价格距离。"""
    liq_price = _display_liquidation_price(platform, pos, leverage)
    if liq_price > 0:
        dist = (
            resolve_position_liq_abs_price_distance(pos, quote, liq_price)
            if platform == "MT5"
            else resolve_position_liq_price_distance(pos, quote, liq_price)
        )
        if platform == "MT5" and dist <= 1e-9:
            fallback = _account_buffer_price_distance(platform, pos, preset_id)
            if fallback != float("inf") and fallback > 0:
                return fallback
        if dist != float("inf"):
            return dist
    return _account_buffer_price_distance(platform, pos, preset_id)


def _account_buffer_price_distance(platform: str, pos: Position, preset_id: str) -> float:
    """无可靠当前价时，用账户/交易所缓冲折算还能抗多少价格点。"""
    if pos.exchange_liq_buffer is None or pos.quantity <= 0:
        return float("inf")
    preset = find_preset(preset_id)
    unit = preset.ba_qty_unit if platform == "BA" else preset.mt5_oz_per_lot
    exposure = abs(pos.quantity) * unit
    if exposure <= 0:
        return float("inf")
    return max(0.0, pos.exchange_liq_buffer) / exposure


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
    point_diff = _resolve_point_diff(
        pos,
        ba_quotes.get(pos.symbol) if platform == "BA" else mt5_quotes.get(pos.symbol),
    )
    if point_diff is not None:
        detail.point_diff = round(point_diff, 3)
    detail.estimated_fee = round(pos.estimated_fee, 4)
    detail.quantity = round(pos.quantity, 4)
    detail.side = pos.side
    pos_lev = pos.leverage if pos.leverage > 0 else lev
    detail.leverage = pos_lev
    liq_price = _display_liquidation_price(platform, pos, pos_lev)
    detail.liquidation_price = round(liq_price, 3) if liq_price > 0 else 0.0
    quote = ba_quotes.get(pos.symbol) if platform == "BA" else mt5_quotes.get(pos.symbol)
    buf = _resolve_price_distance(platform, pos, quote, pos_lev, preset_id)
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
