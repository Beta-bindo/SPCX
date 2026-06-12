"""Application log levels and trade log formatting."""

from __future__ import annotations

from enum import IntEnum

from app.core.models import HedgeMode

LOG_LEVEL_QUIET = "quiet"
LOG_LEVEL_NORMAL = "normal"
LOG_LEVEL_VERBOSE = "verbose"
LOG_LEVEL_DEFAULT = LOG_LEVEL_NORMAL

LOG_LEVEL_OPTIONS: list[tuple[str, str]] = [
    (LOG_LEVEL_QUIET, "精简（仅交易与错误）"),
    (LOG_LEVEL_NORMAL, "标准"),
    (LOG_LEVEL_VERBOSE, "详细"),
]


class LogLevel(IntEnum):
    ERROR = 10
    TRADE = 20
    INFO = 30
    DEBUG = 40


def normalize_log_level(value: str | None) -> str:
    if value in {LOG_LEVEL_QUIET, LOG_LEVEL_NORMAL, LOG_LEVEL_VERBOSE}:
        return value
    return LOG_LEVEL_DEFAULT


def should_log(config_level: str, msg_level: LogLevel) -> bool:
    if msg_level == LogLevel.ERROR:
        return True
    ceiling = {
        LOG_LEVEL_QUIET: LogLevel.TRADE,
        LOG_LEVEL_NORMAL: LogLevel.INFO,
        LOG_LEVEL_VERBOSE: LogLevel.DEBUG,
    }.get(normalize_log_level(config_level), LogLevel.INFO)
    return msg_level.value <= ceiling.value


def hedge_mode_word(mode: str) -> str:
    return "收缩" if mode == HedgeMode.CONTRACTION.value else "扩张"


def hedge_action_label(action: str, mode: str, *, adding: bool = False) -> str:
    """action: open | close"""
    mode_word = hedge_mode_word(mode)
    if action == "close":
        return f"平仓{mode_word}"
    return f"{'加仓' if adding else '开仓'}{mode_word}"


def trade_leg_success_msg(
    platform: str,
    action: str,
    mode: str,
    order_id: str,
    *,
    adding: bool = False,
    qty: str = "",
    lots: str = "",
    price: str = "",
    order_type: str = "",
    spread_index: float | None = None,
) -> str:
    parts = [
        f"【{platform}】{hedge_action_label(action, mode, adding=adding)}成功",
        f"订单 {order_id}",
    ]
    if qty:
        parts.append(f"数量 {qty}")
    if lots:
        parts.append(f"手数 {lots}")
    if price:
        parts.append(f"价 {price}")
    if order_type:
        parts.append(order_type)
    if spread_index is not None:
        parts.append(f"点差指数 {spread_index:+.3f}")
    return " · ".join(parts)


def emit_if_visible(signal, config_level: str, level: LogLevel, message: str) -> None:
    if should_log(config_level, level):
        signal.emit(message)
