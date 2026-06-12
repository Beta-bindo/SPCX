"""自动交易引擎：当点差满足阈值时自动开/平对冲仓。

核心是一个基于阈值的状态机：
- 点差满足阈值且对应方向已勾选时立即触发；
- 点差回落超过 RESET_MARGIN（迟滞）则重置计时；
- 触发后进入 COOLDOWN_SEC 冷却，防止连续重复下单。

每个品种可有多个 lane（黄金支持 maker + market，白银仅 market），各 lane 独立计时。
本模块只产出"下单意图"列表，实际下单由调用方（spread_engine）执行。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.core.models import AppConfig, HedgeMode, SpreadSnapshot
from app.core.order_mode import auto_trade_order_mode
from app.core.trading_service import detect_hedge_mode, spread_allows_add

COOLDOWN_SEC = 30.0   # 同一品种+lane 两次自动下单的最短间隔
RESET_MARGIN = 0.03   # 迟滞带：点差需回落超过此值才重置计时，避免临界抖动
LANE_MAKER = "maker"
LANE_MARKET = "market"


@dataclass
class AutoTradeState:
    """自动交易的跨周期状态（计时与冷却），键为 (品种, 模式, lane) 或 (品种, lane)。"""

    since: dict[tuple[str, str, str], float | None] = field(default_factory=dict)         # 开仓条件满足的起始时刻
    last_fire: dict[tuple[str, str], float] = field(default_factory=dict)                 # 上次开仓触发时刻
    close_since: dict[tuple[str, str, str], float | None] = field(default_factory=dict)   # 平仓条件满足的起始时刻
    last_close_fire: dict[tuple[str, str], float] = field(default_factory=dict)           # 上次平仓触发时刻


@dataclass
class AutoTradeProgress:
    """自动交易进度（保留给 UI 兼容；即时触发后通常不会展示倒计时）。"""

    preset_id: str
    mode: str
    label: str
    elapsed_sec: float
    hold_sec: float


def _lanes_for_preset(preset_id: str) -> tuple[str, ...]:
    """该品种支持的 lane：黄金支持 Maker+市价，白银仅市价。"""
    if preset_id == "xau":
        return (LANE_MAKER, LANE_MARKET)
    return (LANE_MARKET,)


def _lane_label(lane: str) -> str:
    """lane → 显示后缀（市价 lane 显示"市价"，Maker lane 留空）。"""
    return "市价" if lane == LANE_MARKET else ""


def _reset_preset(state: AutoTradeState, preset_id: str) -> None:
    """清空该品种所有 lane 的开仓计时。"""
    for lane in _lanes_for_preset(preset_id):
        _reset_lane_open_timers(state, preset_id, lane)


def _reset_lane_open_timers(state: AutoTradeState, preset_id: str, lane: str) -> None:
    """清空某 lane 下两个方向的开仓计时。"""
    for mode in (HedgeMode.CONTRACTION.value, HedgeMode.EXPANSION.value):
        state.since[(preset_id, mode, lane)] = None


def _reset_open_timer(
    state: AutoTradeState, preset_id: str, mode: str, lane: str
) -> None:
    """清空单个 (品种, 模式, lane) 的开仓计时。"""
    state.since[(preset_id, mode, lane)] = None


def _mode_enabled(config: AppConfig, preset_id: str, mode: str, lane: str) -> bool:
    """该方向在该 lane 上是否开启了自动开仓。"""
    if mode == HedgeMode.CONTRACTION.value:
        return config.auto_contraction_on_lane(preset_id, lane)
    return config.auto_expansion_on_lane(preset_id, lane)


def _mode_satisfied(config: AppConfig, preset_id: str, mode: str, spread: float, lane: str) -> bool:
    """当前点差是否满足开仓阈值（收缩要求 ≥ 上阈值，扩张要求 ≤ 下阈值）。"""
    if mode == HedgeMode.CONTRACTION.value:
        return spread >= config.auto_contraction_threshold_lane(preset_id, lane)
    return spread <= config.auto_expansion_threshold_lane(preset_id, lane)


def _mode_reset(config: AppConfig, preset_id: str, mode: str, spread: float, lane: str) -> bool:
    """点差是否已回落出迟滞带，需重置计时。"""
    if mode == HedgeMode.CONTRACTION.value:
        return spread < config.auto_contraction_threshold_lane(preset_id, lane) - RESET_MARGIN
    return spread > config.auto_expansion_threshold_lane(preset_id, lane) + RESET_MARGIN


def _active_modes(
    config: AppConfig,
    preset_id: str,
    spread: float,
    state: AutoTradeState,
    lane: str,
) -> list[str]:
    """该 lane 上当前"处于激活态"的方向：满足阈值，或已在迟滞带内计时尚未重置。"""
    modes: list[str] = []
    for mode in (HedgeMode.CONTRACTION.value, HedgeMode.EXPANSION.value):
        if not _mode_enabled(config, preset_id, mode, lane):
            continue
        key = (preset_id, mode, lane)
        if _mode_satisfied(config, preset_id, mode, spread, lane):
            modes.append(mode)
        elif state.since.get(key) is not None and not _mode_reset(
            config, preset_id, mode, spread, lane
        ):
            modes.append(mode)
    return modes


def _open_modes_for_evaluation(
    config: AppConfig,
    preset_id: str,
    spread: float,
    state: AutoTradeState,
    positions: list,
    lane: str,
) -> list[str]:
    """评估可开仓方向：已有持仓时只允许沿现有对冲方向加仓，避免反向对锁。"""
    active = detect_hedge_mode(preset_id, positions)
    modes = _active_modes(config, preset_id, spread, state, lane)
    if active is None:
        return modes
    if not _mode_enabled(config, preset_id, active, lane):
        return []
    return [m for m in modes if m == active]


def _opposing_open_enabled(config: AppConfig, preset_id: str, opposing: str) -> bool:
    """反方向是否在任一 lane 开启了自动开仓（用于诊断冲突）。"""
    for lane in _lanes_for_preset(preset_id):
        if _mode_enabled(config, preset_id, opposing, lane):
            return True
    return False


def _opposing_close_enabled(config: AppConfig, preset_id: str, opposing: str) -> bool:
    """反方向是否在任一 lane 开启了自动平仓（用于诊断冲突）。"""
    for lane in _lanes_for_preset(preset_id):
        if _close_mode_enabled(config, preset_id, opposing, lane):
            return True
    return False


def collect_auto_trade_progress(
    config: AppConfig,
    spreads: dict[str, SpreadSnapshot],
    positions: list,
    now: float,
    state: AutoTradeState,
    *,
    preset_ids: tuple[str, ...] = ("xau", "xag"),
) -> AutoTradeProgress | None:
    """汇总所有品种/lane 的开仓倒计时，返回进度最大的一项供 UI 展示。"""
    best: AutoTradeProgress | None = None
    for preset_id in preset_ids:
        active = detect_hedge_mode(preset_id, positions)
        snap = spreads.get(preset_id)
        if snap is None:
            continue
        spread = snap.mid_spread
        label = "黄金" if preset_id == "xau" else "白银"
        for lane in _lanes_for_preset(preset_id):
            if active is not None:
                open_modes = [active] if _mode_enabled(config, preset_id, active, lane) else []
            else:
                open_modes = [
                    m
                    for m in (HedgeMode.CONTRACTION.value, HedgeMode.EXPANSION.value)
                    if _mode_enabled(config, preset_id, m, lane)
                ]
            for mode in open_modes:
                if active is None and mode not in _active_modes(
                    config, preset_id, spread, state, lane
                ):
                    continue
                if active is not None and mode not in _open_modes_for_evaluation(
                    config, preset_id, spread, state, positions, lane
                ):
                    continue
                key = (preset_id, mode, lane)
                started = state.since.get(key)
                if started is None:
                    continue
                elapsed = max(0.0, now - started)
                if elapsed >= hold_sec:
                    continue
                mlabel = "收缩" if mode == HedgeMode.CONTRACTION.value else "扩张"
                lane_text = _lane_label(lane)
                suffix = f"·{lane_text}" if lane_text else ""
                progress = AutoTradeProgress(
                    preset_id=preset_id,
                    mode=mode,
                    label=f"{label}{mlabel}{suffix}",
                    elapsed_sec=elapsed,
                    hold_sec=hold_sec,
                )
                if best is None or progress.elapsed_sec > best.elapsed_sec:
                    best = progress
    return best


def diagnose_auto_trade_block(
    config: AppConfig,
    spreads: dict[str, SpreadSnapshot],
    positions: list,
    *,
    preset_ids: tuple[str, ...] = ("xau", "xag"),
    engine_running: bool = True,
) -> str | None:
    """诊断自动交易为何未触发，返回一句可读的阻塞原因（无阻塞则 None）。

    覆盖：引擎未启动、已有持仓导致反向冲突、点差未达阈值等常见情形，便于排障。
    """
    if not engine_running:
        for preset_id in preset_ids:
            for lane in _lanes_for_preset(preset_id):
                if _mode_enabled(config, preset_id, HedgeMode.CONTRACTION.value, lane) or _mode_enabled(
                    config, preset_id, HedgeMode.EXPANSION.value, lane
                ):
                    return "自动下单：请先点击「启动」开启监控"
        return None

    for preset_id in preset_ids:
        label = "黄金" if preset_id == "xau" else "白银"
        active = detect_hedge_mode(preset_id, positions)
        if active is not None:
            opposing = (
                HedgeMode.EXPANSION.value
                if active == HedgeMode.CONTRACTION.value
                else HedgeMode.CONTRACTION.value
            )
            if _opposing_open_enabled(config, preset_id, opposing):
                mlabel = "收缩" if active == HedgeMode.CONTRACTION.value else "扩张"
                opp_label = "扩张" if opposing == HedgeMode.EXPANSION.value else "收缩"
                return (
                    f"自动下单：{label} 已有{mlabel}持仓，"
                    f"无法启用{opp_label}方向自动开仓"
                )
            if _opposing_close_enabled(config, preset_id, opposing):
                mlabel = "收缩" if active == HedgeMode.CONTRACTION.value else "扩张"
                opp_label = "扩张" if opposing == HedgeMode.EXPANSION.value else "收缩"
                return (
                    f"自动下单：{label} 已有{mlabel}持仓，"
                    f"无法启用{opp_label}方向自动平仓"
                )
            for lane in _lanes_for_preset(preset_id):
                if not _mode_enabled(config, preset_id, active, lane):
                    continue
                snap = spreads.get(preset_id)
                if snap is None:
                    continue
                spread = snap.mid_spread
                if _mode_satisfied(config, preset_id, active, spread, lane):
                    continue
                th = (
                    config.auto_contraction_threshold_lane(preset_id, lane)
                    if active == HedgeMode.CONTRACTION.value
                    else config.auto_expansion_threshold_lane(preset_id, lane)
                )
                op = "≥" if active == HedgeMode.CONTRACTION.value else "≤"
                mlabel = "收缩" if active == HedgeMode.CONTRACTION.value else "扩张"
                lane_text = _lane_label(lane)
                prefix = f"{lane_text}" if lane_text else "Maker"
                if active == HedgeMode.CONTRACTION.value and spread < th - RESET_MARGIN:
                    return (
                        f"自动下单：{label} {prefix}点差 {spread:+.3f} "
                        f"未达{mlabel}加仓阈值 {op} {th:.3f}"
                    )
                if active == HedgeMode.EXPANSION.value and spread > th + RESET_MARGIN:
                    return (
                        f"自动下单：{label} {prefix}点差 {spread:+.3f} "
                        f"未达{mlabel}加仓阈值 {op} {th:.3f}"
                    )
            continue
        snap = spreads.get(preset_id)
        if snap is None:
            continue
        spread = snap.mid_spread
        for lane in _lanes_for_preset(preset_id):
            if _mode_enabled(config, preset_id, HedgeMode.CONTRACTION.value, lane) and not _mode_satisfied(
                config, preset_id, HedgeMode.CONTRACTION.value, spread, lane
            ):
                th = config.auto_contraction_threshold_lane(preset_id, lane)
                if spread < th - RESET_MARGIN:
                    lane_text = _lane_label(lane)
                    prefix = f"{lane_text}" if lane_text else "Maker"
                    return f"自动下单：{label} {prefix}点差 {spread:+.3f} 未达收缩阈值 ≥ {th:.3f}"
            if _mode_enabled(config, preset_id, HedgeMode.EXPANSION.value, lane) and not _mode_satisfied(
                config, preset_id, HedgeMode.EXPANSION.value, spread, lane
            ):
                th = config.auto_expansion_threshold_lane(preset_id, lane)
                if spread > th + RESET_MARGIN:
                    lane_text = _lane_label(lane)
                    prefix = f"{lane_text}" if lane_text else "Maker"
                    return f"自动下单：{label} {prefix}点差 {spread:+.3f} 未达扩张阈值 ≤ {th:.3f}"
    return None


def evaluate_auto_trades(
    config: AppConfig,
    spreads: dict[str, SpreadSnapshot],
    positions: list,
    now: float,
    state: AutoTradeState,
    *,
    preset_ids: tuple[str, ...] = ("xau", "xag"),
) -> list[tuple[str, str, str, str]]:
    """评估并产出本轮应自动开仓的下单意图列表。

    返回每项为 (品种, 对冲模式, 下单模式, 日志文案)。会就地推进/重置 state 中的计时器，
    并在触发后写入冷却时间；每个品种本轮至多触发一次。
    """
    orders: list[tuple[str, str, str, str]] = []

    for preset_id in preset_ids:
        label = "黄金" if preset_id == "xau" else "白银"
        active = detect_hedge_mode(preset_id, positions)
        snap = spreads.get(preset_id)
        if snap is None:
            _reset_preset(state, preset_id)
            continue

        spread = snap.mid_spread
        hold_sec = config.auto_trade_hold_sec(preset_id)

        for lane in _lanes_for_preset(preset_id):
            modes = _open_modes_for_evaluation(
                config, preset_id, spread, state, positions, lane
            )
            order_mode = auto_trade_order_mode(preset_id, lane)

            for mode in (HedgeMode.CONTRACTION.value, HedgeMode.EXPANSION.value):
                key = (preset_id, mode, lane)
                if mode not in modes:
                    if _mode_reset(config, preset_id, mode, spread, lane) or not _mode_enabled(
                        config, preset_id, mode, lane
                    ):
                        state.since[key] = None
                    elif active is not None and mode != active:
                        state.since[key] = None

            if not modes:
                continue

            # 冷却期内不重复触发
            fire_key = (preset_id, lane)
            last = state.last_fire.get(fire_key)
            if last is not None and now - last < COOLDOWN_SEC:
                continue

            for mode in modes:
                key = (preset_id, mode, lane)
                if state.since.get(key) is None:
                    state.since[key] = now
                if not _mode_satisfied(config, preset_id, mode, spread, lane):
                    state.since[key] = None
                    continue

                if active is not None:
                    # 加仓前再校验现价差不劣于持仓差价
                    ok, reason = spread_allows_add(preset_id, positions, spread, mode)
                    if not ok:
                        continue

                mlabel = "收缩" if mode == HedgeMode.CONTRACTION.value else "扩张"
                thresh = (
                    config.auto_contraction_threshold_lane(preset_id, lane)
                    if mode == HedgeMode.CONTRACTION.value
                    else config.auto_expansion_threshold_lane(preset_id, lane)
                )
                op = "≥" if mode == HedgeMode.CONTRACTION.value else "≤"
                lane_text = _lane_label(lane)
                mode_text = f" · {lane_text}" if lane_text else " · Maker"
                orders.append(
                    (
                        preset_id,
                        mode,
                        order_mode,
                        (
                            f"[自动下单] {label} 点差 {spread:+.3f} {op} {thresh:.3f}，"
                            f"已满足 · {mlabel}开仓{mode_text}"
                        ),
                    )
                )
                state.last_fire[fire_key] = now
                _reset_lane_open_timers(state, preset_id, lane)
                break
            if orders and orders[-1][0] == preset_id:
                break

    return orders


def _reset_close_preset(state: AutoTradeState, preset_id: str) -> None:
    """清空该品种所有 lane 的平仓计时。"""
    for lane in _lanes_for_preset(preset_id):
        for mode in (HedgeMode.CONTRACTION.value, HedgeMode.EXPANSION.value):
            state.close_since[(preset_id, mode, lane)] = None


def _close_mode_enabled(config: AppConfig, preset_id: str, mode: str, lane: str) -> bool:
    """该方向在该 lane 上是否开启了自动平仓。"""
    if mode == HedgeMode.CONTRACTION.value:
        return config.auto_close_contraction_on_lane(preset_id, lane)
    return config.auto_close_expansion_on_lane(preset_id, lane)


def _close_mode_satisfied(
    config: AppConfig, preset_id: str, mode: str, spread: float, lane: str
) -> bool:
    """点差是否满足平仓阈值（平仓阈值方向与开仓相反：收缩仓在点差回落时平）。"""
    if mode == HedgeMode.CONTRACTION.value:
        return spread <= config.auto_close_contraction_threshold_lane(preset_id, lane)
    return spread >= config.auto_close_expansion_threshold_lane(preset_id, lane)


def _close_mode_reset(
    config: AppConfig, preset_id: str, mode: str, spread: float, lane: str
) -> bool:
    """点差是否已回到不该平仓的一侧（出迟滞带），需重置平仓计时。"""
    if mode == HedgeMode.CONTRACTION.value:
        return spread > config.auto_close_contraction_threshold_lane(preset_id, lane) + RESET_MARGIN
    return spread < config.auto_close_expansion_threshold_lane(preset_id, lane) - RESET_MARGIN


def collect_auto_close_progress(
    config: AppConfig,
    spreads: dict[str, SpreadSnapshot],
    positions: list,
    now: float,
    state: AutoTradeState,
    *,
    preset_ids: tuple[str, ...] = ("xau", "xag"),
) -> AutoTradeProgress | None:
    """汇总所有品种/lane 的平仓倒计时，返回进度最大的一项供 UI 展示。"""
    best: AutoTradeProgress | None = None
    for preset_id in preset_ids:
        mode = detect_hedge_mode(preset_id, positions)
        if mode is None:
            continue
        snap = spreads.get(preset_id)
        if snap is None:
            continue
        label = "黄金" if preset_id == "xau" else "白银"
        mlabel = "收缩" if mode == HedgeMode.CONTRACTION.value else "扩张"
        for lane in _lanes_for_preset(preset_id):
            if not _close_mode_enabled(config, preset_id, mode, lane):
                continue
            key = (preset_id, mode, lane)
            started = state.close_since.get(key)
            if started is None:
                continue
            elapsed = max(0.0, now - started)
            if elapsed >= hold_sec:
                continue
            lane_text = _lane_label(lane)
            suffix = f"·{lane_text}" if lane_text else ""
            progress = AutoTradeProgress(
                preset_id=preset_id,
                mode=mode,
                label=f"{label}{mlabel}平仓{suffix}",
                elapsed_sec=elapsed,
                hold_sec=hold_sec,
            )
            if best is None or progress.elapsed_sec > best.elapsed_sec:
                best = progress
    return best


def evaluate_auto_closes(
    config: AppConfig,
    spreads: dict[str, SpreadSnapshot],
    positions: list,
    now: float,
    state: AutoTradeState,
    *,
    preset_ids: tuple[str, ...] = ("xau", "xag"),
) -> list[tuple[str, str, str, str]]:
    """评估并产出本轮应自动平仓的下单意图列表（结构同 evaluate_auto_trades）。

    仅对已存在对冲持仓的品种生效；同样遵循即时触发、迟滞重置与冷却。
    """
    orders: list[tuple[str, str, str, str]] = []

    for preset_id in preset_ids:
        label = "黄金" if preset_id == "xau" else "白银"
        mode = detect_hedge_mode(preset_id, positions)
        if mode is None:
            _reset_close_preset(state, preset_id)
            continue

        snap = spreads.get(preset_id)
        if snap is None:
            _reset_close_preset(state, preset_id)
            continue

        spread = snap.mid_spread

        for lane in _lanes_for_preset(preset_id):
            if not _close_mode_enabled(config, preset_id, mode, lane):
                state.close_since[(preset_id, mode, lane)] = None
                continue

            key = (preset_id, mode, lane)
            fire_key = (preset_id, lane)

            if _close_mode_reset(config, preset_id, mode, spread, lane):
                state.close_since[key] = None
                continue

            if not _close_mode_satisfied(config, preset_id, mode, spread, lane):
                state.close_since[key] = None
                continue

            last = state.last_close_fire.get(fire_key)
            if last is not None and now - last < COOLDOWN_SEC:
                continue

            if state.close_since.get(key) is None:
                state.close_since[key] = now

            mlabel = "收缩" if mode == HedgeMode.CONTRACTION.value else "扩张"
            if mode == HedgeMode.CONTRACTION.value:
                thresh = config.auto_close_contraction_threshold_lane(preset_id, lane)
                op = "≤"
            else:
                thresh = config.auto_close_expansion_threshold_lane(preset_id, lane)
                op = "≥"
            order_mode = auto_trade_order_mode(preset_id, lane)
            lane_text = _lane_label(lane)
            mode_text = f" · {lane_text}" if lane_text else " · Maker"
            orders.append(
                (
                    preset_id,
                    mode,
                    order_mode,
                    (
                        f"[自动平仓] {label} 点差 {spread:+.3f} {op} {thresh:.3f}，"
                        f"已满足 · {mlabel}平仓{mode_text}"
                    ),
                )
            )
            state.last_close_fire[fire_key] = now
            state.close_since[key] = None
            break

    return orders
