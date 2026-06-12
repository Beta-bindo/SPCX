"""Detect one-sided or mismatched hedge exposure for UI alerts."""

from __future__ import annotations

from dataclasses import dataclass

from app.core.models import AppConfig, GoldOrderMode, HedgeMode, Position, Side
from app.core.symbols import find_preset
from app.core.trading_service import detect_hedge_mode


@dataclass(frozen=True)
class HedgeHealth:
    level: str  # ok | warn | alert
    code: str
    title: str
    detail: str
    action: str

    @property
    def is_ok(self) -> bool:
        return self.level == "ok"


@dataclass(frozen=True)
class HedgeRepair:
    """Suggested repair when hedge health is not ok."""

    mode: str | None
    order_mode: str | None = None
    tooltip: str = ""


def _side_label(side: Side) -> str:
    if side == Side.BUY:
        return "BUY"
    if side == Side.SELL:
        return "SELL"
    return "--"


def analyze_hedge_health(
    preset_id: str,
    positions: list[Position],
    config: AppConfig | None = None,
) -> HedgeHealth:
    preset = find_preset(preset_id)
    label = "黄金" if preset_id == "xau" else "白银"
    ba_pos = next(
        (p for p in positions if p.platform == "BA" and p.symbol == preset.symbol_ba and p.quantity > 0),
        None,
    )
    mt5_pos = next(
        (p for p in positions if p.platform == "MT5" and p.symbol == preset.symbol_mt5 and p.quantity > 0),
        None,
    )

    if not ba_pos and not mt5_pos:
        return HedgeHealth("ok", "none", f"{label}无持仓", "", "")

    if ba_pos and not mt5_pos:
        mode = HedgeMode.CONTRACTION if ba_pos.side == Side.SELL else HedgeMode.EXPANSION
        ex_side = Side.BUY if mode == HedgeMode.CONTRACTION else Side.SELL
        return HedgeHealth(
            "alert",
            "ba_only",
            f"{label}单边敞口 · 仅 BA",
            f"BA {_side_label(ba_pos.side)} {ba_pos.quantity:.4g} · Ex 无仓",
            f"请在 Ex 补 {_side_label(ex_side)} 对冲，或平掉 BA",
        )

    if mt5_pos and not ba_pos:
        mode = HedgeMode.CONTRACTION if mt5_pos.side == Side.BUY else HedgeMode.EXPANSION
        ba_side = Side.SELL if mode == HedgeMode.CONTRACTION else Side.BUY
        return HedgeHealth(
            "alert",
            "ex_only",
            f"{label}单边敞口 · 仅 Ex",
            f"Ex {_side_label(mt5_pos.side)} {mt5_pos.quantity:.2f} 手 · BA 无仓",
            f"请在 BA 补 {_side_label(ba_side)} 对冲，或平掉 Ex",
        )

    if ba_pos.side == Side.SELL and mt5_pos.side == Side.BUY:
        mode = HedgeMode.CONTRACTION
    elif ba_pos.side == Side.BUY and mt5_pos.side == Side.SELL:
        mode = HedgeMode.EXPANSION
    else:
        return HedgeHealth(
            "alert",
            "side_mismatch",
            f"{label}对冲方向异常",
            f"BA {_side_label(ba_pos.side)} · Ex {_side_label(mt5_pos.side)}",
            "请检查两边持仓并手动平衡",
        )

    mode_label = "收缩" if mode == HedgeMode.CONTRACTION else "扩张"
    if config is not None:
        expected_ba = config.ba_quantity_for(preset_id)
        expected_ex = config.mt5_lot_for(preset_id)
        ba_ratio = ba_pos.quantity / expected_ba if expected_ba > 0 else 1.0
        ex_ratio = mt5_pos.quantity / expected_ex if expected_ex > 0 else 1.0
        if ba_ratio < 0.85 or ex_ratio < 0.85 or abs(ba_ratio - ex_ratio) > 0.2:
            return HedgeHealth(
                "warn",
                "qty_skew",
                f"{label}{mode_label} · 对冲数量不齐",
                f"BA {ba_pos.quantity:.4g} / Ex {mt5_pos.quantity:.2f} 手",
                "请核对两边手数是否匹配",
            )

    return HedgeHealth(
        "ok",
        "hedged",
        f"{label}{mode_label}对冲正常",
        f"BA {ba_pos.quantity:.4g} · Ex {mt5_pos.quantity:.2f} 手",
        "",
    )


def combine_hedge_health(*items: HedgeHealth) -> HedgeHealth:
    if not items:
        return HedgeHealth("ok", "none", "对冲正常", "", "")
    order = {"alert": 3, "warn": 2, "ok": 1}
    alerts = [h for h in items if not h.is_ok]
    if alerts:
        return max(alerts, key=lambda h: order.get(h.level, 0))
    hedged = next((h for h in items if h.code == "hedged"), None)
    if hedged:
        return hedged
    return HedgeHealth("ok", "none", "对冲正常", "", "")


def format_position_status(health: HedgeHealth) -> str:
    if health.level == "alert":
        return f"⚠ {health.title} · {health.detail} · {health.action}"
    if health.level == "warn":
        return f"⚠ {health.title} · {health.detail} · {health.action}"
    if health.code == "none":
        return "当前持仓：无"
    return f"当前持仓：{health.title} · {health.detail}"


def format_hedge_banner(health: HedgeHealth) -> str:
    if health.is_ok:
        return ""
    parts = [health.title, health.detail]
    if health.action:
        parts.append(health.action)
    return "⚠ " + " · ".join(p for p in parts if p)


def suggest_hedge_repair(
    preset_id: str,
    positions: list[Position],
    health: HedgeHealth,
) -> HedgeRepair | None:
    """Return trade-dialog prefill when user can attempt to rebalance."""
    if health.is_ok:
        return None
    if health.code in ("ba_only", "ex_only"):
        mode = detect_hedge_mode(preset_id, positions)
        if not mode:
            return None
        return HedgeRepair(
            mode=mode,
            order_mode=GoldOrderMode.MARKET.value,
            tooltip=health.action,
        )
    if health.code == "qty_skew":
        mode = detect_hedge_mode(preset_id, positions)
        if not mode:
            return None
        return HedgeRepair(
            mode=mode,
            order_mode=None,
            tooltip="打开交易窗口核对手数并补仓",
        )
    if health.code == "side_mismatch":
        return HedgeRepair(
            mode=None,
            order_mode=None,
            tooltip="打开交易窗口手动平衡两边持仓",
        )
    return None
