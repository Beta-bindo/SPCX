"""日志级别定义与交易日志文案格式化。"""

from __future__ import annotations

from enum import IntEnum

from app.core.models import HedgeMode

LOG_LEVEL_QUIET = "quiet"      # 精简：仅交易与错误
LOG_LEVEL_NORMAL = "normal"    # 标准
LOG_LEVEL_VERBOSE = "verbose"  # 详细（含 DEBUG）
LOG_LEVEL_DEFAULT = LOG_LEVEL_NORMAL

LOG_LEVEL_OPTIONS: list[tuple[str, str]] = [
    (LOG_LEVEL_QUIET, "精简（仅交易与错误）"),
    (LOG_LEVEL_NORMAL, "标准"),
    (LOG_LEVEL_VERBOSE, "详细"),
]


class LogLevel(IntEnum):
    """消息重要性（值越小越重要），用于与配置级别比较决定是否输出。"""

    ERROR = 10
    TRADE = 20
    INFO = 30
    DEBUG = 40


def normalize_log_level(value: str | None) -> str:
    """规整日志级别字符串，非法值回退默认。"""
    if value in {LOG_LEVEL_QUIET, LOG_LEVEL_NORMAL, LOG_LEVEL_VERBOSE}:
        return value
    return LOG_LEVEL_DEFAULT


def should_log(config_level: str, msg_level: LogLevel) -> bool:
    """根据配置级别判断该消息是否应输出；ERROR 始终输出。"""
    if msg_level == LogLevel.ERROR:
        return True
    ceiling = {
        LOG_LEVEL_QUIET: LogLevel.TRADE,
        LOG_LEVEL_NORMAL: LogLevel.INFO,
        LOG_LEVEL_VERBOSE: LogLevel.DEBUG,
    }.get(normalize_log_level(config_level), LogLevel.INFO)
    return msg_level.value <= ceiling.value


def hedge_mode_word(mode: str) -> str:
    """对冲模式 → 收缩 / 扩张。"""
    return "收缩" if mode == HedgeMode.CONTRACTION.value else "扩张"


def hedge_action_label(action: str, mode: str, *, adding: bool = False) -> str:
    """生成动作标签，如"开仓收缩""加仓扩张""平仓收缩"。action 为 open | close。"""
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
    """拼装单腿成交成功的日志行（按提供的字段拼" · "分隔的明细）。"""
    parts = [
        f"【{platform}】{hedge_action_label(action, mode, adding=adding)}成功",
        f"订单 {order_id}",
    ]
    if qty:
        parts.append(f"数量 {qty}")
    if lots:
        parts.append(f"手数 {lots}")
    if price:
        parts.append(f"成交价 {price}")
    if order_type:
        parts.append(order_type)
    if spread_index is not None:
        parts.append(f"点差指数 {spread_index:+.3f}")
    return " · ".join(parts)


def emit_if_visible(signal, config_level: str, level: LogLevel, message: str) -> None:
    """仅当该消息级别在配置允许范围内时，通过信号发出。"""
    if should_log(config_level, level):
        signal.emit(message)
