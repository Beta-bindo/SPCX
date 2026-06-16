"""Binance U 本位合约行情 WebSocket 推流（后台线程 + asyncio）。

一条组合连接同时订阅：
- @bookTicker：买一/卖一变化即推（顶档，tick 级，供点差/告警最快更新）；
- @depth20@{ms}ms：前 20 档部分深度快照（多档订单簿展示，可选）。
断线自动重连；连接状态与最近消息时间供上层判断 REST 兜底。
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from typing import Callable

import websockets

from app.core.system_proxy import resolve_http_proxy

FUTURES_WS_BASE = "wss://fstream.binance.com/stream"
WS_STALE_SEC = 10.0
WS_RECONNECT_BASE_SEC = 1.0
WS_RECONNECT_MAX_SEC = 30.0


class BinanceWsStream:
    """在守护线程中运行 asyncio 事件循环，推送 bookTicker 买卖一价。"""

    def __init__(
        self,
        symbols: list[str],
        *,
        use_proxy: bool,
        proxy_host: str,
        proxy_port: int,
        on_quote: Callable[[str, float, float], None],
        on_state: Callable[[str], None],
        on_depth: Callable[[str, list, list], None] | None = None,
        depth_ms: int = 0,
    ) -> None:
        self._symbols = [s.lower() for s in symbols]
        self._use_proxy = use_proxy
        self._proxy_host = proxy_host
        self._proxy_port = proxy_port
        self._on_quote = on_quote
        self._on_state = on_state
        self._on_depth = on_depth
        self._depth_ms = int(depth_ms) if on_depth is not None else 0
        # 独立 stop_event，绝不与连接器共享，避免误停 REST 兜底轮询
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._task: asyncio.Task | None = None
        self._last_message_at: float = 0.0
        self._last_depth_at: float = 0.0
        self._connected = False
        self._session_connected = False

    @property
    def last_message_at(self) -> float:
        return self._last_message_at

    @property
    def is_connected(self) -> bool:
        return self._connected

    def is_live(self, *, stale_sec: float = WS_STALE_SEC) -> bool:
        """已连接且近期收到推送，视为 WS 行情可用。"""
        if not self._connected:
            return False
        if self._last_message_at <= 0:
            return False
        return time.time() - self._last_message_at <= stale_sec

    def depth_is_live(self, *, stale_sec: float = WS_STALE_SEC) -> bool:
        """已连接且近期收到深度快照，视为 WS 订单簿可用（否则上层走 REST 兜底）。"""
        if not self._connected:
            return False
        if self._last_depth_at <= 0:
            return False
        return time.time() - self._last_depth_at <= stale_sec

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event = threading.Event()
        self._thread = threading.Thread(
            target=self._run_loop,
            daemon=True,
            name="binance-ws-stream",
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        loop = self._loop
        task = self._task
        if loop is not None and task is not None:
            # 主动取消事件循环里的任务，立即中断 connect/recv/sleep，避免线程残留
            try:
                loop.call_soon_threadsafe(task.cancel)
            except RuntimeError:
                pass
        thread = self._thread
        if thread is not None and thread.is_alive() and threading.current_thread() is not thread:
            # daemon 线程，已发取消信号；短等即可，剩余清理在后台完成，避免阻塞主线程
            thread.join(timeout=0.5)
        self._thread = None
        self._set_connected(False)

    def _set_connected(self, connected: bool) -> None:
        if self._connected == connected:
            return
        self._connected = connected
        if self._stop_event.is_set():
            return
        self._on_state("streaming" if connected else "connecting")

    def _run_loop(self) -> None:
        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)
        try:
            self._task = loop.create_task(self._main())
            loop.run_until_complete(self._task)
        except asyncio.CancelledError:
            pass
        finally:
            self._set_connected(False)
            try:
                loop.close()
            except Exception:
                pass
            self._loop = None
            self._task = None

    def _build_url(self) -> str:
        parts: list[str] = []
        for sym in self._symbols:
            parts.append(f"{sym}@bookTicker")
            if self._depth_ms > 0:
                parts.append(f"{sym}@depth20@{self._depth_ms}ms")
        streams = "/".join(parts)
        return f"{FUTURES_WS_BASE}?streams={streams}"

    def _proxy_url(self) -> str | None:
        if not self._use_proxy:
            return None
        host, port, _ = resolve_http_proxy(self._proxy_host, self._proxy_port)
        return f"http://{host}:{port}"

    async def _main(self) -> None:
        backoff = WS_RECONNECT_BASE_SEC
        while not self._stop_event.is_set():
            self._on_state("connecting")
            self._session_connected = False
            try:
                await self._listen_once()
            except asyncio.CancelledError:
                break
            except Exception:
                pass
            # 连接已断开：立即清掉最近消息时间并标记未连接，
            # 让 is_live 立刻转 False，迫使上层切回 REST 兜底，避免用过期数据。
            self._last_message_at = 0.0
            self._last_depth_at = 0.0
            self._set_connected(False)
            if self._stop_event.is_set():
                break
            if self._session_connected:
                # 曾成功连上又掉线：立即用最短间隔重连，不累积退避
                backoff = WS_RECONNECT_BASE_SEC
            await asyncio.sleep(backoff)
            if not self._session_connected:
                # 始终连不上（多为代理/网络问题）：指数退避，降低无效重试
                backoff = min(backoff * 2, WS_RECONNECT_MAX_SEC)

    async def _listen_once(self) -> bool:
        url = self._build_url()
        # 始终显式传 proxy：用代理则走 HTTP 代理；否则 None 关闭 websockets 的
        # 系统/环境代理自动探测（避免误用系统 SOCKS 代理导致 python-socks 报错）。
        connect_kwargs: dict = {
            "ping_interval": 20,
            "ping_timeout": 20,
            "close_timeout": 1,
            "open_timeout": 15,
            "proxy": self._proxy_url(),
        }

        async with websockets.connect(url, **connect_kwargs) as ws:
            self._set_connected(True)
            self._session_connected = True
            while not self._stop_event.is_set():
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=5.0)
                except asyncio.TimeoutError:
                    # 行情静默期（周末/低波动）保持连接不重连：
                    # socket 由 ping/pong 维持探活；若长时间无数据，is_live 自然转 False，
                    # 上层会切 REST 兜底；真正掉线时 ping_timeout 会抛 ConnectionClosed。
                    continue
                self._handle_message(raw)
        return True

    def _handle_message(self, raw: str | bytes) -> None:
        try:
            payload = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return

        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, dict):
            data = payload if isinstance(payload, dict) else None
        if not data:
            return

        symbol = str(data.get("s", "")).upper()
        if not symbol:
            return

        # 区分流类型：bookTicker 的 b/a 是最优价（字符串单值），
        # depth20 的 b/a 是 [[价,量],...] 数组。
        if isinstance(data.get("b"), list):
            self._handle_depth(symbol, data)
        else:
            self._handle_book_ticker(symbol, data)

    def _handle_book_ticker(self, symbol: str, data: dict) -> None:
        try:
            bid = float(data.get("b", 0) or 0)
            ask = float(data.get("a", 0) or 0)
        except (TypeError, ValueError):
            # 单条坏消息直接丢弃，不让其冒泡触发整条连接重连
            return
        if bid <= 0 or ask <= 0:
            return

        self._last_message_at = time.time()
        self._on_quote(symbol, bid, ask)

    def _handle_depth(self, symbol: str, data: dict) -> None:
        if self._on_depth is None:
            return
        try:
            bids = [(float(p), float(q)) for p, q in data.get("b", [])]
            asks = [(float(p), float(q)) for p, q in data.get("a", [])]
        except (TypeError, ValueError):
            return
        if not bids or not asks:
            return

        now = time.time()
        self._last_message_at = now
        self._last_depth_at = now
        self._on_depth(symbol, bids, asks)
