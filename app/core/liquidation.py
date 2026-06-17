"""爆仓价、距爆仓价格距离与资金缓冲的计算工具，尽量对齐交易所真实口径。

提供多种估算来源：交易所直接返回的缓冲 > 由爆仓价与盯市价反推 > 退化的保证金估算。
MT5 侧以"权益跌到强平线（stop-out）"为爆仓条件，用二分法反推爆仓价。
"""

from __future__ import annotations

from collections.abc import Callable

from app.core.models import Position, Quote, Side
from app.core.symbols import find_preset


def ba_isolated_liq_buffer(
    isolated_wallet: float, unrealized_pnl: float, maint_margin: float
) -> float:
    """币安逐仓：当 钱包余额 + 浮盈 ≤ 维持保证金 时爆仓，返回距此的缓冲。"""
    return max(0.0, isolated_wallet + unrealized_pnl - maint_margin)


def ba_cross_account_liq_buffer(margin_balance: float, maint_margin: float) -> float:
    """币安全仓：保证金余额 − 维持保证金。"""
    return max(0.0, margin_balance - maint_margin)


def liq_buffer_from_prices(
    side: Side,
    mark: float,
    liquidation_price: float,
    quantity: float,
    *,
    qty_unit: float = 1.0,
) -> float:
    """由"交易所爆仓价 + 当前盯市价"推算距爆仓的资金缓冲（计价货币）。

    qty_unit 把合约/手数换算为实际盎司数；多头随价跌接近爆仓、空头随价涨接近爆仓。
    """
    if liquidation_price <= 0 or mark <= 0 or quantity <= 0:
        return float("inf")
    qty = abs(quantity) * qty_unit
    if side == Side.BUY:
        return max(0.0, (mark - liquidation_price) * qty)
    if side == Side.SELL:
        return max(0.0, (liquidation_price - mark) * qty)
    return float("inf")


def liq_price_distance_from_prices(
    side: Side,
    mark: float,
    liquidation_price: float,
) -> float:
    """由当前价与强平价计算还差多少价格点触发强平。"""
    if liquidation_price <= 0 or mark <= 0:
        return float("inf")
    if side == Side.BUY:
        return max(0.0, mark - liquidation_price)
    if side == Side.SELL:
        return max(0.0, liquidation_price - mark)
    return float("inf")


def mt5_stopout_equity(margin: float, stop_out_pct: float) -> float:
    """MT5 强平线对应的权益值 = 占用保证金 × 强平比例%。"""
    return margin * (stop_out_pct / 100.0)


def mt5_account_liq_buffer(equity: float, margin: float, stop_out_pct: float) -> float:
    """MT5 账户距强平（stop-out）的权益缓冲。"""
    return max(0.0, equity - mt5_stopout_equity(margin, stop_out_pct))


def calc_liquidation_price_from_profit(
    side: Side,
    entry_price: float,
    equity_without_position: float,
    margin: float,
    stop_out_pct: float,
    profit_calc: Callable[[float], float],
    *,
    max_iterations: int = 64,
) -> float:
    """二分搜索使账户权益恰好触及 MT5 强平线的平仓价（即爆仓价）。

    equity_without_position 为剔除本持仓后的权益，profit_calc(平仓价) 给出本持仓盈亏；
    多头在 (0, 入场价] 区间下行搜索，空头在 [入场价, 3×入场价) 区间上行搜索。
    """
    if entry_price <= 0 or margin <= 0:
        return 0.0
    target_equity = mt5_stopout_equity(margin, stop_out_pct)

    def equity_at(close_price: float) -> float:
        return equity_without_position + profit_calc(close_price)

    if side == Side.BUY:
        lo = max(entry_price * 0.01, 1e-6)
        hi = entry_price
        if equity_at(hi) <= target_equity:
            return round(hi, 3)
        if equity_at(lo) > target_equity:
            return round(lo, 3)
        for _ in range(max_iterations):
            mid = (lo + hi) / 2.0
            if equity_at(mid) > target_equity:
                hi = mid
            else:
                lo = mid
            if abs(hi - lo) < 1e-4:
                break
        return round(hi, 3)

    if side == Side.SELL:
        lo = entry_price
        hi = entry_price * 3.0
        if equity_at(lo) <= target_equity:
            return round(lo, 3)
        if equity_at(hi) > target_equity:
            return round(hi, 3)
        for _ in range(max_iterations):
            mid = (lo + hi) / 2.0
            if equity_at(mid) > target_equity:
                lo = mid
            else:
                hi = mid
            if abs(hi - lo) < 1e-4:
                break
        return round(lo, 3)
    return 0.0


def estimate_liquidation_price(entry: float, side: Side, leverage: int, mmr: float = 0.004) -> float:
    """按杠杆与维持保证金率粗估爆仓价（模拟盘 / 交易所未给价时的兜底）。"""
    if entry <= 0 or leverage <= 0 or side == Side.NONE:
        return 0.0
    if side == Side.BUY:
        return entry * (1 - 1 / leverage + mmr)
    return entry * (1 + 1 / leverage - mmr)


def resolve_mark_price(pos: Position, quote: Quote | None) -> float:
    """取盯市价：优先用持仓自带 mark_price，否则按平仓方向取对侧报价。"""
    if pos.mark_price > 0:
        return pos.mark_price
    if quote is None:
        return 0.0
    if pos.side == Side.SELL:
        return quote.ask if quote.ask > 0 else quote.mid
    if pos.side == Side.BUY:
        return quote.bid if quote.bid > 0 else quote.mid
    return quote.mid


def _live_mark_price(pos: Position, quote: Quote | None) -> float:
    """取「实时」盯市价：优先用实时报价的对侧盘口，缺报价再退回持仓自带盯市价。

    与 resolve_mark_price 相反——这里优先实时报价，使「爆」资金缓冲能随行情逐 tick 跳动，
    而不是停留在上次持仓轮询(约 4 秒)时的旧盯市价。
    """
    if quote is not None:
        if pos.side == Side.SELL:
            px = quote.ask if quote.ask > 0 else quote.mid
        elif pos.side == Side.BUY:
            px = quote.bid if quote.bid > 0 else quote.mid
        else:
            px = quote.mid
        if px > 0:
            return px
    if pos.current_price > 0:
        return pos.current_price
    return pos.mark_price


def resolve_position_liq_buffer(
    pos: Position,
    quote: Quote | None,
    preset_id: str,
    leverage: int,
) -> float:
    """求持仓距爆仓的缓冲，按可靠性依次尝试：

    1) 实时报价 + 交易所爆仓价反推（随行情逐 tick 跳动，「爆」更跟手）；
    2) 交易所直接返回的缓冲（轮询值，约 4 秒更新，作为无实时价时的兜底）；
    3) 退化方案：用"名义本金/杠杆"估算保证金再叠加浮盈。
    """
    preset = find_preset(preset_id)
    # 1) 优先用实时报价 + 爆仓价逐 tick 重算，让「爆」跟随行情平滑变化
    mark = _live_mark_price(pos, quote)
    if pos.liquidation_price > 0 and mark > 0:
        unit = preset.ba_qty_unit if pos.platform == "BA" else preset.mt5_oz_per_lot
        buf = liq_buffer_from_prices(
            pos.side,
            mark,
            pos.liquidation_price,
            pos.quantity,
            qty_unit=unit,
        )
        if buf != float("inf"):
            return buf

    # 2) 无实时价/无爆仓价时，退回交易所返回的轮询缓冲
    if pos.exchange_liq_buffer is not None:
        return max(0.0, pos.exchange_liq_buffer)

    if not quote or quote.mid <= 0:
        return float("inf")

    notional = pos.entry_price * pos.quantity * (
        preset.ba_qty_unit if pos.platform == "BA" else preset.mt5_oz_per_lot
    )
    margin = notional / max(leverage, 1)
    if pos.platform == "BA":
        mark_px = quote.ask if pos.side == Side.SELL else quote.bid
        unit = preset.ba_qty_unit
    else:
        mark_px = quote.bid if pos.side == Side.BUY else quote.ask
        unit = preset.mt5_oz_per_lot
    if mark_px <= 0:
        return float("inf")
    if pos.side == Side.SELL:
        unrealized = (pos.entry_price - mark_px) * pos.quantity * unit
    elif pos.side == Side.BUY:
        unrealized = (mark_px - pos.entry_price) * pos.quantity * unit
    else:
        unrealized = 0.0
    return max(0.0, margin + unrealized)


def resolve_position_liq_price_distance(
    pos: Position,
    quote: Quote | None,
    liquidation_price: float | None = None,
) -> float:
    """求持仓距强平价的价格距离，供盈利情况里的「爆」显示。"""
    liq_price = pos.liquidation_price if liquidation_price is None else liquidation_price
    mark = _live_mark_price(pos, quote)
    return liq_price_distance_from_prices(pos.side, mark, liq_price)


def resolve_position_liq_abs_price_distance(
    pos: Position,
    quote: Quote | None,
    liquidation_price: float | None = None,
) -> float:
    """求当前价到强平价的绝对价格距离。

    EX/MT5 的强平价是按账户权益模型本地反推的，并非平台直接返回字段；
    当模型价落在持仓方向的另一侧时，方向公式会把距离压成 0。盈利情况里的
    「爆」是给用户看的“当前价离强平价还有多少点”，这里用绝对距离展示。
    """
    liq_price = pos.liquidation_price if liquidation_price is None else liquidation_price
    mark = _live_mark_price(pos, quote)
    if liq_price <= 0 or mark <= 0:
        return float("inf")
    return abs(mark - liq_price)


def resolve_position_liquidation_price(
    pos: Position,
    leverage: int,
) -> float:
    """取持仓爆仓价：优先用交易所价，否则用持仓杠杆估算。"""
    if pos.liquidation_price > 0:
        return pos.liquidation_price
    lev = pos.leverage if pos.leverage > 0 else leverage
    return estimate_liquidation_price(pos.entry_price, pos.side, lev)
