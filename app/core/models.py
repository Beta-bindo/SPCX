from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class ConnectionMode(str, Enum):
    DEMO = "demo"
    LIVE_BOTH = "live_both"
    LIVE_BA = "live_ba"
    LIVE_MT5 = "live_mt5"


class ConnectionState(str, Enum):
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    ERROR = "error"
    SIMULATED = "simulated"


class Side(str, Enum):
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


# 中间栏可配置板块（顺序即默认显示顺序）。点差价格区与对冲交易按钮区固定常驻，不参与此配置。
PANEL_SECTION_KEYS: tuple[str, ...] = ("alert", "auto", "position")
PANEL_SECTION_LABELS: dict[str, str] = {
    "alert": "告警设置",
    "auto": "自动交易",
    "position": "当前持仓 / 盈利",
}
DEFAULT_PANEL_SECTIONS: str = "alert:1,auto:1,position:1"


def parse_panel_sections(raw: str | None) -> list[tuple[str, bool]]:
    """解析 "alert:1,auto:0,position:1" 为有序的 (key, visible) 列表，并补齐缺失板块。"""
    result: list[tuple[str, bool]] = []
    seen: set[str] = set()
    for chunk in (raw or "").split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        key, _, flag = chunk.partition(":")
        key = key.strip()
        if key not in PANEL_SECTION_KEYS or key in seen:
            continue
        visible = flag.strip() not in ("0", "false", "False", "no")
        result.append((key, visible))
        seen.add(key)
    for key in PANEL_SECTION_KEYS:
        if key not in seen:
            result.append((key, True))
    return result


def serialize_panel_sections(sections: list[tuple[str, bool]]) -> str:
    parts: list[str] = []
    seen: set[str] = set()
    for key, visible in sections:
        if key not in PANEL_SECTION_KEYS or key in seen:
            continue
        parts.append(f"{key}:{1 if visible else 0}")
        seen.add(key)
    for key in PANEL_SECTION_KEYS:
        if key not in seen:
            parts.append(f"{key}:1")
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
    symbol: str
    bid: float = 0.0
    ask: float = 0.0
    timestamp: float = 0.0
    is_simulated: bool = False

    @property
    def mid(self) -> float:
        if self.bid > 0 and self.ask > 0:
            return (self.bid + self.ask) / 2
        return self.bid or self.ask


@dataclass
class OrderBookLevel:
    price: float
    quantity: float


@dataclass
class OrderBook:
    bids: list[OrderBookLevel] = field(default_factory=list)
    asks: list[OrderBookLevel] = field(default_factory=list)
    is_simulated: bool = False


@dataclass
class Position:
    platform: str
    symbol: str
    side: Side = Side.NONE
    quantity: float = 0.0
    entry_price: float = 0.0
    current_price: float = 0.0
    unrealized_pnl: float = 0.0
    estimated_fee: float = 0.0
    liquidation_price: float = 0.0
    mark_price: float = 0.0
    leverage: int = 0
    margin_type: str = ""
    exchange_liq_buffer: float | None = None


@dataclass
class SpreadSnapshot:
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
        """币安 − Exness（中间价差）."""
        return self.mid_spread


@dataclass
class RiskSnapshot:
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
    ba_quotes: dict = field(default_factory=dict)
    mt5_quotes: dict = field(default_factory=dict)
    spreads: dict = field(default_factory=dict)
    risk: RiskSnapshot = field(default_factory=RiskSnapshot)


@dataclass
class AppConfig:
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
    symbol_preset: str = "xau"
    symbol_ba: str = "XAUUSDT"
    symbol_mt5: str = "XAUUSD"
    mt5_lot_size: float = 0.01
    ba_quantity: float = 0.01
    xau_ba_quantity: float = 500.0
    xau_mt5_lot_size: float = 1.0
    xag_ba_quantity: float = 5000.0
    xag_mt5_lot_size: float = 1.0
    xau_ba_qty_map: float = 500.0
    xau_mt5_lot_map: float = 1.0
    xau_trade_lots: float = 1.0
    xag_ba_qty_map: float = 5000.0
    xag_mt5_lot_map: float = 1.0
    xag_trade_lots: float = 1.0
    ba_fee_rate: float = 0.0004
    mt5_commission_per_lot: float = 0.0
    mt5_spread_points: float = 0.25
    mt5_point_value: float = 1.0
    theme: str = "light"
    ba_leverage: int = 20
    mt5_leverage: int = 100
    sync_leverage_on_trade: bool = False
    ba_refresh_interval_sec: float = BA_REFRESH_INTERVAL_DEFAULT
    ba_maker_timeout_sec: float = 5.0
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
    xau_auto_market_contraction_enabled: bool = False
    xau_auto_market_expansion_enabled: bool = False
    xau_auto_market_contraction_threshold: float = 3.0
    xau_auto_market_expansion_threshold: float = -3.0
    xau_auto_market_close_contraction_enabled: bool = False
    xau_auto_market_close_expansion_enabled: bool = False
    xau_auto_market_close_contraction_threshold: float = 0.5
    xau_auto_market_close_expansion_threshold: float = -0.5
    layout_mode: str = LayoutMode.DUAL.value
    single_symbol_preset: str = "xau"
    log_level: str = "normal"
    xau_panel_sections: str = DEFAULT_PANEL_SECTIONS
    xag_panel_sections: str = DEFAULT_PANEL_SECTIONS

    def panel_sections(self, preset_id: str) -> list[tuple[str, bool]]:
        raw = self.xag_panel_sections if preset_id == "xag" else self.xau_panel_sections
        return parse_panel_sections(raw)

    def set_panel_sections(
        self, preset_id: str, sections: list[tuple[str, bool]]
    ) -> None:
        text = serialize_panel_sections(sections)
        if preset_id == "xag":
            self.xag_panel_sections = text
        else:
            self.xau_panel_sections = text

    @property
    def demo_mode(self) -> bool:
        return self.connection_mode == ConnectionMode.DEMO.value

    @property
    def use_live_ba(self) -> bool:
        return self.connection_mode in (ConnectionMode.LIVE_BOTH.value, ConnectionMode.LIVE_BA.value)

    @property
    def use_live_mt5(self) -> bool:
        return self.connection_mode in (ConnectionMode.LIVE_BOTH.value, ConnectionMode.LIVE_MT5.value)

    def ba_quantity_for(self, preset_id: str) -> float:
        if preset_id == "xag":
            ratio = self.xag_ba_qty_map / max(self.xag_mt5_lot_map, 0.001)
            return round(self.xag_trade_lots * ratio, 4)
        ratio = self.xau_ba_qty_map / max(self.xau_mt5_lot_map, 0.001)
        return round(self.xau_trade_lots * ratio, 4)

    def mt5_lot_for(self, preset_id: str) -> float:
        if preset_id == "xag":
            return self.xag_trade_lots
        return self.xau_trade_lots

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
        if preset_id == "xag":
            return self.xag_auto_trade_hold_sec
        return self.xau_auto_trade_hold_sec

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
