"""Binance U 本位合约 User Data Stream（账户私有推送：订单 / 持仓 / 余额）。

通过 listenKey 连接 wss://fstream.binance.com/ws/<listenKey>，实时推送：
- ORDER_TRADE_UPDATE：委托状态 / 成交（用于 Maker 成交即时驱动 Exness 补腿）
- ACCOUNT_UPDATE：持仓 / 余额变化（用于失效持仓缓存、尽快刷新 UI）
- listenKeyExpired：listenKey 失效，需要重新申请并重连

与 bookTicker 行情流（binance_ws_stream）分离：那条是公开行情，这条是账户私有。
断线与 listenKey 失效都会触发重连：每次连接前通过 get_listen_key 回调取（新）key；
申请 / 续期 listenKey 的 REST 由连接器侧串行执行，本类只负责 WS 收发与解析。
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from typing import Callable

import websockets

from app.core.system_proxy import resolve_http_proxy

FUTURES_USER_WS_BASE = "wss://fstream.binance.com/ws"
USER_WS_RECONNECT_BASE_SEC = 1.0
USER_WS_RECONNECT_MAX_SEC = 30.0


class BinanceUserStream:
    """守护线程内运行 asyncio 事件循环，推送账户订单 / 持仓事件。"""

    def __init__(
        self,
        *,
        use_proxy: bool,
        proxy_host: str,
        proxy_port: int,
        get_listen_key: Callable[[], str | None],
        on_order_update: Callable[[dict], None],
        on_account_update: Callable[[dict], None],
        on_state: Callable[[str], None] | None = None,
    ) -> None:
        self._use_proxy = use_proxy
        self._proxy_host = proxy_host
        self._proxy_port = proxy_port
        self._get_listen_key = get_listen_key
        self._on_order_update = on_order_update
        self._on_account_update = on_account_update
        self._on_state = on_state or (lambda _s: None)
        # 独立 stop_event，绝不与连接器共享，避免误停其他线程
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._task: asyncio.Task | None = None
        self._last_message_at: float = 0.0
        self._connected = False
        self._session_connected = False

    @property
    def last_message_at(self) -> float:
        return self._last_message_at

    @property
    def is_connected(self) -> bool:
        return self._connected

    def is_active(self) -> bool:
        """是否已建立连接：账户流可能长时间无事件（无订单/无变动），
        因此用连接状态而非"最近消息时间"判活；socket 由 ping/pong 探活。"""
        return self._connected

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event = threading.Event()
        self._thread = threading.Thread(
            target=self._run_loop,
            daemon=True,
            name="binance-user-stream",
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        loop = self._loop
        task = self._task
        if loop is not None and task is not None:
            try:
                loop.call_soon_threadsafe(task.cancel)
            except RuntimeError:
                pass
        thread = self._thread
        if thread is not None and thread.is_alive() and threading.current_thread() is not thread:
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

    def _proxy_url(self) -> str | None:
        if not self._use_proxy:
            return None
        host, port, _ = resolve_http_proxy(self._proxy_host, self._proxy_port)
        return f"http://{host}:{port}"

    async def _main(self) -> None:
        backoff = USER_WS_RECONNECT_BASE_SEC
        while not self._stop_event.is_set():
            self._on_state("connecting")
            self._session_connected = False
            # 每次（重）连前取一把（新）listenKey：申请失败或刚过期都能自愈。
            # 回调内部走连接器串行 REST，可能短暂阻塞，对独立线程可接受。
            listen_key: str | None = None
            try:
                listen_key = self._get_listen_key()
            except Exception:
                listen_key = None
            if self._stop_event.is_set():
                break
            if not listen_key:
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, USER_WS_RECONNECT_MAX_SEC)
                continue
            try:
                await self._listen_once(listen_key)
            except asyncio.CancelledError:
                break
            except Exception:
                pass
            self._last_message_at = 0.0
            self._set_connected(False)
            if self._stop_event.is_set():
                break
            if self._session_connected:
                # 曾成功连上又掉线：最短间隔重连
                backoff = USER_WS_RECONNECT_BASE_SEC
            await asyncio.sleep(backoff)
            if not self._session_connected:
                backoff = min(backoff * 2, USER_WS_RECONNECT_MAX_SEC)

    async def _listen_once(self, listen_key: str) -> None:
        url = f"{FUTURES_USER_WS_BASE}/{listen_key}"
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
                    # 账户静默期保持连接：socket 由 ping/pong 维持探活。
                    continue
                if not self._handle_message(raw):
                    # listenKey 失效：断开本次连接，回到 _main 取新 key 重连
                    return

    def _handle_message(self, raw: str | bytes) -> bool:
        """处理一条账户事件；返回 False 表示需要重连（如 listenKey 过期）。"""
        try:
            payload = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return True
        if not isinstance(payload, dict):
            return True

        self._last_message_at = time.time()
        event = str(payload.get("e", "") or "")
        if event == "ORDER_TRADE_UPDATE":
            try:
                self._on_order_update(payload)
            except Exception:
                pass
        elif event == "ACCOUNT_UPDATE":
            try:
                self._on_account_update(payload)
            except Exception:
                pass
        elif event == "listenKeyExpired":
            return False
        return True
