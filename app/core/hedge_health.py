"""对冲健康度检测：识别单边敞口、方向错配、两边手数不齐等异常，用于 UI 告警与修复建议。"""

from __future__ import annotations

from dataclasses import dataclass

from app.core.models import AppConfig, GoldOrderMode, HedgeMode, Position, Side
from app.core.symbols import find_preset, preset_display_name
from app.core.trading_service import detect_hedge_mode


@dataclass(frozen=True)
class HedgeHealth:
    """一次对冲健康度评估结果。"""

    level: str   # 严重程度：ok（正常）| warn（提示）| alert（告警）
    code: str    # 机器可读的状态码：none/ba_only/ex_only/side_mismatch/qty_skew/hedged
    title: str   # 标题（含品种与策略）
    detail: str  # 两边持仓明细
    action: str  # 建议的处理动作（仅异常时非空）

    @property
    def is_ok(self) -> bool:
        return self.level == "ok"


@dataclass(frozen=True)
class HedgeRepair:
    """当对冲不健康时，给交易弹窗的预填修复建议。"""

    mode: str | None              # 建议的对冲模式（收缩/扩张），None 表示需人工判断
    order_mode: str | None = None  # 建议的下单方式（如市价补腿）
    tooltip: str = ""             # 鼠标悬浮提示


def _qty_map_ratio(config: AppConfig, preset_id: str) -> float:
    """配置中 BA 数量与 Ex 手数的固定配比（与单次开仓手数无关）。"""
    if preset_id == "xag":
        return config.xag_ba_qty_map / max(config.xag_mt5_lot_map, 0.001)
    return config.xau_ba_qty_map / max(config.xau_mt5_lot_map, 0.001)


def _side_label(side: Side) -> str:
    """方向枚举 → 显示文本。"""
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
    """评估单个品种的对冲健康度。

    判定顺序：无持仓 → 单边敞口（仅 BA / 仅 Ex，告警）→ 方向错配（告警）
    → 数量不齐（warn，需传入 config 才检测）→ 正常对冲。
    """
    preset = find_preset(preset_id)
    label = preset_display_name(preset_id)
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
        # 按 BA:Ex 配比核对（如 100:0.01 手），而非与「当前开仓手数」单次预期比较。
        # 否则开仓手数设为 2 而实际持仓为 2 次 0.01 手叠加时，会误报数量不齐。
        map_ratio = _qty_map_ratio(config, preset_id)
        actual_ratio = (
            ba_pos.quantity / mt5_pos.quantity if mt5_pos.quantity > 0 else 0.0
        )
        if map_ratio <= 0 or actual_ratio <= 0:
            qty_skew = True
        else:
            rel_err = abs(actual_ratio - map_ratio) / map_ratio
            qty_skew = rel_err > 0.15
        if qty_skew:
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
    """合并多个品种的健康度：优先返回最严重的异常，否则返回正常对冲项。"""
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
    """健康度 → 持仓状态行文本。"""
    if health.level == "alert":
        return f"⚠ {health.title} · {health.detail} · {health.action}"
    if health.level == "warn":
        return f"⚠ {health.title} · {health.detail} · {health.action}"
    if health.code == "none":
        return "当前持仓：无"
    return f"当前持仓：{health.title} · {health.detail}"


def format_hedge_banner(health: HedgeHealth) -> str:
    """健康度 → 顶部横幅文本；正常时返回空串（不显示横幅）。"""
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
    """根据健康度给出交易弹窗的预填修复方案；正常时返回 None。

    - 单边敞口：建议按现有方向市价补另一腿；
    - 数量不齐：建议打开弹窗核对手数补仓；
    - 方向错配：建议人工平衡（不预填模式）。
    """
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
