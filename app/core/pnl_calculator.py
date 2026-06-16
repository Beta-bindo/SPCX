"""跨平台盈亏（PnL）与点差计算。

统一根据两端实时报价计算：
- 点差快照（BA 与 Exness 的买价差 / 可执行差价）；
- 每个持仓的浮动盈亏与预估手续费；
- 全局汇总（毛利、手续费、净利）。
"""

from __future__ import annotations

from dataclasses import dataclass

from app.core.models import AppConfig, Position, Quote, Side, SpreadSnapshot
from app.core.symbols import find_preset, preset_for_ba_symbol


@dataclass
class PnlSummary:
    """一次刷新后的盈亏汇总（金额单位均为计价货币 USDT/USD）。"""

    ba_pnl: float = 0.0          # BA 端浮动盈亏合计
    mt5_pnl: float = 0.0         # Exness/MT5 端浮动盈亏合计
    gross_pnl: float = 0.0       # 毛利 = ba_pnl + mt5_pnl
    ba_fee: float = 0.0          # BA 端预估手续费
    mt5_fee: float = 0.0         # MT5 端预估手续费（佣金 + 点差成本）
    total_fees: float = 0.0      # 手续费合计
    net_pnl: float = 0.0         # 净利 = 毛利 − 手续费
    exec_spread: float = 0.0     # 可执行点差（主品种）
    mid_spread: float = 0.0      # 买价点差（主品种，字段名保留兼容）


def build_spread_snapshot(ba: Quote, mt5: Quote, preset_id: str = "xau") -> SpreadSnapshot | None:
    """由两端最新报价构造点差快照；任一端缺买卖价则返回 None。

    - mid_spread（点差指数）：BA 买一 − Exness 买一，用于展示与告警判断；
    - exec_spread（可执行点差）：BA 买一 − Exness 卖一，更贴近实际成交差价。
    """
    if ba.bid <= 0 or ba.ask <= 0 or mt5.bid <= 0 or mt5.ask <= 0:
        return None
    ba_mid = (ba.bid + ba.ask) / 2
    mt5_mid = (mt5.bid + mt5.ask) / 2
    # 任一端为模拟报价则整笔标记为模拟，下单前置校验会据此拦截实盘混合下单
    is_simulated = ba.is_simulated or mt5.is_simulated
    return SpreadSnapshot(
        preset_id=preset_id,
        ba_bid=ba.bid,
        ba_ask=ba.ask,
        mt5_bid=mt5.bid,
        mt5_ask=mt5.ask,
        ba_mid=ba_mid,
        mt5_mid=mt5_mid,
        mid_spread=ba.bid - mt5.bid,
        exec_spread=ba.bid - mt5.ask,
        ba_platform_spread=ba.ask - ba.bid,
        mt5_platform_spread=mt5.ask - mt5.bid,
        is_simulated=is_simulated,
        timestamp=ba.timestamp or mt5.timestamp,
    )


def _mark_price(side: Side, quote: Quote) -> float:
    """取平仓方向对应的盯市价：多头按买一离场、空头按卖一离场。"""
    if side == Side.BUY:
        return quote.bid
    if side == Side.SELL:
        return quote.ask
    return quote.mid


def _position_pnl(position: Position, mark: float, multiplier: float) -> float:
    """按盯市价估算单个持仓的浮动盈亏；无有效盯市价时退回交易所返回值。

    multiplier 把"合约/手数"换算成实际盎司数（如每手黄金 100 盎司）。
    """
    if mark <= 0:
        return position.unrealized_pnl
    if position.side == Side.BUY:
        return (mark - position.entry_price) * position.quantity * multiplier
    if position.side == Side.SELL:
        return (position.entry_price - mark) * position.quantity * multiplier
    return 0.0


def _use_exchange_pnl(platform: str, config: AppConfig) -> bool:
    """该平台是否处于实盘模式（实盘下优先采用交易所回报的盈亏）。"""
    if platform == "BA":
        return config.use_live_ba
    if platform == "MT5":
        return config.use_live_mt5
    return False


def _estimate_ba_fee(notional: float, fee_rate: float, legs: int = 1) -> float:
    """BA 端手续费 = 名义本金 × 费率 × 腿数。"""
    return notional * fee_rate * legs


def _estimate_mt5_fee(
    lots: float,
    commission_per_lot: float,
    spread_points: float,
    point_value: float,
    legs: int = 2,
) -> float:
    """MT5 端成本 = 每手佣金 × 手数 × 腿数 + 点差成本（手数 × 点差点数 × 每点价值）。"""
    commission = lots * commission_per_lot * legs
    spread_cost = lots * spread_points * point_value
    return commission + spread_cost


def _fee_legs_for_display() -> int:
    """实时面板只展示预估平仓成本（单腿），而非开+平的往返成本。"""
    return 1


def estimate_trade_fees(
    preset_id: str,
    config: AppConfig,
    *,
    ba_price: float,
    ba_quantity: float,
    mt5_quantity: float,
    legs: int = 1,
) -> tuple[float, float]:
    """估算单笔成交各端手续费（默认单腿，用于开仓或平仓记账）。"""
    preset = find_preset(preset_id)
    notional = ba_price * ba_quantity * preset.ba_qty_unit
    ba_fee = round(_estimate_ba_fee(notional, config.ba_fee_rate, legs), 4)
    mt5_fee = round(
        _estimate_mt5_fee(
            mt5_quantity,
            config.mt5_commission_per_lot,
            config.mt5_spread_points,
            config.mt5_point_value,
            legs=legs,
        ),
        4,
    )
    return ba_fee, mt5_fee


def _preset_for_position(pos: Position) -> str:
    """根据持仓的交易对反查所属品种预设（xau/xag）。"""
    if pos.platform == "BA":
        return preset_for_ba_symbol(pos.symbol)
    for pid in ("xau", "xag"):
        preset = find_preset(pid)
        if preset.symbol_mt5 == pos.symbol:
            return pid
    return "xau"


def _quote_ready(quote: Quote) -> bool:
    """报价是否含有效买卖价。"""
    return quote.bid > 0 and quote.ask > 0


def calculate_pnl(
    positions: list[Position],
    ba_quotes: dict[str, Quote],
    mt5_quotes: dict[str, Quote],
    config: AppConfig,
    primary_spread: SpreadSnapshot | None,
) -> tuple[list[Position], PnlSummary]:
    """根据最新报价刷新所有持仓的浮盈与手续费，并返回汇总。

    返回 (更新后的持仓列表, 盈亏汇总)。函数会就地修改传入的 Position 对象
    （current_price / unrealized_pnl / estimated_fee 字段）。
    """
    updated: list[Position] = []
    summary = PnlSummary()
    if primary_spread:
        summary.exec_spread = primary_spread.exec_spread
        summary.mid_spread = primary_spread.mid_spread

    for pos in positions:
        preset_id = _preset_for_position(pos)
        preset = find_preset(preset_id)
        if pos.platform == "BA":
            quote = ba_quotes.get(pos.symbol, Quote(symbol=pos.symbol))
            mark = _mark_price(pos.side, quote)
            multiplier = preset.ba_qty_unit
            if _use_exchange_pnl("BA", config) and not _quote_ready(quote):
                pnl = pos.unrealized_pnl
            else:
                pnl = _position_pnl(pos, mark, multiplier)
            price_for_fee = mark if mark > 0 else pos.entry_price
            notional = price_for_fee * pos.quantity * multiplier
            pos.current_price = mark if mark > 0 else pos.entry_price
            pos.unrealized_pnl = round(pnl, 2)
            pos.estimated_fee = round(
                _estimate_ba_fee(notional, config.ba_fee_rate, _fee_legs_for_display()),
                4,
            )
            summary.ba_pnl += pos.unrealized_pnl
            summary.ba_fee += pos.estimated_fee
        elif pos.platform == "MT5":
            quote = mt5_quotes.get(pos.symbol, Quote(symbol=pos.symbol))
            mark = _mark_price(pos.side, quote)
            multiplier = preset.mt5_oz_per_lot
            if _use_exchange_pnl("MT5", config) and not _quote_ready(quote):
                pnl = pos.unrealized_pnl
            else:
                pnl = _position_pnl(pos, mark, multiplier)
            pos.current_price = mark if mark > 0 else pos.entry_price
            pos.unrealized_pnl = round(pnl, 2)
            pos.estimated_fee = round(
                _estimate_mt5_fee(
                    pos.quantity,
                    config.mt5_commission_per_lot,
                    config.mt5_spread_points,
                    config.mt5_point_value,
                    legs=_fee_legs_for_display(),
                ),
                4,
            )
            summary.mt5_pnl += pos.unrealized_pnl
            summary.mt5_fee += pos.estimated_fee
        updated.append(pos)

    # 汇总并统一四舍五入（金额 2 位、费用 4 位）
    summary.gross_pnl = round(summary.ba_pnl + summary.mt5_pnl, 2)
    summary.total_fees = round(summary.ba_fee + summary.mt5_fee, 4)
    summary.net_pnl = round(summary.gross_pnl - summary.total_fees, 2)
    summary.ba_pnl = round(summary.ba_pnl, 2)
    summary.mt5_pnl = round(summary.mt5_pnl, 2)
    summary.ba_fee = round(summary.ba_fee, 4)
    summary.mt5_fee = round(summary.mt5_fee, 4)
    return updated, summary
