from __future__ import annotations

from app.core.models import AppConfig, OpenOrder, Position
from app.core.symbols import find_preset
from app.core.trading_service import detect_hedge_mode


def _position_for(
    positions: list[Position], platform: str, symbol: str
) -> Position | None:
    for pos in positions:
        if pos.platform == platform and pos.symbol == symbol and pos.quantity > 0:
            return pos
    return None


def format_ba_account(config: AppConfig) -> str:
    key = (config.ba_api_key or "").strip()
    if not key:
        return ""
    if len(key) <= 8:
        return f"{key[:2]}***"
    return f"{key[:4]}...{key[-4:]}"


def format_mt5_account(config: AppConfig) -> str:
    login = int(config.mt5_login or 0)
    server = (config.mt5_server or "").strip()
    if login <= 0:
        return ""
    if server:
        return f"{login}@{server}"
    return str(login)


def build_preset_position(positions: list[Position], preset_id: str) -> str:
    preset = find_preset(preset_id)
    ba = _position_for(positions, "BA", preset.symbol_ba)
    mt5 = _position_for(positions, "MT5", preset.symbol_mt5)
    if not ba and not mt5:
        return "无仓"
    mode = detect_hedge_mode(preset_id, positions)
    mode_label = {"contraction": "收缩", "expansion": "扩张"}.get(mode or "", "持仓")
    ba_text = f"BA {ba.side.value} {ba.quantity:.4g}" if ba else "BA无"
    mt5_text = f"Ex {mt5.side.value} {mt5.quantity:.4g}" if mt5 else "Ex无"
    return f"{mode_label} {ba_text}/{mt5_text}"


def build_position_summary(positions: list[Position]) -> str:
    parts: list[str] = []
    for preset_id, label in (("xau", "黄金"), ("xag", "SPCXUSDT")):
        pos = build_preset_position(positions, preset_id)
        parts.append(f"{label}:{pos}")
    return " | ".join(parts)


def _format_platform_orders(orders: list[OpenOrder]) -> str:
    if not orders:
        return "无"
    total = sum(o.total_quantity for o in orders)
    filled = sum(o.filled_quantity for o in orders)
    remaining = sum(o.remaining_quantity for o in orders)
    return f"总量{total:.4g}/已成交{filled:.4g}/剩余{remaining:.4g}"


def _format_ba_orders(orders: list[OpenOrder]) -> str:
    if not orders:
        return "BA无"
    open_orders = [o for o in orders if not o.reduce_only]
    close_orders = [o for o in orders if o.reduce_only]
    parts: list[str] = []
    if open_orders:
        parts.append(f"开{_format_platform_orders(open_orders)}")
    if close_orders:
        parts.append(f"平{_format_platform_orders(close_orders)}")
    return " ".join(parts)


def build_preset_open_orders(orders: list[OpenOrder], preset_id: str) -> str:
    preset = find_preset(preset_id)
    ba_orders = [o for o in orders if o.platform == "BA" and o.symbol == preset.symbol_ba]
    mt5_orders = [
        o for o in orders if o.platform == "MT5" and o.symbol == preset.symbol_mt5
    ]
    if not ba_orders and not mt5_orders:
        return "无委托"
    ba_text = _format_ba_orders(ba_orders)
    mt5_text = _format_platform_orders(mt5_orders) if mt5_orders else "Ex无"
    if mt5_orders:
        mt5_text = f"Ex {mt5_text}"
    return f"{ba_text} / {mt5_text}"


def build_open_orders_summary(orders: list[OpenOrder]) -> str:
    parts: list[str] = []
    for preset_id, label in (("xau", "黄金"), ("xag", "SPCXUSDT")):
        detail = build_preset_open_orders(orders, preset_id)
        parts.append(f"{label}:{detail}")
    return " | ".join(parts)


def build_license_telemetry(
    config: AppConfig,
    positions: list[Position],
    open_orders: list[OpenOrder] | None = None,
) -> dict[str, str]:
    orders = open_orders or []
    spcx_position = build_preset_position(positions, "xag")
    spcx_open_orders = build_preset_open_orders(orders, "xag")
    return {
        "ba_account": format_ba_account(config),
        "mt5_account": format_mt5_account(config),
        "position_summary": build_position_summary(positions),
        "xau_position": build_preset_position(positions, "xau"),
        "spcx_position": spcx_position,
        "xag_position": spcx_position,
        "open_orders_summary": build_open_orders_summary(orders),
        "xau_open_orders": build_preset_open_orders(orders, "xau"),
        "spcx_open_orders": spcx_open_orders,
        "xag_open_orders": spcx_open_orders,
    }
