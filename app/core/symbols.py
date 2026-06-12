from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SymbolPreset:
    id: str
    label: str
    symbol_ba: str
    symbol_mt5: str
    demo_ba_base: float
    demo_mt5_base: float
    ba_qty_unit: float = 1.0
    mt5_oz_per_lot: float = 100.0


SYMBOL_PRESETS: list[SymbolPreset] = [
    SymbolPreset("xau", "黄金 XAU", "XAUUSDT", "XAUUSD", 2650.0, 2647.0, 1.0, 100.0),
    SymbolPreset("xag", "白银 XAG", "XAGUSDT", "XAGUSD", 67.0, 66.95, 1.0, 5000.0),
    SymbolPreset("custom", "自定义", "", "", 2650.0, 2647.0, 1.0, 100.0),
]

PRESET_BY_ID = {p.id: p for p in SYMBOL_PRESETS}

WATCHED_PRESETS = ["xau", "xag"]


def find_preset(preset_id: str) -> SymbolPreset:
    return PRESET_BY_ID.get(preset_id, SYMBOL_PRESETS[0])


def resolve_symbols(preset_id: str, symbol_ba: str, symbol_mt5: str) -> tuple[str, str, SymbolPreset]:
    preset = find_preset(preset_id)
    if preset_id == "custom":
        return symbol_ba or "XAUUSDT", symbol_mt5 or "XAUUSD", preset
    return preset.symbol_ba, preset.symbol_mt5, preset


def preset_for_ba_symbol(symbol_ba: str) -> str:
    for preset in SYMBOL_PRESETS:
        if preset.symbol_ba == symbol_ba:
            return preset.id
    return "custom"


def watched_ba_symbols() -> list[str]:
    return [find_preset(p).symbol_ba for p in WATCHED_PRESETS]


def watched_mt5_symbols() -> list[str]:
    return [find_preset(p).symbol_mt5 for p in WATCHED_PRESETS]
