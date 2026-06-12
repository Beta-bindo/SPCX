"""交易品种预设：黄金/白银（及自定义）的两端交易对与换算单位。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SymbolPreset:
    """单个品种预设：两端交易对符号、模拟基准价与数量换算单位。"""

    id: str               # 预设标识：xau / xag / custom
    label: str            # 显示名
    symbol_ba: str        # BA（币安）交易对
    symbol_mt5: str       # MT5/Exness 交易对
    demo_ba_base: float   # 模拟行情 BA 基准价
    demo_mt5_base: float  # 模拟行情 MT5 基准价
    ba_qty_unit: float = 1.0       # BA 数量 → 盎司的换算系数
    mt5_oz_per_lot: float = 100.0  # MT5 每手对应盎司数（黄金 100 / 白银 5000）


SYMBOL_PRESETS: list[SymbolPreset] = [
    SymbolPreset("xau", "黄金 XAU", "XAUUSDT", "XAUUSD", 2650.0, 2647.0, 1.0, 100.0),
    SymbolPreset("xag", "白银 XAG", "XAGUSDT", "XAGUSD", 67.0, 66.95, 1.0, 5000.0),
    SymbolPreset("custom", "自定义", "", "", 2650.0, 2647.0, 1.0, 100.0),
]

PRESET_BY_ID = {p.id: p for p in SYMBOL_PRESETS}

WATCHED_PRESETS = ["xau", "xag"]  # 实际监控的品种（不含 custom）


def find_preset(preset_id: str) -> SymbolPreset:
    """按 id 取预设，未知 id 回退到第一个（黄金）。"""
    return PRESET_BY_ID.get(preset_id, SYMBOL_PRESETS[0])


def resolve_symbols(preset_id: str, symbol_ba: str, symbol_mt5: str) -> tuple[str, str, SymbolPreset]:
    """解析最终使用的两端交易对：custom 用传入值（带兜底），否则用预设值。"""
    preset = find_preset(preset_id)
    if preset_id == "custom":
        return symbol_ba or "XAUUSDT", symbol_mt5 or "XAUUSD", preset
    return preset.symbol_ba, preset.symbol_mt5, preset


def preset_for_ba_symbol(symbol_ba: str) -> str:
    """由 BA 交易对反查所属预设 id，找不到归为 custom。"""
    for preset in SYMBOL_PRESETS:
        if preset.symbol_ba == symbol_ba:
            return preset.id
    return "custom"


def watched_ba_symbols() -> list[str]:
    """所有受监控品种的 BA 交易对列表。"""
    return [find_preset(p).symbol_ba for p in WATCHED_PRESETS]


def watched_mt5_symbols() -> list[str]:
    """所有受监控品种的 MT5 交易对列表。"""
    return [find_preset(p).symbol_mt5 for p in WATCHED_PRESETS]
