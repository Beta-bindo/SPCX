"""Queue trade uploads when the license server is unreachable."""

from __future__ import annotations

import json
import threading
from pathlib import Path

from app.core.paths import user_data_dir

_lock = threading.Lock()


def _path() -> Path:
    return user_data_dir() / "pending_trades.json"


def _trade_key(trade: dict) -> tuple:
    key = trade.get("record_key", "")
    if key:
        return ("hedge", key)
    return (
        trade.get("order_time", ""),
        trade.get("ba_order_no", ""),
        trade.get("ex_order_no", ""),
    )


def load_pending() -> list[dict]:
    path = _path()
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return data
    except (json.JSONDecodeError, TypeError, ValueError):
        pass
    return []


def save_pending(trades: list[dict]) -> None:
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(trades, ensure_ascii=False, indent=2), encoding="utf-8")


def enqueue_trades(trades: list[dict]) -> None:
    if not trades:
        return
    with _lock:
        pending = load_pending()
        keys = {_trade_key(item) for item in pending}
        for trade in trades:
            key = _trade_key(trade)
            if key in keys:
                continue
            pending.append(trade)
            keys.add(key)
        save_pending(pending)


def remove_trades(trades: list[dict]) -> None:
    if not trades:
        return
    remove_keys = {_trade_key(item) for item in trades}
    with _lock:
        pending = [item for item in load_pending() if _trade_key(item) not in remove_keys]
        save_pending(pending)


def clear_pending() -> None:
    with _lock:
        save_pending([])
