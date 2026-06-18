"""全局数据模型与枚举。

集中定义贯穿各层的数据结构：报价 / 持仓 / 点差快照 / 风险快照，以及最重要的
应用配置 :class:`AppConfig`。所有结构均为纯数据（dataclass / Enum），不含业务逻辑，
以便在 core、connectors、widgets 之间安全传递。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class ConnectionMode(str, Enum):
    """连接模式：纯模拟 / 双实盘 / 仅 BA 实盘 / 仅 MT5 实盘。"""

    DEMO = "demo"
    LIVE_BOTH = "live_both"
    LIVE_BA = "live_ba"
    LIVE_MT5 = "live_mt5"


class ConnectionState(str, Enum):
    """单个连接器的运行状态。"""

    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    ERROR = "error"
    SIMULATED = "simulated"


class Side(str, Enum):
    """买卖方向；NONE 表示无方向 / 无持仓。"""

    BUY = "BUY"
    SELL = "SELL"
    NONE = "NONE"


class HedgeMode(str, Enum):
    """收缩 = BA 空 + Exness 多；扩张 = BA 多 + Exness 空."""

    CONTRACTION = "contraction"
    EXPANSION = "expansion"


class GoldOrderMode(str, Enum):
    """黄金 BA/Exness 下单模式."""

    LIMIT = "limit"
    MAKER = "maker"
    MARKET = "market"


class LayoutMode(str, Enum):
    """双品种并排 / 单品种切换."""

    DUAL = "dual"
    SINGLE = "single"


# 中间栏可配置板块（顺序即默认显示顺序）。对冲交易按钮区固定常驻，不参与此配置。
PANEL_SECTION_KEYS: tuple[str, ...] = ("spread", "alert", "auto", "position")
PANEL_SECTION_LABELS: dict[str, str] = {
    "spread": "跨平台点差",
    "alert": "告警设置",
    "auto": "自动交易",
    "position": "当前持仓 / 盈利",
}
DEFAULT_PANEL_SECTIONS: str = "spread:1:10:18,alert:1:10:18,auto:1:10:18,position:1:10:18"

# (板块 key, 是否显示, 字体 pt, 勾选框 px)
PanelSectionEntry = tuple[str, bool, int, int]


def parse_panel_sections(
    raw: str | None,
    *,
    default_font_pt: int = 10,
    default_check_px: int = 18,
) -> list[PanelSectionEntry]:
    """解析板块配置。支持旧格式 key:visible 与新格式 key:visible:font_pt:check_px。"""
    from app.widgets.panel_ui_scale import clamp_check_px, clamp_font_pt

    default_font_pt = clamp_font_pt(default_font_pt)
    default_check_px = clamp_check_px(default_check_px)
    result: list[PanelSectionEntry] = []
    seen: set[str] = set()
    for chunk in (raw or "").split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        parts = [p.strip() for p in chunk.split(":")]
        key = parts[0]
        if key not in PANEL_SECTION_KEYS or key in seen:
            continue
        visible = True
        if len(parts) > 1:
            visible = parts[1] not in ("0", "false", "False", "no")
        font_pt = default_font_pt
        check_px = default_check_px
        if len(parts) > 2:
            try:
                font_pt = clamp_font_pt(int(parts[2]))
            except ValueError:
                pass
        if len(parts) > 3:
            try:
                check_px = clamp_check_px(int(parts[3]))
            except ValueError:
                pass
        result.append((key, visible, font_pt, check_px))
        seen.add(key)
    for key in PANEL_SECTION_KEYS:
        if key not in seen:
            entry: PanelSectionEntry = (key, True, default_font_pt, default_check_px)
            if key == "spread":
                result.insert(0, entry)
            else:
                result.append(entry)
            seen.add(key)
    return result


def serialize_panel_sections(sections: list[PanelSectionEntry] | list[tuple]) -> str:
    parts: list[str] = []
    seen: set[str] = set()
    from app.widgets.panel_ui_scale import clamp_check_px, clamp_font_pt

    for item in sections:
        if len(item) >= 4:
            key, visible, font_pt, check_px = item[0], item[1], item[2], item[3]
        elif len(item) == 2:
            key, visible = item[0], item[1]
            font_pt, check_px = 10, 18
        else:
            continue
        if key not in PANEL_SECTION_KEYS or key in seen:
            continue
        font_pt = clamp_font_pt(int(font_pt))
        check_px = clamp_check_px(int(check_px))
        parts.append(f"{key}:{1 if visible else 0}:{font_pt}:{check_px}")
        seen.add(key)
    for key in PANEL_SECTION_KEYS:
        if key not in seen:
            parts.append(f"{key}:1:10:18")
    return ",".join(parts)


BA_REFRESH_INTERVAL_OPTIONS: tuple[float, ...] = (
    0.3,
    0.4,
    0.5,
    0.6,
    0.7,
    0.8,
    0.9,
    1.0,
)
BA_REFRESH_INTERVAL_DEFAULT = 0.8


def normalize_ba_refresh_interval(value: float | int | str | None) -> float:
    try:
        candidate = float(value)
    except (TypeError, ValueError):
        return BA_REFRESH_INTERVAL_DEFAULT
    for option in BA_REFRESH_INTERVAL_OPTIONS:
        if abs(candidate - option) < 1e-6:
            return option
    return BA_REFRESH_INTERVAL_DEFAULT


@dataclass
class Quote:
    """单一交易对的实时报价（买一 / 卖一）。"""

    symbol: str
    bid: float = 0.0
    ask: float = 0.0
    timestamp: float = 0.0
    is_simulated: bool = False  # 是否为模拟行情（非实盘真实报价）

    @property
    def mid(self) -> float:
        """中间价；缺一边时退回另一边。"""
        if self.bid > 0 and self.ask > 0:
            return (self.bid + self.ask) / 2
        return self.bid or self.ask


@dataclass
class AccountSnapshot:
    """单个平台的账户资金快照（余额 / 已用保证金 / 可用保证金）。

    - BA（币安合约）：balance=钱包余额、used_margin=已用保证金、
      free_margin=可用余额(availableBalance)、equity=保证金余额；currency 通常为 USDT。
    - EX（MT5/Exness）：balance=结余、used_margin=已用预付款、
      free_margin=可用预付款、equity=净值；currency 为账户币种（多为 USD）。
    """

    platform: str  # "BA" / "MT5"
    balance: float = 0.0        # 合约钱包余额（BA totalWalletBalance / MT5 结余）
    used_margin: float = 0.0    # 已用保证金
    free_margin: float = 0.0    # 可用保证金（BA availableBalance / MT5 margin_free）
    equity: float = 0.0         # 保证金余额 / 净值（BA totalMarginBalance / MT5 equity）
    cash_balance: float = 0.0   # 现金钱包余额（BA 现货 USDT；MT5 无此概念，为 0）
    currency: str = ""
    is_live: bool = False  # 是否为真实账户数据（模拟/未连接为 False）
    timestamp: float = 0.0


@dataclass
class OrderBookLevel:
    """盘口单档：价格 + 挂单量。"""

    price: float
    quantity: float


@dataclass
class OrderBook:
    """盘口快照（买卖各若干档）。"""

    bids: list[OrderBookLevel] = field(default_factory=list)
    asks: list[OrderBookLevel] = field(default_factory=list)
    is_simulated: bool = False


@dataclass
class Position:
    """单平台单交易对的持仓快照。"""

    platform: str               # "BA" 或 "MT5"
    symbol: str
    side: Side = Side.NONE
    quantity: float = 0.0       # BA 为合约/币数，MT5 为手数
    entry_price: float = 0.0
    current_price: float = 0.0
    unrealized_pnl: float = 0.0
    estimated_fee: float = 0.0  # 预估平仓手续费
    liquidation_price: float = 0.0
    mark_price: float = 0.0
    leverage: int = 0
    margin_type: str = ""
    exchange_liq_buffer: float | None = None  # 交易所返回的爆仓缓冲（如有）


@dataclass
class OpenOrder:
    """单平台未完全成交的委托单快照。"""

    platform: str               # "BA" 或 "MT5"
    symbol: str
    order_id: str = ""
    side: Side = Side.NONE
    order_type: str = ""
    total_quantity: float = 0.0       # 委托总量
    filled_quantity: float = 0.0      # 已成交量
    remaining_quantity: float = 0.0   # 剩余量
    price: float = 0.0
    reduce_only: bool = False         # BA：True 表示平仓委托（reduceOnly）


@dataclass
class SpreadSnapshot:
    """两端报价构成的点差快照（详见 pnl_calculator.build_spread_snapshot）。"""

    preset_id: str = "xau"
    ba_bid: float = 0.0
    ba_ask: float = 0.0
    mt5_bid: float = 0.0
    mt5_ask: float = 0.0
    ba_mid: float = 0.0
    mt5_mid: float = 0.0
    mid_spread: float = 0.0
    exec_spread: float = 0.0
    ba_platform_spread: float = 0.0
    mt5_platform_spread: float = 0.0
    is_simulated: bool = True
    timestamp: float = 0.0

    @property
    def spread(self) -> float:
        """币安 − Exness（买价差）."""
        return self.mid_spread

    def executable_spread(self, action: str, mode: str) -> float:
        """该场景实际可成交的点差（按两腿真实吃 bid/ask 的方向计算）。

        各场景两腿成交方向与对应公式：
        - 收缩开仓 / 扩张平仓：BA 卖@bid + Ex 买@ask → ba_bid − mt5_ask
        - 扩张开仓 / 收缩平仓：BA 买@ask + Ex 卖@bid → ba_ask − mt5_bid

        action 为 "open"/"close"，mode 为 HedgeMode 值（"contraction"/"expansion"）。
        缺完整两端买卖价时（如测试/降级场景）回退到点差指数 mid_spread。
        """
        if self.ba_bid <= 0 or self.ba_ask <= 0 or self.mt5_bid <= 0 or self.mt5_ask <= 0:
            return self.mid_spread
        contraction = mode == "contraction"
        opening = action == "open"
        # 收缩开仓 与 扩张平仓 同侧；扩张开仓 与 收缩平仓 同侧
        if contraction == opening:
            return self.ba_bid - self.mt5_ask
        return self.ba_ask - self.mt5_bid


@dataclass
class RiskSnapshot:
    """风险快照：各品种两端的爆仓价与到爆仓的距离（默认 99999 表示无持仓/无风险）。"""

    xau_ba_liq: float = 99999.0
    xau_mt5_liq: float = 99999.0
    xag_ba_liq: float = 99999.0
    xag_mt5_liq: float = 99999.0
    ba_liq_distance: float = 99999.0
    mt5_liq_distance: float = 99999.0
    ba_platform_spread: float = 0.0
    mt5_platform_spread: float = 0.0


@dataclass
class MarketUpdate:
    """一次行情刷新打包：两端报价 + 各品种点差 + 风险快照，供 UI 一次性更新。"""

    ba_quotes: dict = field(default_factory=dict)
    mt5_quotes: dict = field(default_factory=dict)
    spreads: dict = field(default_factory=dict)
    risk: RiskSnapshot = field(default_factory=RiskSnapshot)


@dataclass
class AppConfig:
    """应用全局配置（持久化到 config.json）。

    字段按用途分组：连接凭据 / 代理、交易品种与手数、手续费参数、主题与刷新、
    点差与爆仓告警、自动交易阈值（限价与市价两条 lane）、布局等。
    带 xau_/xag_ 前缀的字段为黄金/SPCXUSDT各自独立配置，通过下方 *_for(preset_id)
    辅助方法按当前品种取值。
    """

    # —— 连接凭据与代理 ——
    ba_api_key: str = ""
    ba_api_secret: str = ""
    proxy_host: str = "127.0.0.1"
    proxy_port: int = 7897
    use_proxy: bool = False
    mt5_login: int = 0
    mt5_password: str = ""
    mt5_server: str = ""
    mt5_terminal_path: str = ""
    connection_mode: str = ConnectionMode.DEMO.value
    # —— 交易品种与手数映射 ——
    symbol_preset: str = "xau"
    symbol_ba: str = "XAUUSDT"
    symbol_mt5: str = "XAUUSD"
    mt5_lot_size: float = 0.01
    ba_quantity: float = 0.01
    xau_ba_quantity: float = 500.0
    xau_mt5_lot_size: float = 1.0
    xag_ba_quantity: float = 1.0
    xag_mt5_lot_size: float = 1.0
    xau_ba_qty_map: float = 500.0
    xau_mt5_lot_map: float = 1.0
    xau_trade_lots: float = 1.0
    xag_ba_qty_map: float = 1.0
    xag_mt5_lot_map: float = 1.0
    xag_trade_lots: float = 1.0
    # —— 手续费 / 成本参数 ——
    ba_fee_rate: float = 0.0004
    mt5_commission_per_lot: float = 0.0
    mt5_spread_points: float = 0.25
    mt5_point_value: float = 1.0
    # —— 外观、杠杆与刷新 ——
    theme: str = "light"
    ba_leverage: int = 20
    mt5_leverage: int = 100
    sync_leverage_on_trade: bool = False
    # BA 保证金模式："" 跟随平台不设置 / "cross" 全仓 / "isolated" 逐仓
    ba_margin_type: str = ""
    ba_refresh_interval_sec: float = BA_REFRESH_INTERVAL_DEFAULT
    ba_maker_timeout_sec: float = 5.0   # Maker 委托等待成交超时（秒），超时撤单
    # 网络延迟超过该毫秒数时，自动取消所有已勾选的自动下单（0=不启用该保护）
    auto_trade_max_latency_ms: float = 200.0
    # —— 点差 / 爆仓告警阈值与开关 ——
    xau_spread_alert_min: float = 1.0
    xau_spread_alert_max: float = 3.0
    xag_spread_alert_min: float = -2.0
    xag_spread_alert_max: float = 1.0
    xau_ba_liq_alert: float = 100.0
    xau_mt5_liq_alert: float = 100.0
    xag_ba_liq_alert: float = 50.0
    xag_mt5_liq_alert: float = 50.0
    xau_alert_sound_enabled: bool = True
    xag_alert_sound_enabled: bool = True
    xau_spread_sound_enabled: bool = True
    xau_liq_sound_enabled: bool = True
    xag_spread_sound_enabled: bool = True
    xag_liq_sound_enabled: bool = True
    xau_spread_alert_enabled: bool = True
    xag_spread_alert_enabled: bool = True
    xau_liq_alert_enabled: bool = True
    xag_liq_alert_enabled: bool = True
    # —— 自动交易：限价/Maker lane 的开平仓阈值与开关 ——
    xau_auto_contraction_enabled: bool = False
    xau_auto_expansion_enabled: bool = False
    xag_auto_contraction_enabled: bool = False
    xag_auto_expansion_enabled: bool = False
    xau_auto_contraction_threshold: float = 3.0
    xau_auto_expansion_threshold: float = -3.0
    xag_auto_contraction_threshold: float = 3.0
    xag_auto_expansion_threshold: float = -3.0
    xau_auto_trade_hold_sec: float = 3.0
    xag_auto_trade_hold_sec: float = 3.0
    xau_auto_close_contraction_enabled: bool = False
    xau_auto_close_expansion_enabled: bool = False
    xag_auto_close_contraction_enabled: bool = False
    xag_auto_close_expansion_enabled: bool = False
    xau_auto_close_contraction_threshold: float = 0.5
    xau_auto_close_expansion_threshold: float = -0.5
    xag_auto_close_contraction_threshold: float = 0.5
    xag_auto_close_expansion_threshold: float = -0.5
    # —— 自动交易：市价 lane（仅黄金启用）的开平仓阈值与开关 ——
    xau_auto_market_contraction_enabled: bool = False
    xau_auto_market_expansion_enabled: bool = False
    xau_auto_market_contraction_threshold: float = 3.0
    xau_auto_market_expansion_threshold: float = -3.0
    xau_auto_market_close_contraction_enabled: bool = False
    xau_auto_market_close_expansion_enabled: bool = False
    xau_auto_market_close_contraction_threshold: float = 0.5
    xau_auto_market_close_expansion_threshold: float = -0.5
    # —— 界面布局与日志 ——
    layout_mode: str = LayoutMode.DUAL.value
    single_symbol_preset: str = "xau"
    selected_symbols: str = "XAUUSDT,SPCXUSDT"
    log_level: str = "normal"
    xau_panel_sections: str = DEFAULT_PANEL_SECTIONS
    xag_panel_sections: str = DEFAULT_PANEL_SECTIONS

    def panel_sections(self, preset_id: str) -> list[PanelSectionEntry]:
        """取该品种的中间栏板块顺序、可见性与各板块 UI 缩放。"""
        raw = self.xag_panel_sections if preset_id == "xag" else self.xau_panel_sections
        return parse_panel_sections(raw)

    def set_panel_sections(
        self, preset_id: str, sections: list[PanelSectionEntry] | list[tuple]
    ) -> None:
        text = serialize_panel_sections(sections)
        if preset_id == "xag":
            self.xag_panel_sections = text
        else:
            self.xau_panel_sections = text

    @property
    def demo_mode(self) -> bool:
        """是否纯模拟模式。"""
        return self.connection_mode == ConnectionMode.DEMO.value

    @property
    def use_live_ba(self) -> bool:
        """BA 是否走实盘。"""
        return self.connection_mode in (ConnectionMode.LIVE_BOTH.value, ConnectionMode.LIVE_BA.value)

    @property
    def use_live_mt5(self) -> bool:
        """MT5 是否走实盘。"""
        return self.connection_mode in (ConnectionMode.LIVE_BOTH.value, ConnectionMode.LIVE_MT5.value)

    def ba_quantity_for(self, preset_id: str) -> float:
        """按 MT5 手数与"手数↔BA 数量"映射比例换算出对应的 BA 下单数量。"""
        if preset_id == "xag":
            ratio = self.xag_ba_qty_map / max(self.xag_mt5_lot_map, 0.001)
            return round(self.xag_trade_lots * ratio, 4)
        ratio = self.xau_ba_qty_map / max(self.xau_mt5_lot_map, 0.001)
        return round(self.xau_trade_lots * ratio, 4)

    def mt5_lot_for(self, preset_id: str) -> float:
        """该品种每次交易的 MT5 手数。"""
        if preset_id == "xag":
            return self.xag_trade_lots
        return self.xau_trade_lots

    # 以下一组 *_for / *_on / *_threshold 方法均为"按 preset_id 取黄金或SPCXUSDT对应字段"
    # 的便捷读取器；带 *_lane 后缀的版本进一步在限价 lane 与市价 lane 之间选择
    # （市价 lane 仅黄金启用，SPCXUSDT市价相关项回退到限价 lane 或返回 0/False）。
    def spread_alert_min(self, preset_id: str) -> float:
        if preset_id == "xag":
            return self.xag_spread_alert_min
        return self.xau_spread_alert_min

    def spread_alert_max(self, preset_id: str) -> float:
        if preset_id == "xag":
            return self.xag_spread_alert_max
        return self.xau_spread_alert_max

    def spread_alerts_on(self, preset_id: str) -> bool:
        if preset_id == "xag":
            return self.xag_spread_alert_enabled
        return self.xau_spread_alert_enabled

    def liq_alerts_on(self, preset_id: str) -> bool:
        if preset_id == "xag":
            return self.xag_liq_alert_enabled
        return self.xau_liq_alert_enabled

    def alert_sound_on(self, preset_id: str) -> bool:
        return self.spread_alerts_on(preset_id) or self.liq_alerts_on(preset_id)

    def any_alert_sound_enabled(self) -> bool:
        return (
            self.spread_alerts_on("xau")
            or self.liq_alerts_on("xau")
            or self.spread_alerts_on("xag")
            or self.liq_alerts_on("xag")
        )

    def auto_contraction_on(self, preset_id: str) -> bool:
        if preset_id == "xag":
            return self.xag_auto_contraction_enabled
        return self.xau_auto_contraction_enabled

    def auto_expansion_on(self, preset_id: str) -> bool:
        if preset_id == "xag":
            return self.xag_auto_expansion_enabled
        return self.xau_auto_expansion_enabled

    def auto_contraction_threshold(self, preset_id: str) -> float:
        if preset_id == "xag":
            return self.xag_auto_contraction_threshold
        return self.xau_auto_contraction_threshold

    def auto_expansion_threshold(self, preset_id: str) -> float:
        if preset_id == "xag":
            return self.xag_auto_expansion_threshold
        return self.xau_auto_expansion_threshold

    def auto_trade_hold_sec(self, preset_id: str) -> float:
        # 自动开/平仓改为满足阈值且已勾选后立即执行；旧配置字段保留但不再参与判断。
        return 0.0

    def auto_close_contraction_on(self, preset_id: str) -> bool:
        if preset_id == "xag":
            return self.xag_auto_close_contraction_enabled
        return self.xau_auto_close_contraction_enabled

    def auto_close_expansion_on(self, preset_id: str) -> bool:
        if preset_id == "xag":
            return self.xag_auto_close_expansion_enabled
        return self.xau_auto_close_expansion_enabled

    def auto_close_contraction_threshold(self, preset_id: str) -> float:
        if preset_id == "xag":
            return self.xag_auto_close_contraction_threshold
        return self.xau_auto_close_contraction_threshold

    def auto_close_expansion_threshold(self, preset_id: str) -> float:
        if preset_id == "xag":
            return self.xag_auto_close_expansion_threshold
        return self.xau_auto_close_expansion_threshold

    def auto_contraction_on_lane(self, preset_id: str, lane: str) -> bool:
        if lane == "market":
            if preset_id == "xau":
                return self.xau_auto_market_contraction_enabled
            if preset_id == "xag":
                return self.xag_auto_contraction_enabled
            return False
        if preset_id == "xag":
            return False
        return self.auto_contraction_on(preset_id)

    def auto_expansion_on_lane(self, preset_id: str, lane: str) -> bool:
        if lane == "market":
            if preset_id == "xau":
                return self.xau_auto_market_expansion_enabled
            if preset_id == "xag":
                return self.xag_auto_expansion_enabled
            return False
        if preset_id == "xag":
            return False
        return self.auto_expansion_on(preset_id)

    def auto_contraction_threshold_lane(self, preset_id: str, lane: str) -> float:
        if lane == "market":
            if preset_id == "xau":
                return self.xau_auto_market_contraction_threshold
            if preset_id == "xag":
                return self.xag_auto_contraction_threshold
            return 0.0
        if preset_id == "xag":
            return 0.0
        return self.auto_contraction_threshold(preset_id)

    def auto_expansion_threshold_lane(self, preset_id: str, lane: str) -> float:
        if lane == "market":
            if preset_id == "xau":
                return self.xau_auto_market_expansion_threshold
            if preset_id == "xag":
                return self.xag_auto_expansion_threshold
            return 0.0
        if preset_id == "xag":
            return 0.0
        return self.auto_expansion_threshold(preset_id)

    def auto_close_contraction_on_lane(self, preset_id: str, lane: str) -> bool:
        if lane == "market":
            if preset_id == "xau":
                return self.xau_auto_market_close_contraction_enabled
            if preset_id == "xag":
                return self.xag_auto_close_contraction_enabled
            return False
        if preset_id == "xag":
            return False
        return self.auto_close_contraction_on(preset_id)

    def auto_close_expansion_on_lane(self, preset_id: str, lane: str) -> bool:
        if lane == "market":
            if preset_id == "xau":
                return self.xau_auto_market_close_expansion_enabled
            if preset_id == "xag":
                return self.xag_auto_close_expansion_enabled
            return False
        if preset_id == "xag":
            return False
        return self.auto_close_expansion_on(preset_id)

    def auto_close_contraction_threshold_lane(self, preset_id: str, lane: str) -> float:
        if lane == "market":
            if preset_id == "xau":
                return self.xau_auto_market_close_contraction_threshold
            if preset_id == "xag":
                return self.xag_auto_close_contraction_threshold
            return 0.0
        if preset_id == "xag":
            return 0.0
        return self.auto_close_contraction_threshold(preset_id)

    def auto_close_expansion_threshold_lane(self, preset_id: str, lane: str) -> float:
        if lane == "market":
            if preset_id == "xau":
                return self.xau_auto_market_close_expansion_threshold
            if preset_id == "xag":
                return self.xag_auto_close_expansion_threshold
            return 0.0
        if preset_id == "xag":
            return 0.0
        return self.auto_close_expansion_threshold(preset_id)
