"""对冲交易的纯业务逻辑层。

封装"开仓 / 加仓 / 平仓"两腿（BA + Exness/MT5）的下单编排，以及对冲方向
（收缩 / 扩张）、加仓点差校验等判定。本层不直接触碰 UI，只通过两个连接器
对象操作交易所，便于单元测试。

两腿执行策略：
- 市价（Market）：两腿并发下单，最大化成交速度；
- Maker / 限价：先挂 BA，按 BA 实际新增成交量分批用 Exness 市价补对冲；
  BA 超时撤单后会再检查取消前最后成交量，避免漏补。
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING, Callable

from app.core.models import GoldOrderMode, HedgeMode, Position, Side
from app.core.order_mode import order_mode_log_label
from app.core.symbols import find_preset, preset_display_name
from app.core.trade_result import HedgeTradeResult, LegResult

if TYPE_CHECKING:
    from app.connectors.binance_connector import BinanceConnector
    from app.connectors.mt5_connector import MT5Connector

# 加仓点差容差：当前点差与持仓差价相差在此范围内仍允许加仓，避免临界抖动误判
ADD_SPREAD_MARGIN = 0.02


def _mode_label(mode: str) -> str:
    """对冲模式 → 中文标签（收缩 / 扩张）。"""
    return "收缩" if mode == HedgeMode.CONTRACTION.value else "扩张"


def _position_for(
    positions: list[Position], platform: str, symbol: str
) -> Position | None:
    """在持仓列表中查找指定平台 + 交易对、且数量大于 0 的持仓。"""
    for pos in positions:
        if pos.platform == platform and pos.symbol == symbol and pos.quantity > 0:
            return pos
    return None


def detect_hedge_mode(preset_id: str, positions: list[Position]) -> str | None:
    """Return contraction/expansion when hedged, else None (no position)."""
    preset = find_preset(preset_id)
    ba_pos = _position_for(positions, "BA", preset.symbol_ba)
    mt5_pos = _position_for(positions, "MT5", preset.symbol_mt5)
    if not ba_pos and not mt5_pos:
        return None
    if ba_pos and mt5_pos:
        if ba_pos.side == Side.SELL and mt5_pos.side == Side.BUY:
            return HedgeMode.CONTRACTION.value
        if ba_pos.side == Side.BUY and mt5_pos.side == Side.SELL:
            return HedgeMode.EXPANSION.value
    if ba_pos:
        return (
            HedgeMode.CONTRACTION.value
            if ba_pos.side == Side.SELL
            else HedgeMode.EXPANSION.value
        )
    if mt5_pos:
        return (
            HedgeMode.CONTRACTION.value
            if mt5_pos.side == Side.BUY
            else HedgeMode.EXPANSION.value
        )
    return None


def hedge_mode_strategy_label(mode: str | None) -> str:
    """对冲模式 → 策略全称标签（用于面板展示）。"""
    if mode == HedgeMode.CONTRACTION.value:
        return "收缩策略"
    if mode == HedgeMode.EXPANSION.value:
        return "扩张策略"
    return "--"


def hedge_strategy_label_for_leg(platform: str, side: Side) -> str:
    """Fallback: infer strategy from a single leg when hedge mode unknown."""
    if side == Side.NONE:
        return "--"
    if platform == "BA":
        return "收缩" if side == Side.SELL else "扩张"
    return "扩张" if side == Side.BUY else "收缩"


def hedge_strategy_label_for_platform(platform: str, hedge_mode: str | None) -> str:
    """UI direction: BA shows order mode; Ex shows opposite hedge leg (收缩单→BA收缩/Ex扩张)."""
    if hedge_mode is None:
        return "--"
    if hedge_mode == HedgeMode.CONTRACTION.value:
        return "收缩" if platform == "BA" else "扩张"
    if hedge_mode == HedgeMode.EXPANSION.value:
        return "扩张" if platform == "BA" else "收缩"
    return "--"


def position_entry_spread(ba_pos: Position | None, mt5_pos: Position | None) -> float | None:
    """持仓加权平均入场价差：BA 均价 − Exness 均价（多次加仓由交易所合并）。"""
    if (
        ba_pos
        and mt5_pos
        and ba_pos.entry_price > 0
        and mt5_pos.entry_price > 0
    ):
        return round(ba_pos.entry_price - mt5_pos.entry_price, 3)
    return None


def spread_allows_add(
    preset_id: str,
    positions: list[Position],
    spread: float,
    mode: str,
) -> tuple[bool, str | None]:
    """已有对冲持仓时，仅当现价差不劣于持仓差价才允许加仓。"""
    preset = find_preset(preset_id)
    ba_pos = _position_for(positions, "BA", preset.symbol_ba)
    mt5_pos = _position_for(positions, "MT5", preset.symbol_mt5)
    if not ba_pos or not mt5_pos:
        return True, None
    entry = position_entry_spread(ba_pos, mt5_pos)
    if entry is None:
        return True, None
    if mode == HedgeMode.CONTRACTION.value:
        if spread + ADD_SPREAD_MARGIN < entry:
            return (
                False,
                f"当前点差 {spread:+.3f} 低于持仓差价 {entry:+.3f}，暂不加仓",
            )
    elif mode == HedgeMode.EXPANSION.value:
        if spread - ADD_SPREAD_MARGIN > entry:
            return (
                False,
                f"当前点差 {spread:+.3f} 高于持仓差价 {entry:+.3f}，暂不加仓",
            )
    return True, None


def _is_market_mode(order_mode: str) -> bool:
    """是否市价单（决定两腿并发还是顺序执行）。"""
    return order_mode == GoldOrderMode.MARKET.value


def _mt5_lots_for_ba_fill(config, preset_id: str, ba_filled_qty: float) -> float:
    """把 BA 实际成交量按配置比例换算成本次 Exness 对冲手数。"""
    ba_cfg = max(float(config.ba_quantity_for(preset_id)), 1e-9)
    mt5_cfg = float(config.mt5_lot_for(preset_id))
    return max(0.0, mt5_cfg * float(ba_filled_qty) / ba_cfg)


def _weighted_filled_price(legs: list[LegResult]) -> float:
    """多笔同端成交按成交量加权为一个均价，供记账/上报使用。"""
    total_qty = 0.0
    total_notional = 0.0
    for leg in legs:
        if leg.filled_quantity > 0 and leg.filled_price > 0:
            total_qty += leg.filled_quantity
            total_notional += leg.filled_quantity * leg.filled_price
    return total_notional / total_qty if total_qty > 0 else 0.0


def _aggregate_order_ids(legs: list[LegResult]) -> str:
    """合并分批成交的订单号，供利润计算器按官方历史精确回查。"""
    ids: list[str] = []
    seen: set[str] = set()
    for leg in legs:
        for part in str(leg.order_id or "").replace(";", ",").split(","):
            oid = part.strip()
            if oid and oid not in seen:
                ids.append(oid)
                seen.add(oid)
    return ",".join(ids)


def _aggregate_known_fee(legs: list[LegResult]) -> tuple[float, bool]:
    """聚合同一平台多笔成交的真实费用。"""
    known = [leg for leg in legs if leg.fee_known]
    if not known:
        return 0.0, False
    return round(sum(leg.fee for leg in known), 4), True


def _has_known_execution(leg: LegResult) -> bool:
    """该腿是否有确认成交量：自动补偿只允许基于确认成交，而不是状态未知。"""
    return leg.success or leg.filled_quantity > 0


def _confirmed_failure(leg: LegResult) -> bool:
    """明确未成交/未执行的失败；状态未知时不能拿它作为反向补单依据。"""
    return not leg.success and not leg.needs_reconciliation


def _open_rollback_allowed(leg: LegResult, opposite: LegResult) -> bool:
    """开仓失败时，仅在本腿确认成交且对手腿确认失败时才自动回滚。"""
    return _has_known_execution(leg) and _confirmed_failure(opposite)


def _mark_ba_followup_failed(ba: LegResult, *, action: str) -> None:
    """BA 已成交但 Exness 跟随腿失败时，修正 BA 单腿日志里的乐观文案。"""
    followup = "Exness 补平失败" if action == "close" else "Exness 补对冲失败"
    if not ba.message:
        ba.message = followup
    elif "已按成交量补 Exness" in ba.message:
        ba.message = ba.message.replace("已按成交量补 Exness", followup)
    elif followup not in ba.message:
        ba.message = f"{ba.message}；{followup}"


def _restore_closed_legs(
    binance: "BinanceConnector",
    mt5: "MT5Connector",
    preset_id: str,
    mode: str,
    ba: LegResult,
    mt5_leg: LegResult,
) -> None:
    """平仓部分失败时，只在对手腿确认失败时按确认平仓量恢复。"""
    if (
        _has_known_execution(ba)
        and _confirmed_failure(mt5_leg)
        and ba.filled_quantity > 0
    ):
        restore = binance.open_hedge_leg(
            preset_id,
            mode,
            GoldOrderMode.MARKET.value,
            qty_override=ba.filled_quantity,
        )
        ba.compensated = restore.success
        ba.compensation_message = (
            f"BA 已按刚平仓量 {ba.filled_quantity:g} 市价恢复对冲"
            if restore.success
            else restore.message
        )
    if (
        _has_known_execution(mt5_leg)
        and _confirmed_failure(ba)
        and mt5_leg.filled_quantity > 0
    ):
        restore = mt5.open_hedge_leg(
            preset_id,
            mode,
            GoldOrderMode.MARKET.value,
            lots_override=mt5_leg.filled_quantity,
        )
        mt5_leg.compensated = restore.success
        mt5_leg.compensation_message = (
            f"Exness 已按刚平仓量 {mt5_leg.filled_quantity:g} 手市价恢复对冲"
            if restore.success
            else restore.message
        )


def open_hedge(
    binance: "BinanceConnector",
    mt5: "MT5Connector",
    preset_id: str,
    mode: str = HedgeMode.CONTRACTION.value,
    order_mode: str = GoldOrderMode.MAKER.value,
    *,
    had_position: bool | None = None,
    spread_guard: Callable[[], bool] | None = None,
) -> HedgeTradeResult:
    """开（加）对冲仓：BA 与 Exness 各下一腿，失败时自动回滚已成交腿。

    had_position 表示下单前是否已有持仓（仅用于日志区分"开仓/加仓"）；
    调用方应传入缓存值以避免热路径上的实时持仓拉取。
    """
    preset = find_preset(preset_id)
    # had_position 仅用于日志区分「开仓/加仓」。下单热路径上调用方应传入
    # 已缓存的持仓判断，避免在点击下单瞬间做一次实时 MT5 持仓拉取（IPC 阻塞）。
    if had_position is None:
        had_position = (
            _position_for(binance.get_positions(), "BA", preset.symbol_ba) is not None
            or _position_for(mt5.get_positions(), "MT5", preset.symbol_mt5) is not None
        )
    if _is_market_mode(order_mode):
        with ThreadPoolExecutor(max_workers=2, thread_name_prefix="hedge-open") as pool:
            ba_f = pool.submit(binance.open_hedge_leg, preset_id, mode, order_mode)
            mt5_f = pool.submit(mt5.open_hedge_leg, preset_id, mode, order_mode)
            ba = ba_f.result()
            mt5_leg = mt5_f.result()
    else:
        mt5_legs: list[LegResult] = []
        mt5_failed = False

        def hedge_ba_fill(ba_delta: float) -> bool:
            nonlocal mt5_failed
            lots = _mt5_lots_for_ba_fill(binance.config, preset_id, ba_delta)
            if lots <= 0:
                return True
            leg = mt5.open_hedge_leg(
                preset_id,
                mode,
                GoldOrderMode.MARKET.value,
                lots_override=lots,
            )
            mt5_legs.append(leg)
            mt5_failed = mt5_failed or not leg.success
            return leg.success

        # Maker/限价按 BA 实际成交量驱动 EX：等待期间每新增成交就补一笔；
        # 超时撤单后 BA 连接器会再读一次最终成交量，补上取消前最后成交。
        ba = binance.open_hedge_leg(
            preset_id,
            mode,
            order_mode,
            on_fill_delta=hedge_ba_fill,
            should_keep_waiting=spread_guard,
        )
        if mt5_legs:
            mt5_success = all(leg.success for leg in mt5_legs)
            mt5_needs_reconciliation = any(
                leg.needs_reconciliation for leg in mt5_legs
            )
            mt5_fee, mt5_fee_known = _aggregate_known_fee(mt5_legs)
            mt5_leg = LegResult(
                platform="MT5",
                success=mt5_success,
                message=(
                    f"Exness 已按 BA 实际成交分批补 {sum(leg.filled_quantity for leg in mt5_legs):g} 手"
                    if mt5_success
                    else "Exness 分批补对冲失败，请立即检查单边敞口"
                ),
                order_id=_aggregate_order_ids(mt5_legs),
                filled_quantity=sum(leg.filled_quantity for leg in mt5_legs),
                filled_price=_weighted_filled_price(mt5_legs),
                fee=mt5_fee,
                fee_known=mt5_fee_known,
                needs_reconciliation=mt5_needs_reconciliation,
            )
        else:
            mt5_leg = LegResult(
                platform="MT5",
                success=False,
                message="BA 委托未成交，已跳过 Exness 对冲下单",
            )
        if mt5_failed:
            if mt5_leg.needs_reconciliation:
                ba.needs_reconciliation = True
            _mark_ba_followup_failed(ba, action="open")
    legs = [ba, mt5_leg]
    success = all(leg.success for leg in legs)
    # 自动回滚必须真正消除敞口：统一走市价平仓。限价/Maker 回滚单可能挂不上或不成交、
    # 超时被撤后敞口仍残留（曾导致「部分成功，自动回滚失败」）。
    # 关键：只回滚「本次实际成交的增量」(filled_quantity)，绝不能 close_all——
    # 否则加仓失败回滚会把用户原有持仓一并平掉（曾导致加 0.01 却平 0.05 全仓）。
    # 状态未知时不按默认量盲目回滚；否则可能在对手腿其实已成交时反向补出新单边。
    if not success and _open_rollback_allowed(ba, mt5_leg):
        rollback = binance.close_hedge_leg(
            preset_id,
            GoldOrderMode.MARKET.value,
            mode,
            qty_override=ba.filled_quantity if ba.filled_quantity > 0 else None,
        )
        ba.compensated = rollback.success
        ba.compensation_message = rollback.message
    if not success and _open_rollback_allowed(mt5_leg, ba):
        rollback = mt5.close_hedge_leg(
            preset_id,
            GoldOrderMode.MARKET.value,
            mode,
            lots_override=mt5_leg.filled_quantity if mt5_leg.filled_quantity > 0 else None,
        )
        mt5_leg.compensated = rollback.success
        mt5_leg.compensation_message = rollback.message
    label = preset_display_name(preset_id)
    mlabel = _mode_label(mode)
    om_label = order_mode_log_label(preset_id, order_mode)
    verb = "加仓" if had_position else "开仓"
    if success:
        message = f"{label}{verb}{mlabel}({om_label})完成"
    elif any(
        leg.success or leg.needs_reconciliation or leg.filled_quantity > 0
        for leg in legs
    ):
        compensated_legs = [leg for leg in legs if leg.compensation_message]
        if compensated_legs and all(leg.compensated for leg in compensated_legs):
            message = (
                f"⚠ {label}{verb}{mlabel}({om_label})部分成功，"
                "系统已尝试自动回滚"
            )
        elif compensated_legs:
            message = (
                f"⚠ {label}{verb}{mlabel}({om_label})部分成功，"
                "自动回滚失败，请立即检查单边敞口"
            )
        elif any(leg.needs_reconciliation for leg in legs):
            message = (
                f"⚠ {label}{verb}{mlabel}({om_label})部分成功，"
                "状态待对账，已暂停自动回滚，请立即检查持仓"
            )
        else:
            message = f"⚠ {label}{verb}{mlabel}({om_label})部分成功，请立即检查单边敞口"
    else:
        message = f"{label}{verb}{mlabel}({om_label})失败"
    return HedgeTradeResult(action="open", success=success, legs=legs, message=message)


def close_hedge(
    binance: "BinanceConnector",
    mt5: "MT5Connector",
    preset_id: str,
    mode: str = HedgeMode.CONTRACTION.value,
    order_mode: str = GoldOrderMode.MAKER.value,
) -> HedgeTradeResult:
    """平对冲仓。

    - 市价：两腿并发平，最大化速度；
    - Maker/限价：以 BA 为主——先挂 BA reduceOnly 限价平仓，按 BA 实际成交量分批用
      Exness 市价同步平仓，绝不先平 Exness 跑单边（与开仓逻辑完全对称）。
    部分成功会提示检查剩余持仓。
    """
    if _is_market_mode(order_mode):
        with ThreadPoolExecutor(max_workers=2, thread_name_prefix="hedge-close") as pool:
            ba_f = pool.submit(binance.close_hedge_leg, preset_id, order_mode, mode)
            mt5_f = pool.submit(mt5.close_hedge_leg, preset_id, order_mode, mode)
            ba = ba_f.result()
            mt5_leg = mt5_f.result()
    else:
        mt5_legs: list[LegResult] = []
        mt5_failed = False

        def hedge_ba_close_fill(ba_delta: float) -> bool:
            nonlocal mt5_failed
            lots = _mt5_lots_for_ba_fill(binance.config, preset_id, ba_delta)
            if lots <= 0:
                return True
            leg = mt5.close_hedge_leg(
                preset_id,
                GoldOrderMode.MARKET.value,
                mode,
                lots_override=lots,
            )
            mt5_legs.append(leg)
            mt5_failed = mt5_failed or not leg.success
            return leg.success

        # Maker/限价平仓：BA reduceOnly 限价为主，按 BA 每次新增成交量补 Exness 市价平；
        # 超时撤单后 BA 连接器会再读一次最终成交量，补上取消前最后成交。
        ba = binance.close_hedge_leg(
            preset_id,
            order_mode,
            mode,
            on_fill_delta=hedge_ba_close_fill,
        )
        if mt5_legs:
            mt5_success = all(leg.success for leg in mt5_legs)
            mt5_needs_reconciliation = any(
                leg.needs_reconciliation for leg in mt5_legs
            )
            failed_msgs = "; ".join(
                leg.message for leg in mt5_legs if not leg.success and leg.message
            )
            mt5_fee, mt5_fee_known = _aggregate_known_fee(mt5_legs)
            mt5_leg = LegResult(
                platform="MT5",
                success=mt5_success,
                message=(
                    f"Exness 已按 BA 实际成交分批平 {sum(leg.filled_quantity for leg in mt5_legs):g} 手"
                    if mt5_success
                    else f"Exness 分批平对冲失败：{failed_msgs or '请立即检查单边敞口'}"
                ),
                order_id=_aggregate_order_ids(mt5_legs),
                filled_quantity=sum(leg.filled_quantity for leg in mt5_legs),
                filled_price=_weighted_filled_price(mt5_legs),
                fee=mt5_fee,
                fee_known=mt5_fee_known,
                needs_reconciliation=mt5_needs_reconciliation,
            )
        elif ba.success:
            # BA 端本就无可平持仓（已为空）：此时不存在"BA 成交驱动"，直接清理
            # Exness 可能残留的单边持仓使两端归零；Ex 也为空则 no-op 成功。
            mt5_leg = mt5.close_hedge_leg(
                preset_id, GoldOrderMode.MARKET.value, mode, close_all=True
            )
        else:
            # BA 委托未成交且确有持仓未平：未触碰 Exness，不会跑单边
            mt5_leg = LegResult(
                platform="MT5",
                success=False,
                message="BA 委托未成交，已跳过 Exness 对冲平仓",
            )
        if mt5_failed:
            if mt5_leg.needs_reconciliation:
                ba.needs_reconciliation = True
            _mark_ba_followup_failed(ba, action="close")
    legs = [ba, mt5_leg]
    success = all(leg.success for leg in legs)
    if not success:
        _restore_closed_legs(binance, mt5, preset_id, mode, ba, mt5_leg)
    label = preset_display_name(preset_id)
    mlabel = _mode_label(mode)
    om_label = order_mode_log_label(preset_id, order_mode)
    if success:
        message = f"{label}平仓{mlabel}({om_label})完成"
    elif any(
        leg.success or leg.needs_reconciliation or leg.filled_quantity > 0
        for leg in legs
    ):
        restored = [leg for leg in legs if leg.compensation_message]
        if restored and all(leg.compensated for leg in restored):
            message = (
                f"⚠ {label}平仓{mlabel}({om_label})部分成功，"
                "系统已尝试自动恢复对冲，请检查剩余持仓"
            )
        elif restored:
            message = (
                f"⚠ {label}平仓{mlabel}({om_label})部分成功，"
                "自动恢复对冲失败，请立即检查单边敞口"
            )
        elif any(leg.needs_reconciliation for leg in legs):
            message = (
                f"⚠ {label}平仓{mlabel}({om_label})部分成功，"
                "状态待对账，已暂停自动恢复，请立即检查持仓"
            )
        else:
            message = f"⚠ {label}平仓{mlabel}({om_label})部分成功，请检查剩余持仓"
    else:
        message = f"{label}平仓{mlabel}({om_label})失败"
    return HedgeTradeResult(action="close", success=success, legs=legs, message=message)
