"""Paired demo quotes so BA / MT5 mids stay aligned and spread stays realistic."""

from __future__ import annotations

import math
import random
import time

from app.core.models import Quote
from app.core.symbols import WATCHED_PRESETS, find_preset

# Max |mid_spread| we accept when rebuilding; beyond this is almost always stale/mixed quotes.
SANITY_MAX_SPREAD = {"xau": 25.0, "xag": 8.0}


def demo_tick_time(t: float, interval_sec: float) -> float:
    """Quantize time so both connectors generate the same tick."""
    step = max(0.1, float(interval_sec))
    return math.floor(t / step) * step


def target_demo_spread(preset_id: str, t: float) -> float:
    """Oscillating spread index; gold ~1.2–2.8, silver ~0–0.12."""
    if preset_id == "xau":
        return 2.0 + math.sin(t / 14.0) * 0.55 + random.uniform(-0.12, 0.12)
    return 0.05 + math.sin(t / 11.0) * 0.025 + random.uniform(-0.008, 0.008)


def generate_demo_pair(preset_id: str, t: float) -> tuple[Quote, Quote]:
    """Return (ba_quote, mt5_quote) with mid_spread ≈ target_demo_spread."""
    preset = find_preset(preset_id)
    i = 0 if preset_id == "xau" else 1
    spread = target_demo_spread(preset_id, t)
    drift = math.sin(t / 8.0 + i) * (0.8 if preset_id == "xau" else 0.025)
    ba_mid = preset.demo_ba_base + drift + random.uniform(-0.08, 0.08)
    mt5_mid = ba_mid - spread
    if preset_id == "xau":
        ba_half = random.uniform(0.12, 0.35)
        mt5_half = random.uniform(0.10, 0.30)
    else:
        ba_half = random.uniform(0.004, 0.015)
        mt5_half = random.uniform(0.004, 0.012)
    ba = Quote(
        symbol=preset.symbol_ba,
        bid=ba_mid - ba_half,
        ask=ba_mid + ba_half,
        timestamp=t,
        is_simulated=True,
    )
    mt5 = Quote(
        symbol=preset.symbol_mt5,
        bid=mt5_mid - mt5_half,
        ask=mt5_mid + mt5_half,
        timestamp=t,
        is_simulated=True,
    )
    return ba, mt5


def generate_all_demo_pairs(t: float) -> dict[str, tuple[Quote, Quote]]:
    return {preset_id: generate_demo_pair(preset_id, t) for preset_id in WATCHED_PRESETS}


def spread_is_sane(preset_id: str, mid_spread: float) -> bool:
    limit = SANITY_MAX_SPREAD.get(preset_id, 50.0)
    return abs(mid_spread) <= limit


def align_sim_mt5_to_ba(ba: Quote, mt5: Quote, preset_id: str, *, interval_sec: float = 0.8) -> Quote:
    """混合模式：实盘 BA + 模拟 MT5 时，将 MT5 演示价锚定到 BA 现价，避免基数偏差。"""
    if ba.is_simulated or not mt5.is_simulated:
        return mt5
    if ba.bid <= 0 or ba.ask <= 0:
        return mt5
    t = demo_tick_time(time.time(), interval_sec)
    spread = target_demo_spread(preset_id, t)
    ba_mid = (ba.bid + ba.ask) / 2
    mt5_mid = ba_mid - spread
    if mt5.ask > mt5.bid > 0:
        mt5_half = (mt5.ask - mt5.bid) / 2
    elif preset_id == "xau":
        mt5_half = 0.20
    else:
        mt5_half = 0.008
    return Quote(
        symbol=mt5.symbol,
        bid=mt5_mid - mt5_half,
        ask=mt5_mid + mt5_half,
        timestamp=t,
        is_simulated=True,
    )
