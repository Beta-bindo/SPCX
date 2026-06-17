"""记录最近开/平仓时间锚点，供 BA 资金费区间查询；不再维护本地流水。"""

from __future__ import annotations

import json
import threading
from datetime import datetime
from pathlib import Path

from app.core.paths import user_data_dir

_lock = threading.Lock()


def _path() -> Path:
    return user_data_dir() / "trade_anchors.json"


def _load() -> dict[str, str]:
    path = _path()
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return {str(k): str(v) for k, v in data.items()}
    except (json.JSONDecodeError, TypeError, ValueError):
        pass
    return {}


def _save(data: dict[str, str]) -> None:
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _key(preset_id: str, mode: str) -> str:
    return f"{preset_id}:{mode}"


def record_trade_anchor(preset_id: str, mode: str, action: str, settled_at: str | None = None) -> None:
    """记录某品种某模式最近一次开/平仓时间。"""
    if action not in ("open", "close"):
        return
    stamp = settled_at or datetime.now().isoformat(timespec="seconds")
    with _lock:
        data = _load()
        data[_key(preset_id, mode)] = stamp
        _save(data)


def funding_period_start(preset_id: str, mode: str) -> datetime | None:
    """本次应对账的 BA 资金费起始时刻：最近一次同品种同模式的开/平仓。"""
    stamp = _load().get(_key(preset_id, mode), "")
    if not stamp:
        return None
    try:
        return datetime.fromisoformat(stamp.replace(" ", "T"))
    except ValueError:
        return None


def hedge_sides(mode: str) -> tuple[str, str]:
    """对冲模式 → (BA 方向, MT5 方向)。"""
    if mode == "expansion":
        return "BUY", "SELL"
    return "SELL", "BUY"
