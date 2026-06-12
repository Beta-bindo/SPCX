"""Auto-open hedge when spread satisfies contraction/expansion thresholds."""

from __future__ import annotations

from dataclasses import dataclass, field

from app.core.models import AppConfig, HedgeMode, SpreadSnapshot
from app.core.order_mode import auto_trade_order_mode
from app.core.trading_service import detect_hedge_mode, spread_allows_add

COOLDOWN_SEC = 30.0
RESET_MARGIN = 0.03
LANE_MAKER = "maker"
LANE_MARKET = "market"


@dataclass
class AutoTradeState:
    since: dict[tuple[str, str, str], float | None] = field(default_factory=dict)
    last_fire: dict[tuple[str, str], float] = field(default_factory=dict)
    close_since: dict[tuple[str, str, str], float | None] = field(default_factory=dict)
    last_close_fire: dict[tuple[str, str], float] = field(default_factory=dict)


@dataclass
class AutoTradeProgress:
    preset_id: str
    mode: str
    label: str
    elapsed_sec: float
    hold_sec: float


def _lanes_for_preset(preset_id: str) -> tuple[str, ...]:
    if preset_id == "xau":
        return (LANE_MAKER, LANE_MARKET)
    return (LANE_MARKET,)


def _lane_label(lane: str) -> str:
    return "市价" if lane == LANE_MARKET else ""


def _reset_preset(state: AutoTradeState, preset_id: str) -> None:
    for lane in _lanes_for_preset(preset_id):
        _reset_lane_open_timers(state, preset_id, lane)


def _reset_lane_open_timers(state: AutoTradeState, preset_id: str, lane: str) -> None:
    for mode in (HedgeMode.CONTRACTION.value, HedgeMode.EXPANSION.value):
        state.since[(preset_id, mode, lane)] = None


def _reset_open_timer(
    state: AutoTradeState, preset_id: str, mode: str, lane: str
) -> None:
    state.since[(preset_id, mode, lane)] = None


def _mode_enabled(config: AppConfig, preset_id: str, mode: str, lane: str) -> bool:
    if mode == HedgeMode.CONTRACTION.value:
        return config.auto_contraction_on_lane(preset_id, lane)
    return config.auto_expansion_on_lane(preset_id, lane)


def _mode_satisfied(config: AppConfig, preset_id: str, mode: str, spread: float, lane: str) -> bool:
    if mode == HedgeMode.CONTRACTION.value:
        return spread >= config.auto_contraction_threshold_lane(preset_id, lane)
    return spread <= config.auto_expansion_threshold_lane(preset_id, lane)


def _mode_reset(config: AppConfig, preset_id: str, mode: str, spread: float, lane: str) -> bool:
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
    active = detect_hedge_mode(preset_id, positions)
    modes = _active_modes(config, preset_id, spread, state, lane)
    if active is None:
        return modes
    if not _mode_enabled(config, preset_id, active, lane):
        return []
    return [m for m in modes if m == active]


def _opposing_open_enabled(config: AppConfig, preset_id: str, opposing: str) -> bool:
    for lane in _lanes_for_preset(preset_id):
        if _mode_enabled(config, preset_id, opposing, lane):
            return True
    return False


def _opposing_close_enabled(config: AppConfig, preset_id: str, opposing: str) -> bool:
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
    best: AutoTradeProgress | None = None
    for preset_id in preset_ids:
        active = detect_hedge_mode(preset_id, positions)
        snap = spreads.get(preset_id)
        if snap is None:
            continue
        spread = snap.mid_spread
        hold_sec = config.auto_trade_hold_sec(preset_id)
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
    """Return list of (preset_id, hedge_mode, order_mode, log_message)."""
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

            fire_key = (preset_id, lane)
            last = state.last_fire.get(fire_key)
            if last is not None and now - last < COOLDOWN_SEC:
                continue

            for mode in modes:
                key = (preset_id, mode, lane)
                started = state.since.get(key)
                if started is None:
                    state.since[key] = now
                    continue
                if now - started < hold_sec:
                    continue
                if not _mode_satisfied(config, preset_id, mode, spread, lane):
                    state.since[key] = None
                    continue

                if active is not None:
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
                            f"连续 {hold_sec:.0f}s 满足 · {mlabel}开仓{mode_text}"
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
    for lane in _lanes_for_preset(preset_id):
        for mode in (HedgeMode.CONTRACTION.value, HedgeMode.EXPANSION.value):
            state.close_since[(preset_id, mode, lane)] = None


def _close_mode_enabled(config: AppConfig, preset_id: str, mode: str, lane: str) -> bool:
    if mode == HedgeMode.CONTRACTION.value:
        return config.auto_close_contraction_on_lane(preset_id, lane)
    return config.auto_close_expansion_on_lane(preset_id, lane)


def _close_mode_satisfied(
    config: AppConfig, preset_id: str, mode: str, spread: float, lane: str
) -> bool:
    if mode == HedgeMode.CONTRACTION.value:
        return spread <= config.auto_close_contraction_threshold_lane(preset_id, lane)
    return spread >= config.auto_close_expansion_threshold_lane(preset_id, lane)


def _close_mode_reset(
    config: AppConfig, preset_id: str, mode: str, spread: float, lane: str
) -> bool:
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
    best: AutoTradeProgress | None = None
    for preset_id in preset_ids:
        mode = detect_hedge_mode(preset_id, positions)
        if mode is None:
            continue
        snap = spreads.get(preset_id)
        if snap is None:
            continue
        hold_sec = config.auto_trade_hold_sec(preset_id)
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
        hold_sec = config.auto_trade_hold_sec(preset_id)

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

            started = state.close_since.get(key)
            if started is None:
                state.close_since[key] = now
                continue
            if now - started < hold_sec:
                continue

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
                        f"连续 {hold_sec:.0f}s 满足 · {mlabel}平仓{mode_text}"
                    ),
                )
            )
            state.last_close_fire[fire_key] = now
            state.close_since[key] = None
            break

    return orders
