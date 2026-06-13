"""配置持久化：在 ~/.xau_assistant/config.json 与 AppConfig 之间读写。

负责老版本字段的兼容迁移（_migrate_legacy）、敏感字段的加/解密（API 密钥、MT5 密码），
以及异步保存以避免阻塞 UI。读取失败时回退到默认配置而非崩溃。
"""

from __future__ import annotations

import copy
import json
import threading
from pathlib import Path

from app.core.models import (
    AppConfig,
    ConnectionMode,
    BA_REFRESH_INTERVAL_DEFAULT,
    DEFAULT_PANEL_SECTIONS,
    normalize_ba_refresh_interval,
)
from app.core.app_log import LOG_LEVEL_DEFAULT, normalize_log_level
from app.core.secret_store import protect_secret, unprotect_secret

CONFIG_DIR = Path.home() / ".xau_assistant"
CONFIG_FILE = CONFIG_DIR / "config.json"


def ensure_config_dir() -> None:
    """确保配置目录存在。"""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)


def _migrate_legacy(data: dict) -> dict:
    """把旧版本配置字段补齐/迁移为当前结构（向后兼容历史 config.json）。"""
    if "connection_mode" not in data and data.get("demo_mode", True):
        data["connection_mode"] = ConnectionMode.DEMO.value
    elif "connection_mode" not in data:
        data["connection_mode"] = ConnectionMode.LIVE_BOTH.value
    if "symbol_preset" not in data:
        data["symbol_preset"] = "xau"
    if "xau_ba_qty_map" not in data and "xau_ba_quantity" in data:
        data.setdefault("xau_ba_qty_map", data.get("xau_ba_quantity", 500))
        data.setdefault("xau_mt5_lot_map", 1.0)
        data.setdefault("xau_trade_lots", data.get("xau_mt5_lot_size", 1.0))
    if "xag_ba_qty_map" not in data and "xag_ba_quantity" in data:
        data.setdefault("xag_ba_qty_map", data.get("xag_ba_quantity", 5000))
        data.setdefault("xag_mt5_lot_map", 1.0)
        data.setdefault("xag_trade_lots", data.get("xag_mt5_lot_size", 1.0))
    if "xau_spread_alert_min" not in data and "alert_ba_spread" in data:
        data.setdefault("xau_spread_alert_min", 0.0)
        data.setdefault("xau_spread_alert_max", data.get("alert_ba_spread", 3.0))
    legacy_sound = bool(data.get("alert_sound_enabled", True))
    data.setdefault("xau_alert_sound_enabled", legacy_sound)
    data.setdefault("xag_alert_sound_enabled", legacy_sound)
    legacy_xau = bool(data.get("xau_alert_sound_enabled", True))
    legacy_xag = bool(data.get("xag_alert_sound_enabled", True))
    data.setdefault("xau_spread_sound_enabled", legacy_xau)
    data.setdefault("xau_liq_sound_enabled", legacy_xau)
    data.setdefault("xag_spread_sound_enabled", legacy_xag)
    data.setdefault("xag_liq_sound_enabled", legacy_xag)
    for prefix in ("xau", "xag"):
        spread_on = bool(data.get(f"{prefix}_spread_alert_enabled", True))
        liq_on = bool(data.get(f"{prefix}_liq_alert_enabled", True))
        data[f"{prefix}_spread_sound_enabled"] = spread_on
        data[f"{prefix}_liq_sound_enabled"] = liq_on
        data[f"{prefix}_alert_sound_enabled"] = spread_on or liq_on
    if "xau_auto_contraction_enabled" not in data and data.get("xau_auto_trade"):
        data.setdefault("xau_auto_contraction_enabled", True)
    if "xag_auto_contraction_enabled" not in data and data.get("xag_auto_trade"):
        data.setdefault("xag_auto_contraction_enabled", True)
    return data


def load_config() -> AppConfig:
    """读取并解析配置文件为 AppConfig；文件缺失或损坏时返回默认配置。"""
    ensure_config_dir()
    if not CONFIG_FILE.exists():
        return AppConfig()
    try:
        # 解密敏感字段、按字段填充并做兼容迁移；任一步异常都回退默认配置
        data = _migrate_legacy(json.loads(CONFIG_FILE.read_text(encoding="utf-8")))
        cfg = AppConfig(
            ba_api_key=data.get("ba_api_key", ""),
            ba_api_secret=unprotect_secret(data.get("ba_api_secret", "")),
            proxy_host=data.get("proxy_host", "127.0.0.1"),
            proxy_port=int(data.get("proxy_port", 7897)),
            use_proxy=bool(data.get("use_proxy", False)),
            mt5_login=int(data.get("mt5_login", 0)),
            mt5_password=unprotect_secret(data.get("mt5_password", "")),
            mt5_server=data.get("mt5_server", ""),
            mt5_terminal_path=data.get("mt5_terminal_path", ""),
            connection_mode=data.get("connection_mode", ConnectionMode.DEMO.value),
            symbol_preset=data.get("symbol_preset", "xau"),
            symbol_ba=data.get("symbol_ba", "XAUUSDT"),
            symbol_mt5=data.get("symbol_mt5", "XAUUSD"),
            mt5_lot_size=float(data.get("mt5_lot_size", 1.0)),
            ba_quantity=float(data.get("ba_quantity", 500.0)),
            xau_ba_quantity=float(data.get("xau_ba_quantity", data.get("xau_ba_qty_map", 500.0))),
            xau_mt5_lot_size=float(data.get("xau_mt5_lot_size", data.get("xau_trade_lots", 1.0))),
            xag_ba_quantity=float(data.get("xag_ba_quantity", data.get("xag_ba_qty_map", 5000.0))),
            xag_mt5_lot_size=float(data.get("xag_mt5_lot_size", data.get("xag_trade_lots", 1.0))),
            xau_ba_qty_map=float(data.get("xau_ba_qty_map", 500.0)),
            xau_mt5_lot_map=float(data.get("xau_mt5_lot_map", 1.0)),
            xau_trade_lots=float(data.get("xau_trade_lots", 1.0)),
            xag_ba_qty_map=float(data.get("xag_ba_qty_map", 5000.0)),
            xag_mt5_lot_map=float(data.get("xag_mt5_lot_map", 1.0)),
            xag_trade_lots=float(data.get("xag_trade_lots", 1.0)),
            ba_fee_rate=float(data.get("ba_fee_rate", 0.0004)),
            mt5_commission_per_lot=float(data.get("mt5_commission_per_lot", 0.0)),
            mt5_spread_points=float(data.get("mt5_spread_points", 0.25)),
            mt5_point_value=float(data.get("mt5_point_value", 1.0)),
            theme=data.get("theme", "light"),
            ba_leverage=int(data.get("ba_leverage", 20)),
            mt5_leverage=int(data.get("mt5_leverage", 100)),
            sync_leverage_on_trade=bool(data.get("sync_leverage_on_trade", False)),
            ba_refresh_interval_sec=normalize_ba_refresh_interval(
                data.get("ba_refresh_interval_sec", BA_REFRESH_INTERVAL_DEFAULT)
            ),
            ba_maker_timeout_sec=float(data.get("ba_maker_timeout_sec", 5.0)),
            auto_trade_max_latency_ms=float(data.get("auto_trade_max_latency_ms", 200.0)),
            xau_spread_alert_min=float(data.get("xau_spread_alert_min", 1.0)),
            xau_spread_alert_max=float(data.get("xau_spread_alert_max", 3.0)),
            xag_spread_alert_min=float(data.get("xag_spread_alert_min", -2.0)),
            xag_spread_alert_max=float(data.get("xag_spread_alert_max", 1.0)),
            xau_ba_liq_alert=float(data.get("xau_ba_liq_alert", data.get("alert_ba_liq_usdt", 100.0))),
            xau_mt5_liq_alert=float(data.get("xau_mt5_liq_alert", data.get("alert_mt5_liq_usdt", 100.0))),
            xag_ba_liq_alert=float(data.get("xag_ba_liq_alert", 50.0)),
            xag_mt5_liq_alert=float(data.get("xag_mt5_liq_alert", 50.0)),
            xau_alert_sound_enabled=bool(data.get("xau_alert_sound_enabled", True)),
            xag_alert_sound_enabled=bool(data.get("xag_alert_sound_enabled", True)),
            xau_spread_sound_enabled=bool(data.get("xau_spread_sound_enabled", True)),
            xau_liq_sound_enabled=bool(data.get("xau_liq_sound_enabled", True)),
            xag_spread_sound_enabled=bool(data.get("xag_spread_sound_enabled", True)),
            xag_liq_sound_enabled=bool(data.get("xag_liq_sound_enabled", True)),
            xau_spread_alert_enabled=bool(data.get("xau_spread_alert_enabled", True)),
            xag_spread_alert_enabled=bool(data.get("xag_spread_alert_enabled", True)),
            xau_liq_alert_enabled=bool(data.get("xau_liq_alert_enabled", True)),
            xag_liq_alert_enabled=bool(data.get("xag_liq_alert_enabled", True)),
            xau_auto_contraction_enabled=bool(data.get("xau_auto_contraction_enabled", False)),
            xau_auto_expansion_enabled=bool(data.get("xau_auto_expansion_enabled", False)),
            xag_auto_contraction_enabled=bool(data.get("xag_auto_contraction_enabled", False)),
            xag_auto_expansion_enabled=bool(data.get("xag_auto_expansion_enabled", False)),
            xau_auto_contraction_threshold=float(data.get("xau_auto_contraction_threshold", 3.0)),
            xau_auto_expansion_threshold=float(data.get("xau_auto_expansion_threshold", -3.0)),
            xag_auto_contraction_threshold=float(data.get("xag_auto_contraction_threshold", 3.0)),
            xag_auto_expansion_threshold=float(data.get("xag_auto_expansion_threshold", -3.0)),
            xau_auto_trade_hold_sec=float(data.get("xau_auto_trade_hold_sec", 3.0)),
            xag_auto_trade_hold_sec=float(data.get("xag_auto_trade_hold_sec", 3.0)),
            xau_auto_close_contraction_enabled=bool(
                data.get("xau_auto_close_contraction_enabled", False)
            ),
            xau_auto_close_expansion_enabled=bool(
                data.get("xau_auto_close_expansion_enabled", False)
            ),
            xag_auto_close_contraction_enabled=bool(
                data.get("xag_auto_close_contraction_enabled", False)
            ),
            xag_auto_close_expansion_enabled=bool(
                data.get("xag_auto_close_expansion_enabled", False)
            ),
            xau_auto_close_contraction_threshold=float(
                data.get("xau_auto_close_contraction_threshold", 0.5)
            ),
            xau_auto_close_expansion_threshold=float(
                data.get("xau_auto_close_expansion_threshold", -0.5)
            ),
            xag_auto_close_contraction_threshold=float(
                data.get("xag_auto_close_contraction_threshold", 0.5)
            ),
            xag_auto_close_expansion_threshold=float(
                data.get("xag_auto_close_expansion_threshold", -0.5)
            ),
            xau_auto_market_contraction_enabled=bool(
                data.get("xau_auto_market_contraction_enabled", False)
            ),
            xau_auto_market_expansion_enabled=bool(
                data.get("xau_auto_market_expansion_enabled", False)
            ),
            xau_auto_market_contraction_threshold=float(
                data.get("xau_auto_market_contraction_threshold", 3.0)
            ),
            xau_auto_market_expansion_threshold=float(
                data.get("xau_auto_market_expansion_threshold", -3.0)
            ),
            xau_auto_market_close_contraction_enabled=bool(
                data.get("xau_auto_market_close_contraction_enabled", False)
            ),
            xau_auto_market_close_expansion_enabled=bool(
                data.get("xau_auto_market_close_expansion_enabled", False)
            ),
            xau_auto_market_close_contraction_threshold=float(
                data.get("xau_auto_market_close_contraction_threshold", 0.5)
            ),
            xau_auto_market_close_expansion_threshold=float(
                data.get("xau_auto_market_close_expansion_threshold", -0.5)
            ),
            layout_mode=data.get("layout_mode", "dual"),
            single_symbol_preset=data.get("single_symbol_preset", "xau"),
            log_level=normalize_log_level(data.get("log_level", LOG_LEVEL_DEFAULT)),
            xau_panel_sections=data.get("xau_panel_sections", DEFAULT_PANEL_SECTIONS),
            xag_panel_sections=data.get("xag_panel_sections", DEFAULT_PANEL_SECTIONS),
        )
        # 由手数映射重算派生的 BA 数量/手数，保证一致性
        cfg.xau_ba_quantity = cfg.ba_quantity_for("xau")
        cfg.xag_ba_quantity = cfg.ba_quantity_for("xag")
        cfg.xau_mt5_lot_size = cfg.mt5_lot_for("xau")
        cfg.xag_mt5_lot_size = cfg.mt5_lot_for("xag")
        return cfg
    except (json.JSONDecodeError, OSError, ValueError):
        return AppConfig()


def save_config(config: AppConfig) -> None:
    """将 AppConfig 序列化写入磁盘（敏感字段加密、中文不转义）。"""
    ensure_config_dir()
    config.xau_ba_quantity = config.ba_quantity_for("xau")
    config.xag_ba_quantity = config.ba_quantity_for("xag")
    config.xau_mt5_lot_size = config.mt5_lot_for("xau")
    config.xag_mt5_lot_size = config.mt5_lot_for("xag")
    payload = {
        "ba_api_key": config.ba_api_key,
        "ba_api_secret": protect_secret(config.ba_api_secret),
        "proxy_host": config.proxy_host,
        "proxy_port": config.proxy_port,
        "use_proxy": config.use_proxy,
        "mt5_login": config.mt5_login,
        "mt5_password": protect_secret(config.mt5_password),
        "mt5_server": config.mt5_server,
        "mt5_terminal_path": config.mt5_terminal_path,
        "connection_mode": config.connection_mode,
        "symbol_preset": config.symbol_preset,
        "symbol_ba": config.symbol_ba,
        "symbol_mt5": config.symbol_mt5,
        "mt5_lot_size": config.xau_mt5_lot_size,
        "ba_quantity": config.xau_ba_quantity,
        "xau_ba_quantity": config.xau_ba_quantity,
        "xau_mt5_lot_size": config.xau_mt5_lot_size,
        "xag_ba_quantity": config.xag_ba_quantity,
        "xag_mt5_lot_size": config.xag_mt5_lot_size,
        "xau_ba_qty_map": config.xau_ba_qty_map,
        "xau_mt5_lot_map": config.xau_mt5_lot_map,
        "xau_trade_lots": config.xau_trade_lots,
        "xag_ba_qty_map": config.xag_ba_qty_map,
        "xag_mt5_lot_map": config.xag_mt5_lot_map,
        "xag_trade_lots": config.xag_trade_lots,
        "ba_fee_rate": config.ba_fee_rate,
        "mt5_commission_per_lot": config.mt5_commission_per_lot,
        "mt5_spread_points": config.mt5_spread_points,
        "mt5_point_value": config.mt5_point_value,
        "theme": config.theme,
        "ba_leverage": config.ba_leverage,
        "mt5_leverage": config.mt5_leverage,
        "sync_leverage_on_trade": config.sync_leverage_on_trade,
        "ba_refresh_interval_sec": config.ba_refresh_interval_sec,
        "ba_maker_timeout_sec": config.ba_maker_timeout_sec,
        "auto_trade_max_latency_ms": config.auto_trade_max_latency_ms,
        "xau_spread_alert_min": config.xau_spread_alert_min,
        "xau_spread_alert_max": config.xau_spread_alert_max,
        "xag_spread_alert_min": config.xag_spread_alert_min,
        "xag_spread_alert_max": config.xag_spread_alert_max,
        "xau_ba_liq_alert": config.xau_ba_liq_alert,
        "xau_mt5_liq_alert": config.xau_mt5_liq_alert,
        "xag_ba_liq_alert": config.xag_ba_liq_alert,
        "xag_mt5_liq_alert": config.xag_mt5_liq_alert,
        "xau_alert_sound_enabled": config.xau_alert_sound_enabled,
        "xag_alert_sound_enabled": config.xag_alert_sound_enabled,
        "xau_spread_sound_enabled": config.xau_spread_sound_enabled,
        "xau_liq_sound_enabled": config.xau_liq_sound_enabled,
        "xag_spread_sound_enabled": config.xag_spread_sound_enabled,
        "xag_liq_sound_enabled": config.xag_liq_sound_enabled,
        "xau_spread_alert_enabled": config.xau_spread_alert_enabled,
        "xag_spread_alert_enabled": config.xag_spread_alert_enabled,
        "xau_liq_alert_enabled": config.xau_liq_alert_enabled,
        "xag_liq_alert_enabled": config.xag_liq_alert_enabled,
        "xau_auto_contraction_enabled": config.xau_auto_contraction_enabled,
        "xau_auto_expansion_enabled": config.xau_auto_expansion_enabled,
        "xag_auto_contraction_enabled": config.xag_auto_contraction_enabled,
        "xag_auto_expansion_enabled": config.xag_auto_expansion_enabled,
        "xau_auto_contraction_threshold": config.xau_auto_contraction_threshold,
        "xau_auto_expansion_threshold": config.xau_auto_expansion_threshold,
        "xag_auto_contraction_threshold": config.xag_auto_contraction_threshold,
        "xag_auto_expansion_threshold": config.xag_auto_expansion_threshold,
        "xau_auto_trade_hold_sec": config.xau_auto_trade_hold_sec,
        "xag_auto_trade_hold_sec": config.xag_auto_trade_hold_sec,
        "xau_auto_close_contraction_enabled": config.xau_auto_close_contraction_enabled,
        "xau_auto_close_expansion_enabled": config.xau_auto_close_expansion_enabled,
        "xag_auto_close_contraction_enabled": config.xag_auto_close_contraction_enabled,
        "xag_auto_close_expansion_enabled": config.xag_auto_close_expansion_enabled,
        "xau_auto_close_contraction_threshold": config.xau_auto_close_contraction_threshold,
        "xau_auto_close_expansion_threshold": config.xau_auto_close_expansion_threshold,
        "xag_auto_close_contraction_threshold": config.xag_auto_close_contraction_threshold,
        "xag_auto_close_expansion_threshold": config.xag_auto_close_expansion_threshold,
        "xau_auto_market_contraction_enabled": config.xau_auto_market_contraction_enabled,
        "xau_auto_market_expansion_enabled": config.xau_auto_market_expansion_enabled,
        "xau_auto_market_contraction_threshold": config.xau_auto_market_contraction_threshold,
        "xau_auto_market_expansion_threshold": config.xau_auto_market_expansion_threshold,
        "xau_auto_market_close_contraction_enabled": config.xau_auto_market_close_contraction_enabled,
        "xau_auto_market_close_expansion_enabled": config.xau_auto_market_close_expansion_enabled,
        "xau_auto_market_close_contraction_threshold": config.xau_auto_market_close_contraction_threshold,
        "xau_auto_market_close_expansion_threshold": config.xau_auto_market_close_expansion_threshold,
        "layout_mode": config.layout_mode,
        "single_symbol_preset": config.single_symbol_preset,
        "log_level": normalize_log_level(config.log_level),
        "xau_panel_sections": config.xau_panel_sections,
        "xag_panel_sections": config.xag_panel_sections,
    }
    CONFIG_FILE.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


_save_config_lock = threading.Lock()


def save_config_async(config: AppConfig) -> None:
    """后台写入配置，避免阻塞下单点击响应。"""
    snapshot = copy.deepcopy(config)

    def _write() -> None:
        with _save_config_lock:
            save_config(snapshot)

    threading.Thread(target=_write, daemon=True, name="save-config").start()
