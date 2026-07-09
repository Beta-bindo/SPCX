"""交易品种预设：两个监控槽位（及自定义）的两端交易对与换算单位。"""

from __future__ import annotations

from dataclasses import dataclass, replace


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
    mt5_oz_per_lot: float = 1.0


SYMBOL_PRESETS: list[SymbolPreset] = [
    SymbolPreset("xau", "XAUUSDT", "XAUUSDT", "XAUUSD", 2650.0, 2647.0, 1.0, 100.0),
    SymbolPreset("xag", "SPCXUSDT", "SPCXUSDT", "SPCXUSDT", 1.0, 1.0, 1.0, 1.0),
    SymbolPreset("custom", "自定义", "", "", 2650.0, 2647.0, 1.0, 100.0),
]

PRESET_BY_ID = {p.id: p for p in SYMBOL_PRESETS}

WATCHED_PRESETS = ["xau", "xag"]  # 实际监控的品种（不含 custom）
_DYNAMIC_PRESETS: dict[str, SymbolPreset] = {}

MT5_SYMBOL_BY_BA_SYMBOL: dict[str, str] = {
    "ETHUSDT": "ETH",
}


def mt5_symbol_for_ba_symbol(symbol_ba: str) -> str:
    """BA U 本位交易对映射到 EX/MT5 品种名；未配置则默认同名。"""
    symbol = (symbol_ba or "").strip().upper()
    return MT5_SYMBOL_BY_BA_SYMBOL.get(symbol, symbol)


def normalize_selected_symbols(raw: str | None) -> list[str]:
    """解析配置中的监控品种，最多保留两个大写交易对。"""
    result: list[str] = []
    for chunk in (raw or "").replace(";", ",").split(","):
        symbol = chunk.strip().upper()
        if symbol and symbol not in result:
            result.append(symbol)
        if len(result) >= len(WATCHED_PRESETS):
            break
    return result or ["SPCXUSDT"]


def selected_symbols_text(symbols: list[str]) -> str:
    return ",".join(normalize_selected_symbols(",".join(symbols)))


def apply_selected_symbols(raw: str | None) -> None:
    """把两个监控槽位映射到用户选择的交易对。"""
    selected = normalize_selected_symbols(raw)
    base_by_id = {p.id: p for p in SYMBOL_PRESETS}
    _DYNAMIC_PRESETS.clear()
    for preset_id, symbol in zip(WATCHED_PRESETS, selected):
        base = base_by_id[preset_id]
        label = symbol
        mt5_symbol = "XAUUSD" if symbol == "XAUUSDT" else mt5_symbol_for_ba_symbol(symbol)
        demo_base = base.demo_ba_base if symbol == base.symbol_ba else 1.0
        if symbol == "XAUUSDT":
            _DYNAMIC_PRESETS[preset_id] = base
        else:
            _DYNAMIC_PRESETS[preset_id] = replace(
                base,
                label=label,
                symbol_ba=symbol,
                symbol_mt5=mt5_symbol,
                demo_ba_base=demo_base,
                demo_mt5_base=demo_base,
                mt5_oz_per_lot=1.0,
            )


def find_preset(preset_id: str) -> SymbolPreset:
    """按 id 取预设，未知 id 回退到第一个（黄金）。"""
    if preset_id in _DYNAMIC_PRESETS:
        return _DYNAMIC_PRESETS[preset_id]
    return PRESET_BY_ID.get(preset_id, SYMBOL_PRESETS[0])


def active_preset_ids() -> list[str]:
    """当前用户实际选择并启用的监控槽位。"""
    if not _DYNAMIC_PRESETS:
        return list(WATCHED_PRESETS)
    return [preset_id for preset_id in WATCHED_PRESETS if preset_id in _DYNAMIC_PRESETS]


def preset_display_name(preset_id: str) -> str:
    preset = find_preset(preset_id)
    return preset.symbol_ba or preset.label or preset_id.upper()


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
    return [find_preset(p).symbol_ba for p in active_preset_ids()]


def watched_mt5_symbols() -> list[str]:
    """所有受监控品种的 MT5 交易对列表。"""
    return [find_preset(p).symbol_mt5 for p in active_preset_ids()]
