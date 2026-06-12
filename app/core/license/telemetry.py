from __future__ import annotations

from app.core.models import AppConfig, Position
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
    for preset_id, label in (("xau", "黄金"), ("xag", "白银")):
        pos = build_preset_position(positions, preset_id)
        parts.append(f"{label}:{pos}")
    return " | ".join(parts)


def build_license_telemetry(
    config: AppConfig, positions: list[Position]
) -> dict[str, str]:
    return {
        "ba_account": format_ba_account(config),
        "mt5_account": format_mt5_account(config),
        "position_summary": build_position_summary(positions),
        "xau_position": build_preset_position(positions, "xau"),
        "xag_position": build_preset_position(positions, "xag"),
    }
