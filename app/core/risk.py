"""风险快照汇总：计算各品种两端"距爆仓的资金缓冲"，供告警使用。

distance 单位为计价货币（USDT/USD），值越小越接近爆仓；无持仓返回 inf，
对外统一压成 99999（表示无风险）。
"""


from __future__ import annotations

from app.core.liquidation import resolve_position_liq_buffer
from app.core.models import AppConfig, Position, Quote, RiskSnapshot
from app.core.symbols import WATCHED_PRESETS, find_preset


def _platform_spread(quote: Quote) -> float:
    """平台内买卖价差（卖一 − 买一）。"""
    if quote.bid > 0 and quote.ask > 0:
        return quote.ask - quote.bid
    return 0.0


def _ba_liq_distance(
    positions: list[Position],
    quote: Quote,
    leverage: int,
    preset_id: str,
) -> float:
    """BA 端该品种持仓距爆仓的缓冲；无持仓返回 inf。"""
    preset = find_preset(preset_id)
    pos = next(
        (p for p in positions if p.platform == "BA" and p.symbol == preset.symbol_ba),
        None,
    )
    if not pos:
        return float("inf")
    return resolve_position_liq_buffer(pos, quote, preset_id, leverage)


def _mt5_liq_distance(
    positions: list[Position],
    quote: Quote,
    preset_id: str,
    leverage: int = 100,
) -> float:
    """MT5 端该品种持仓距爆仓的缓冲；优先用持仓自带杠杆。"""
    preset = find_preset(preset_id)
    pos = next(
        (p for p in positions if p.platform == "MT5" and p.symbol == preset.symbol_mt5),
        None,
    )
    if not pos:
        return float("inf")
    lev = pos.leverage if pos.leverage > 0 else leverage
    return resolve_position_liq_buffer(pos, quote, preset_id, lev)


def _finite_or_max(value: float) -> float:
    """把 inf 压成 99999（UI 友好的"无风险"占位值）。"""
    return value if value != float("inf") else 99999.0


def build_risk_snapshot(
    positions: list[Position],
    ba_quotes: dict[str, Quote],
    mt5_quotes: dict[str, Quote],
    config: AppConfig,
) -> RiskSnapshot:
    """遍历所有受关注品种，取每端最小缓冲（最危险持仓）组装风险快照。"""
    ba_spreads = []
    mt5_spreads = []
    xau_ba = xau_mt5 = xag_ba = xag_mt5 = float("inf")

    for preset_id in WATCHED_PRESETS:
        preset = find_preset(preset_id)
        ba_q = ba_quotes.get(preset.symbol_ba)
        mt5_q = mt5_quotes.get(preset.symbol_mt5)
        if ba_q:
            ba_spreads.append(_platform_spread(ba_q))
            dist = _ba_liq_distance(positions, ba_q, config.ba_leverage, preset_id)
            if preset_id == "xau":
                xau_ba = min(xau_ba, dist)
            else:
                xag_ba = min(xag_ba, dist)
        if mt5_q:
            mt5_spreads.append(_platform_spread(mt5_q))
            dist = _mt5_liq_distance(
                positions, mt5_q, preset_id, config.mt5_leverage
            )
            if preset_id == "xau":
                xau_mt5 = min(xau_mt5, dist)
            else:
                xag_mt5 = min(xag_mt5, dist)

    xau_ba_f = _finite_or_max(xau_ba)
    xau_mt5_f = _finite_or_max(xau_mt5)
    xag_ba_f = _finite_or_max(xag_ba)
    xag_mt5_f = _finite_or_max(xag_mt5)

    return RiskSnapshot(
        xau_ba_liq=xau_ba_f,
        xau_mt5_liq=xau_mt5_f,
        xag_ba_liq=xag_ba_f,
        xag_mt5_liq=xag_mt5_f,
        ba_liq_distance=min(xau_ba_f, xag_ba_f),
        mt5_liq_distance=min(xau_mt5_f, xag_mt5_f),
        ba_platform_spread=min(ba_spreads) if ba_spreads else 0.0,
        mt5_platform_spread=min(mt5_spreads) if mt5_spreads else 0.0,
    )
