"""本地成交订单号锚点：用于利润计算器按官方订单号补齐成交明细。"""

from __future__ import annotations

import json
import threading
from datetime import date, datetime
from pathlib import Path
from typing import Any

from app.core.paths import user_data_dir
from app.core.symbols import active_preset_ids

_lock = threading.Lock()
_MAX_RECORDS = 2000


def _path() -> Path:
    return user_data_dir() / "trade_records.json"


def _load_all() -> list[dict]:
    path = _path()
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
    except (json.JSONDecodeError, OSError, TypeError, ValueError):
        pass
    return []


def _save_all(rows: list[dict]) -> None:
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(rows[-_MAX_RECORDS:], ensure_ascii=False, indent=2)
    path.write_text(payload, encoding="utf-8")


def _record_key(item: dict) -> tuple:
    key = str(item.get("record_key") or "")
    if key:
        return ("record_key", key)
    return (
        str(item.get("action") or ""),
        str(item.get("ba_order_no") or ""),
        str(item.get("ex_order_no") or ""),
        str(item.get("order_time") or ""),
    )


def _parse_order_time(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text or text == "--":
        return None
    try:
        return datetime.fromisoformat(text.replace(" ", "T"))
    except ValueError:
        return None


def append_trade_record(row: Any, *, preset_id: str, mode: str, action: str) -> None:
    """保存本机真实成交的订单号配对，后续按订单号查官方历史。"""
    if action not in {"open", "close"}:
        return
    payload = row.to_payload() if hasattr(row, "to_payload") else dict(row or {})
    payload["preset_id"] = preset_id
    payload["mode"] = mode
    payload["action"] = action
    if not payload.get("order_time"):
        payload["order_time"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with _lock:
        rows = _load_all()
        key = _record_key(payload)
        rows = [item for item in rows if _record_key(item) != key]
        rows.append(payload)
        _save_all(rows)


def load_trade_records(
    start: date,
    end: date,
    symbol_filter: str = "all",
) -> list[dict]:
    """读取日期/品种范围内的本地订单号锚点。"""
    out: list[dict] = []
    wanted = set(active_preset_ids()) if symbol_filter == "all" else {symbol_filter}
    start_dt = datetime.combine(start, datetime.min.time())
    end_dt = datetime.combine(end, datetime.max.time())
    with _lock:
        rows = list(_load_all())
    for item in rows:
        preset_id = str(item.get("preset_id") or "")
        if preset_id and preset_id not in wanted:
            continue
        when = _parse_order_time(item.get("order_time"))
        if when is None or when < start_dt or when > end_dt:
            continue
        out.append(item)
    return out
