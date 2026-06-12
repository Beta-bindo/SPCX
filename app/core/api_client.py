from __future__ import annotations

import threading
from typing import Callable, TypeVar

T = TypeVar("T")


class ApiClient:
    """Simple re-entrant lock wrapper for cross-thread API access."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._priority = threading.Event()

    def run(self, fn: Callable[[], T]) -> T:
        with self._lock:
            return fn()

    def run_priority(self, fn: Callable[[], T]) -> T:
        """下单等热路径：标记优先并尽快获取锁。"""
        self._priority.set()
        try:
            with self._lock:
                return fn()
        finally:
            self._priority.clear()

    def priority_pending(self) -> bool:
        return self._priority.is_set()
