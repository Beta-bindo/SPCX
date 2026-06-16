"""币安（U 本位合约）连接器。

封装与 Binance Futures 的全部交互：行情轮询、盘口深度、持仓查询（带缓存）、
杠杆设置，以及对冲单腿的开/平仓（支持市价 / 限价 / Maker-only，含限价等待成交与撤单）。
未配置实盘或缺少 SDK 时退化为内置模拟行情，保证 UI 可离线演示。

线程模型：行情/持仓轮询在后台线程 `_poll_loop` 进行，结果通过 Qt 信号回主线程；
持仓查询用 TTL 缓存 + 单飞锁（_positions_fetch_lock）避免重复请求。
"""

from __future__ import annotations

import math
import random
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Optional

import requests
from PySide6.QtCore import QMetaObject, QObject, Qt, QTimer, Signal, Slot

from app.core.api_client import ApiClient
from app.core.http_session import (
    configure_requests_session,
    is_transient_network_error,
    run_with_network_retry,
)
from app.core.ssl_certs import ensure_ca_bundle
from app.core.system_proxy import resolve_http_proxy
from app.core.exchange_utils import (
    format_binance_price,
    format_binance_qty,
    get_binance_lot_step,
    get_binance_price_tick,
    get_binance_symbol_meta,
    translate_exchange_error,
)
from app.core.models import AccountSnapshot, AppConfig, ConnectionState, GoldOrderMode, OpenOrder, OrderBook, OrderBookLevel, Position, Quote, Side
from app.core.order_mode import resolve_execution_flags
from app.core.demo_market import demo_tick_time, generate_all_demo_pairs
from app.core.symbols import WATCHED_PRESETS, find_preset, resolve_symbols, watched_ba_symbols
from app.core.trade_result import LegResult
from app.connectors.binance_ws_stream import BinanceWsStream, WS_STALE_SEC
from app.connectors.binance_user_stream import BinanceUserStream
from app.core.app_log import (
    LogLevel,
    hedge_action_label,
    hedge_mode_word,
    should_log,
    trade_leg_success_msg,
)

import importlib.util

# binance 包的 __init__ 较重（~270ms）。启动阶段不需要它，改为首次连实盘时
# 在后台线程懒加载，避免拖慢窗口显示。这里仅用 find_spec 探测是否可用，不触发加载。
HAS_BINANCE = importlib.util.find_spec("binance") is not None

# listenKey 有效期约 60 分钟，每 30 分钟续期一次（留足余量）
LISTEN_KEY_KEEPALIVE_SEC = 30 * 60

# 订单簿多档（@depth20）WS 推送频率：500ms 一次快照，足够流畅且消息量适中
BA_DEPTH_WS_MS = 500


@dataclass
class _OrderStreamState:
    """User Data Stream 累积的单个委托成交状态（按 orderId 维护）。"""

    symbol: str = ""
    executed_qty: float = 0.0   # 累计已成交量（ORDER_TRADE_UPDATE 的 z 字段）
    avg_price: float = 0.0      # 成交均价（ap 字段）
    status: str = ""            # 委托状态（X 字段：NEW/PARTIALLY_FILLED/FILLED/...）
    updated_at: float = field(default_factory=time.monotonic)


class _BinanceNotLoaded(Exception):
    """binance SDK 尚未加载时的占位异常，确保 except 子句始终是合法异常类。"""


Client = None
BinanceAPIException: type[BaseException] = _BinanceNotLoaded


def _ensure_binance_loaded() -> bool:
    """首次需要时才真正 import binance（其 __init__ 较重），返回是否可用。"""
    global Client, BinanceAPIException
    if not HAS_BINANCE:
        return False
    if Client is None:
        from binance.client import Client as _Client
        from binance.exceptions import BinanceAPIException as _Exc

        Client = _Client
        BinanceAPIException = _Exc
    return True


def _format_ba_connection_error(exc: Exception, config: AppConfig) -> str:
    """把底层连接异常翻译成面向用户、含排障建议的中文提示（多与代理相关）。"""
    msg = str(exc)
    exc_name = type(exc).__name__
    if "Proxy" in exc_name or "proxy" in msg.lower():
        if config.use_proxy:
            return (
                f"BA 代理连接失败（{config.proxy_host}:{config.proxy_port}）。"
                "请确认 Clash/V2Ray 已启动，HTTP 代理端口是否正确（常见 7897 或 7890）"
            )
        return (
            "BA 连接失败：网络走了不可用代理。"
            "请在设置 → 连接 中勾选「启用 HTTP 代理」，填写 127.0.0.1:7897"
        )
    if "Timeout" in exc_name or "timeout" in msg.lower():
        return (
            "BA 连接超时：访问 Binance 可能需要代理。"
            "请在设置 → 连接 中勾选「启用 HTTP 代理」，端口填 Clash 的 HTTP 端口（常见 7897）"
        )
    if is_transient_network_error(exc):
        if config.use_proxy:
            return (
                "BA SSL/代理握手失败（偶发）。"
                f"请确认 Clash 已启动、HTTP 代理端口为 {config.proxy_host}:{config.proxy_port}，"
                "并尝试切换节点后重启监控"
            )
        return (
            "BA SSL 连接失败。若在国内访问 Binance，请在设置 → 连接 中启用 HTTP 代理（127.0.0.1:7897）"
        )
    return f"BA 初始化失败: {exc}"


class BinanceConnector(QObject):
    """币安合约连接器，对外暴露报价/持仓/下单能力，并通过信号通知 UI。"""

    quote_received = Signal(object)   # 收到新报价（Quote）
    state_changed = Signal(str)       # 连接状态变化
    ws_state_changed = Signal(str)    # WS 行情：streaming / rest / connecting / off
    latency_updated = Signal(float)   # 接口往返延迟（ms）
    open_orders_changed = Signal(object)  # 当前存在挂单的 BA 交易对集合（frozenset[str]）
    open_orders_detail = Signal(object)  # 当前 BA 存活委托快照（list[OpenOrder]，带数量）
    order_book_updated = Signal(str)  # 某交易对盘口已更新（symbol），驱动 UI 重绘订单簿
    account_received = Signal(object)  # 账户资金快照（AccountSnapshot）
    log = Signal(str)                 # 日志行

    def __init__(self, config: AppConfig, parent=None):
        super().__init__(parent)
        self.config = config
        self._client: Optional[object] = None
        self._state = ConnectionState.DISCONNECTED
        self._last_latency_ms: float | None = None
        self._order_books: dict[str, OrderBook] = {}
        self._quotes: dict[str, Quote] = {}
        # 保护 _quotes/_order_books：WS 线程、REST 轮询线程、主线程并发读写
        self._book_lock = threading.RLock()
        self._demo_timer: Optional[QTimer] = None
        self._poll_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._api = ApiClient()
        self._demo_positions: dict[str, Position] = {}      # 模拟模式下的虚拟持仓
        self._effective_proxy_host: str | None = None
        self._effective_proxy_port: int | None = None
        self._leverage_applied: dict[str, int] = {}         # 已设置过杠杆的交易对
        self._margin_type_applied: dict[str, str] = {}      # 已设置过保证金模式的交易对
        self._positions_cache: list[Position] = []          # 持仓缓存（含 TTL）
        self._positions_cache_at: float = 0.0
        self._symbol_leverage: dict[str, int] = {}          # 各交易对实际杠杆（来自账户接口）
        self._symbol_leverage_at: float = 0.0               # 杠杆缓存时间（带 TTL，少拉账户接口）
        self._positions_fetch_lock = threading.Lock()       # 持仓拉取单飞锁
        self._positions_inflight: threading.Event | None = None
        self._quote_poll_count = 0
        self._ws_stream: BinanceWsStream | None = None
        self._ws_mode = "off"
        self._ws_mode_lock = threading.Lock()
        self._ws_pending_lock = threading.Lock()
        self._ws_pending_quotes: dict[str, Quote] = {}
        self._ws_coalesce_timer = QTimer(self)
        self._ws_coalesce_timer.setSingleShot(True)
        self._ws_coalesce_timer.setInterval(50)
        self._ws_coalesce_timer.timeout.connect(self._flush_ws_coalesce)
        self._ws_latency_emit_at = 0.0
        self._open_order_symbols: frozenset[str] = frozenset()  # 当前挂单交易对快照
        self._open_orders_cache: list[OpenOrder] = []
        # ---- User Data Stream（账户私有推送）相关 ----
        self._user_stream: BinanceUserStream | None = None
        self._listen_key: str | None = None
        self._listen_key_at: float = 0.0
        # 按 orderId 累积的成交状态 + 条件变量（WS 线程推送、下单线程等待）
        self._order_cond = threading.Condition()
        self._order_states: dict[str, _OrderStreamState] = {}
        # 正在 _stream_wait_fills 中等待的 orderId，prune 时跳过避免误删等待者状态对象
        self._waiting_orders: set[str] = set()
        # 推送驱动的"各交易对存活委托快照"（symbol -> {order_id: OpenOrder}），
        # 用于委托指示灯与带数量明细（替代 REST 轮询）
        self._stream_active_orders: dict[str, dict[str, OpenOrder]] = {}
        self._open_orders_emit_lock = threading.RLock()
        self._manual_cancel_event = threading.Event()       # 手动撤单：中断进行中的 Maker 等待
        # 指示灯是否已用 REST 现存挂单打底（断线重连后需重新打底，置 False）
        self._user_stream_seeded = False
        # ACCOUNT_UPDATE 到达后置脏，poll 循环据此尽快强刷一次持仓
        self._account_dirty = threading.Event()
        # 账户资金快照节流：上次拉取时间（秒）
        self._account_snapshot_at = 0.0

    @property
    def ws_mode(self) -> str:
        with self._ws_mode_lock:
            return self._ws_mode

    @property
    def order_books(self) -> dict[str, OrderBook]:
        with self._book_lock:
            return dict(self._order_books)

    def order_book(self, symbol: str) -> OrderBook:
        with self._book_lock:
            return self._order_books.get(symbol, OrderBook())

    @property
    def quotes(self) -> dict[str, Quote]:
        with self._book_lock:
            return dict(self._quotes)

    def quote(self, symbol: str) -> Quote:
        with self._book_lock:
            return self._quotes.get(symbol, Quote(symbol=symbol))

    @property
    def last_quote(self) -> Quote:
        xau = find_preset("xau").symbol_ba
        with self._book_lock:
            return self._quotes.get(xau, Quote(symbol=xau))

    @property
    def state(self) -> ConnectionState:
        return self._state

    @property
    def latency_ms(self) -> float | None:
        return self._last_latency_ms

    def _record_latency(self, ms: float) -> None:
        self._last_latency_ms = ms
        self.latency_updated.emit(ms)

    def update_config(self, config: AppConfig) -> None:
        """热更新配置；杠杆相关项变化时清空"已设置杠杆"标记以便重设。"""
        if (
            config.ba_leverage != self.config.ba_leverage
            or config.sync_leverage_on_trade != self.config.sync_leverage_on_trade
        ):
            self._leverage_applied.clear()
        if config.ba_margin_type != self.config.ba_margin_type:
            self._margin_type_applied.clear()
        self.config = config
        if self._demo_timer is not None:
            self._demo_timer.setInterval(self._ba_refresh_interval_ms())

    def _log(self, level: LogLevel, message: str) -> None:
        if should_log(self.config.log_level, level):
            self.log.emit(message)

    def _ba_refresh_interval_ms(self) -> int:
        return max(100, int(round(self.config.ba_refresh_interval_sec * 1000)))

    def start(self) -> None:
        """启动连接：实盘且有密钥时拉起后台轮询线程，否则退化为模拟行情。"""
        if self._poll_thread is not None and self._poll_thread.is_alive():
            return
        self._stop_event = threading.Event()
        if not self.config.use_live_ba or not self.config.ba_api_key:
            self._start_demo()
            return
        if not HAS_BINANCE:
            self._log(LogLevel.INFO, "未安装 binance 库，BA 使用模拟行情")
            self._start_demo()
            return
        self._set_state(ConnectionState.CONNECTING)
        self._poll_thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._poll_thread.start()

    def stop(self) -> None:
        """停止轮询/模拟、等待线程退出并清空缓存。"""
        self._stop_event.set()
        self._ws_coalesce_timer.stop()
        with self._ws_pending_lock:
            self._ws_pending_quotes.clear()
        self._stop_ws_stream()
        self._stop_user_stream()
        if self._demo_timer:
            self._demo_timer.stop()
            self._demo_timer = None
        thread = self._poll_thread
        if (
            thread is not None
            and thread.is_alive()
            and threading.current_thread() is not thread
        ):
            thread.join(timeout=0.5)
        self._poll_thread = None
        self._emit_ws_mode("off")
        self._emit_open_orders(frozenset())
        with self._book_lock:
            self._quotes.clear()
            self._order_books.clear()
        self._set_state(ConnectionState.DISCONNECTED)

    def _session(self) -> requests.Session | None:
        if not self._client:
            return None
        return getattr(self._client, "session", None)

    def _run_ba_api(self, fn, *, log_failures: bool = True, priority: bool = False):
        """统一执行币安 API 调用：经 ApiClient 串行化 + 网络重试，并友好化常见错误。

        priority=True 用于下单等关键请求插队；遇到限频(-1003/418)立即停止重试并提示。
        """
        session = self._session()

        def _call():
            if priority:
                return self._api.run_priority(fn)
            return self._api.run(fn)

        try:
            return run_with_network_retry(_call, session=session)
        except Exception as exc:
            if HAS_BINANCE and isinstance(exc, BinanceAPIException):
                code = getattr(exc, "code", None)
                if code in (-1003, 418):
                    self._log(
                        LogLevel.ERROR,
                        f"BA 请求过于频繁 (code={code})，已暂停重试。"
                        "请加大行情刷新间隔(建议≥1.0s)并避免多开客户端",
                    )
                    raise
            if log_failures and is_transient_network_error(exc):
                self._log(
                    LogLevel.DEBUG,
                    "BA 网络/SSL 暂时失败，已自动重试仍不通。"
                    "请检查 Clash 是否运行、代理端口是否为 7897",
                )
            raise

    def _maker_timeout_sec(self) -> float:
        return max(1.0, float(self.config.ba_maker_timeout_sec))

    def _maker_poll_sec(self) -> float:
        return max(0.5, min(1.0, self.config.ba_refresh_interval_sec))

    def _try_cancel_order(self, symbol: str, order_id: str) -> None:
        """尽力撤销委托（失败仅记调试日志，不抛出）。"""
        if not self._client or not order_id:
            return

        def _cancel() -> None:
            self._client.futures_cancel_order(symbol=symbol, orderId=int(order_id))

        try:
            self._run_ba_api(_cancel, log_failures=False)
            self._log(LogLevel.TRADE, f"BA Maker 委托已撤单 · {symbol} #{order_id}")
        except Exception as exc:
            self._log(LogLevel.DEBUG, f"BA 撤单 #{order_id}: {exc}")

    def cancel_all_open_orders(self) -> int:
        """撤销所有受监控交易对的未成交委托，返回成功撤销的委托笔数。

        先置「撤单中断」事件，让正在进行的 Maker 等待（持有 API 锁）尽快中止并撤掉自身挂单，
        从而释放锁让本次撤单真正执行——否则会被 Maker 等待全程持锁饿死、点了没反应。
        直接对所有受监控品种发 cancel-all，不依赖本地缓存的委托集合（避免下单期间缓存陈旧漏撤）。
        """
        if not self.config.use_live_ba or not self._client:
            return 0
        self._manual_cancel_event.set()
        try:
            # 尽力取一次现存挂单仅用于计数/日志（取不到也照常撤）
            try:
                pending = [o for o in self.get_open_orders() if o.remaining_quantity > 0]
            except Exception:
                pending = []
            cancelled = 0
            for symbol in watched_ba_symbols():
                def _cancel(s=symbol) -> None:
                    self._client.futures_cancel_all_open_orders(symbol=s)

                try:
                    self._run_ba_api(_cancel, log_failures=False, priority=True)
                    count = sum(1 for o in pending if o.symbol == symbol)
                    if count:
                        cancelled += count
                        self._log(LogLevel.TRADE, f"BA 已撤销 {symbol} 全部委托（{count} 笔）")
                except Exception as exc:
                    self._log(
                        LogLevel.ERROR,
                        f"BA 撤单失败 {symbol}: {translate_exchange_error(exc)}",
                    )
            # 撤单后清空本地存活委托跟踪并推送空，立即熄灭委托灯/清空明细
            with self._open_orders_emit_lock:
                self._stream_active_orders.clear()
                self._open_orders_cache = []
            self._emit_open_orders(frozenset(), [])
            return cancelled
        finally:
            self._manual_cancel_event.clear()

    def _wait_for_limit_order(
        self,
        symbol: str,
        order_id: str,
        *,
        min_executed: float,
    ) -> bool:
        """等待限价/Maker 委托成交至 min_executed：优先 User Data Stream 推送，
        无推送时回退 REST 轮询。"""
        if self._user_stream_active():
            confirmed, _ = self._stream_wait_fills(
                symbol, order_id, target_qty=min_executed, on_fill_delta=None
            )
            return confirmed
        return self._poll_wait_for_limit_order(symbol, order_id, min_executed=min_executed)

    def _poll_wait_for_limit_order(
        self,
        symbol: str,
        order_id: str,
        *,
        min_executed: float,
    ) -> bool:
        """轮询限价/Maker 委托直到成交或超时；达到最小成交量即视为成功。"""
        timeout = self._maker_timeout_sec()
        poll_sec = self._maker_poll_sec()
        deadline = time.monotonic() + timeout
        oid = int(order_id)
        while time.monotonic() < deadline:
            if self._manual_cancel_event.is_set():
                return False  # 手动撤单中断：交由调用方撤掉本挂单
            def _check() -> bool:
                order = self._client.futures_get_order(symbol=symbol, orderId=oid)
                status = str(order.get("status", "")).upper()
                executed = float(order.get("executedQty", 0) or 0)
                if status == "FILLED" or executed + 1e-9 >= min_executed:
                    return True
                if status in ("CANCELED", "REJECTED", "EXPIRED"):
                    return executed + 1e-9 >= min_executed
                return False

            try:
                if self._run_ba_api(_check, log_failures=False):
                    return True
            except Exception:
                pass
            time.sleep(poll_sec)
        return False

    def _fetch_order_status(self, symbol: str, order_id: str) -> dict:
        """读取单个 BA 委托状态；调用方需在 BA API 执行上下文中使用。"""
        return self._client.futures_get_order(symbol=symbol, orderId=int(order_id))

    @staticmethod
    def _parse_open_order(raw: dict) -> OpenOrder:
        """将 BA futures_get_open_orders 返回项解析为 OpenOrder。"""
        total = float(raw.get("origQty", 0) or 0)
        filled = float(raw.get("executedQty", 0) or 0)
        side_raw = str(raw.get("side", "")).upper()
        if side_raw == "BUY":
            side = Side.BUY
        elif side_raw == "SELL":
            side = Side.SELL
        else:
            side = Side.NONE
        return OpenOrder(
            platform="BA",
            symbol=str(raw.get("symbol", "")),
            order_id=str(raw.get("orderId", "")),
            side=side,
            order_type=str(raw.get("type", "")),
            total_quantity=total,
            filled_quantity=filled,
            remaining_quantity=max(0.0, total - filled),
            price=float(raw.get("price", 0) or 0),
            reduce_only=bool(raw.get("reduceOnly")),
        )

    def get_open_orders(self) -> list[OpenOrder]:
        """查询受监控交易对的全部未成交委托。"""
        if not self.config.use_live_ba or not self._client:
            return []
        if self._api.priority_pending():
            return list(self._open_orders_cache)
        orders: list[OpenOrder] = []
        for symbol in watched_ba_symbols():
            try:
                raw_orders = self._run_ba_api(
                    lambda s=symbol: self._client.futures_get_open_orders(symbol=s),
                    log_failures=False,
                ) or []
            except Exception:
                continue
            for raw in raw_orders:
                orders.append(self._parse_open_order(raw))
        self._open_orders_cache = orders
        return orders

    def _emit_open_orders(
        self, symbols: frozenset[str], detail: list[OpenOrder] | None = None
    ) -> None:
        """挂单变化时通知 UI：symbols 驱动委托指示灯，detail 为带数量的委托快照。

        REST 轮询线程与 User Data Stream 线程都可能调用，故加锁保护快照比较。
        symbols 仅在集合变化时发；detail 只要提供就发（部分成交时集合不变但数量需刷新）。
        """
        with self._open_orders_emit_lock:
            symbols_changed = symbols != self._open_order_symbols
            if symbols_changed:
                self._open_order_symbols = symbols
        if symbols_changed:
            self.open_orders_changed.emit(symbols)
        if detail is not None:
            self.open_orders_detail.emit(list(detail))

    def _collect_stream_orders_locked(self) -> list[OpenOrder]:
        """汇总各交易对的存活委托快照（须持有 _open_orders_emit_lock）。"""
        out: list[OpenOrder] = []
        for bag in self._stream_active_orders.values():
            out.extend(bag.values())
        return out

    def _note_local_pending_order(self, order: OpenOrder) -> None:
        """下出限价/Maker 委托后立即点亮委托灯：把刚下的挂单并入存活委托与 REST 缓存并推送。

        这样委托灯/数量能在下单成功的瞬间反映，不必等 User Data Stream 推送，也不会被
        下单(priority)期间 get_open_orders 返回的旧缓存把灯重新灭掉。后续成交/撤单由
        ORDER_TRADE_UPDATE 或 REST 轮询自然回收。
        """
        if not order.symbol or not order.order_id:
            return
        with self._open_orders_emit_lock:
            bag = self._stream_active_orders.setdefault(order.symbol, {})
            bag[order.order_id] = order
            self._open_orders_cache = [
                o for o in self._open_orders_cache if str(o.order_id) != order.order_id
            ] + [order]
            active = frozenset(
                sym for sym, ids in self._stream_active_orders.items() if ids
            )
            detail = self._collect_stream_orders_locked()
        self._emit_open_orders(active, detail)

    def _poll_open_orders(self, watched: set[str]) -> None:
        """刷新委托指示灯：复用 get_open_orders 结果（REST 兜底，带数量）。"""
        if not self._client:
            return
        orders = self.get_open_orders()
        watched_orders = [o for o in orders if o.symbol in watched]
        active = {o.symbol for o in watched_orders}
        self._emit_open_orders(frozenset(active), watched_orders)

    def _wait_for_limit_order_fills(
        self,
        symbol: str,
        order_id: str,
        *,
        target_qty: float,
        on_fill_delta: Callable[[float], bool] | None = None,
    ) -> tuple[bool, float]:
        """等待 Maker/限价委托成交，按新增成交量回调；返回(是否全成, 总成交量)。

        优先用 User Data Stream 推送的成交（毫秒级触发 Exness 补腿），无推送时
        回退 REST 轮询。"""
        if self._user_stream_active():
            return self._stream_wait_fills(
                symbol, order_id, target_qty=target_qty, on_fill_delta=on_fill_delta
            )
        return self._poll_wait_for_limit_order_fills(
            symbol, order_id, target_qty=target_qty, on_fill_delta=on_fill_delta
        )

    def _poll_wait_for_limit_order_fills(
        self,
        symbol: str,
        order_id: str,
        *,
        target_qty: float,
        on_fill_delta: Callable[[float], bool] | None = None,
    ) -> tuple[bool, float]:
        """轮询 Maker/限价委托，按新增成交量回调；返回(是否全成, 总成交量)。"""
        timeout = self._maker_timeout_sec()
        poll_sec = self._maker_poll_sec()
        deadline = time.monotonic() + timeout
        last_executed = 0.0
        status = ""
        while time.monotonic() < deadline:
            if self._manual_cancel_event.is_set():
                return False, last_executed  # 手动撤单中断
            try:
                order = self._fetch_order_status(symbol, order_id)
            except Exception:
                time.sleep(poll_sec)
                continue
            status = str(order.get("status", "")).upper()
            executed = float(order.get("executedQty", 0) or 0)
            delta = max(0.0, executed - last_executed)
            if delta > 1e-9:
                if on_fill_delta is not None and not on_fill_delta(delta):
                    return False, executed
                last_executed = executed
            if status == "FILLED" or executed + 1e-9 >= target_qty:
                return True, executed
            if status in ("CANCELED", "REJECTED", "EXPIRED"):
                return False, executed
            time.sleep(poll_sec)
        return False, last_executed

    def _stream_wait_fills(
        self,
        symbol: str,
        order_id: str,
        *,
        target_qty: float,
        on_fill_delta: Callable[[float], bool] | None = None,
    ) -> tuple[bool, float]:
        """事件驱动等待委托成交：阻塞在条件变量上等 ORDER_TRADE_UPDATE 推送，
        并以 REST 作低频安全兜底（防止漏推/延迟），返回(是否达到目标, 总成交量)。"""
        timeout = self._maker_timeout_sec()
        safety_interval = self._maker_poll_sec()
        deadline = time.monotonic() + timeout
        st = self._register_order_state(order_id, symbol)
        last_executed = 0.0
        last_safety = time.monotonic()
        # 标记为等待中：prune 不得回收该订单状态对象，否则推送写入新对象、本线程持旧对象将漏唤醒
        with self._order_cond:
            self._waiting_orders.add(order_id)
        try:
            while True:
                if self._manual_cancel_event.is_set():
                    return False, last_executed  # 手动撤单中断：交由调用方撤掉本挂单
                now = time.monotonic()
                remaining = deadline - now
                if remaining <= 0:
                    break
                with self._order_cond:
                    self._order_cond.wait(timeout=min(0.2, remaining))
                    executed = st.executed_qty
                    status = st.status
                # 安全兜底：距上次校验超过一个轮询周期，用 REST 校一次，弥补推送缺口
                now = time.monotonic()
                if now - last_safety >= safety_interval:
                    last_safety = now
                    try:
                        order = self._fetch_order_status(symbol, order_id)
                        rest_exec = float(order.get("executedQty", 0) or 0)
                        rest_status = str(order.get("status", "")).upper()
                        if rest_exec > executed or (rest_status and not status):
                            executed = max(executed, rest_exec)
                            status = rest_status or status
                            with self._order_cond:
                                if executed > st.executed_qty:
                                    st.executed_qty = executed
                                if rest_status:
                                    st.status = rest_status
                    except Exception:
                        pass
                delta = max(0.0, executed - last_executed)
                if delta > 1e-9:
                    if on_fill_delta is not None and not on_fill_delta(delta):
                        return False, executed
                    last_executed = executed
                if status == "FILLED" or executed + 1e-9 >= target_qty:
                    return True, executed
                if status in ("CANCELED", "REJECTED", "EXPIRED"):
                    return False, executed
            return False, last_executed
        finally:
            with self._order_cond:
                self._waiting_orders.discard(order_id)

    def _position_cache_ttl(self) -> float:
        """仅用于合并同一时刻的重复 force 请求，不替代定时拉取。"""
        return 0.25

    def _refresh_positions_from_api(self) -> list[Position]:
        """单飞拉取持仓：并发调用时只让一个线程真正请求，其余等待复用结果。"""
        leader = False
        waiter: threading.Event | None = None
        with self._positions_fetch_lock:
            if self._positions_inflight is not None:
                waiter = self._positions_inflight
            else:
                waiter = threading.Event()
                self._positions_inflight = waiter
                leader = True
        if not leader:
            waiter.wait(timeout=15.0)
            return list(self._positions_cache)
        try:
            positions = self._fetch_live_positions()
            self._positions_cache = positions
            self._positions_cache_at = time.time()
            return list(positions)
        finally:
            waiter.set()
            with self._positions_fetch_lock:
                if self._positions_inflight is waiter:
                    self._positions_inflight = None

    def _invalidate_positions_cache(self) -> None:
        self._positions_cache_at = 0.0

    def _parse_live_positions(
        self,
        raw_rows: list[dict],
        *,
        cross_account_buffer: float | None = None,
        leverage_map: dict[str, int] | None = None,
    ) -> list[Position]:
        """把交易所原始持仓行解析为 Position，并按逐仓/全仓计算爆仓缓冲、记录杠杆。

        注意：V3 positionRisk 接口已不再返回 leverage 字段，需通过 leverage_map
        （来自 futures_account 的 positions）补齐每个交易对的真实杠杆，否则会回退到设置值。
        """
        watched = set(watched_ba_symbols())
        positions: list[Position] = []
        resolved_leverage: dict[str, int] = {}
        for row in raw_rows:
            symbol = str(row.get("symbol", ""))
            if symbol not in watched:
                continue
            lev = int(float(row.get("leverage", 0) or 0))
            if lev <= 0 and leverage_map:
                lev = int(leverage_map.get(symbol, 0) or 0)
            if lev <= 0:
                lev = int(self._symbol_leverage.get(symbol, 0) or 0)
            if lev > 0:
                resolved_leverage[symbol] = lev
            amount = float(row.get("positionAmt", 0))
            if amount == 0:
                continue
            side = Side.BUY if amount > 0 else Side.SELL
            unrealized = float(row.get("unRealizedProfit", 0) or 0)
            liq_price = float(row.get("liquidationPrice", 0) or 0)
            mark = float(row.get("markPrice", 0) or 0)
            margin_type = str(row.get("marginType", "") or "").lower()
            maint = float(row.get("maintMargin", 0) or 0)
            wallet = float(row.get("isolatedWallet", 0) or 0)
            exchange_buffer: float | None = None
            if margin_type == "isolated" and wallet > 0:
                from app.core.liquidation import ba_isolated_liq_buffer

                exchange_buffer = ba_isolated_liq_buffer(wallet, unrealized, maint)
            elif cross_account_buffer is not None:
                exchange_buffer = cross_account_buffer
            positions.append(
                Position(
                    platform="BA",
                    symbol=symbol,
                    side=side,
                    quantity=abs(amount),
                    entry_price=float(row.get("entryPrice", 0)),
                    unrealized_pnl=unrealized,
                    liquidation_price=liq_price,
                    mark_price=mark,
                    leverage=lev,
                    margin_type=margin_type,
                    exchange_liq_buffer=exchange_buffer,
                )
            )
        if resolved_leverage:
            self._symbol_leverage.update(resolved_leverage)
        return positions

    def _fetch_live_positions(self) -> list[Position]:
        """实际调用接口拉取持仓；全仓持仓额外查账户以得到全仓爆仓缓冲。"""
        watched = set(watched_ba_symbols())

        def _fetch() -> list[Position]:
            rows = self._client.futures_position_information()
            if watched:
                rows = [row for row in rows if str(row.get("symbol", "")) in watched]
            cross_buffer: float | None = None
            leverage_map: dict[str, int] | None = None
            margin_types = {
                str(row.get("marginType", "") or "").lower()
                for row in rows
                if float(row.get("positionAmt", 0) or 0) != 0
            }
            need_cross = margin_types == {"cross"} or (
                margin_types and "isolated" not in margin_types
            )
            # V3 positionRisk 不再返回 leverage：杠杆缓存过期(或全仓需账户算缓冲)时拉一次账户，
            # 顺带把每个交易对的真实杠杆补齐，避免每个轮询都打账户接口。
            lev_stale = (time.time() - self._symbol_leverage_at) > 30.0
            if need_cross or lev_stale:
                try:
                    from app.core.liquidation import ba_cross_account_liq_buffer

                    account = self._client.futures_account()
                    leverage_map = self._extract_account_leverage(account)
                    if need_cross:
                        cross_buffer = ba_cross_account_liq_buffer(
                            float(account.get("totalMarginBalance", 0) or 0),
                            float(account.get("totalMaintMargin", 0) or 0),
                        )
                except Exception:
                    leverage_map = None
                    cross_buffer = None
            return self._parse_live_positions(
                rows, cross_account_buffer=cross_buffer, leverage_map=leverage_map
            )

        return self._run_ba_api(_fetch, log_failures=False)

    def _extract_account_leverage(self, account: dict) -> dict[str, int]:
        """从 futures_account 的 positions 提取各交易对真实杠杆并刷新缓存。"""
        watched = set(watched_ba_symbols())
        result: dict[str, int] = {}
        for p in account.get("positions", []) or []:
            sym = str(p.get("symbol", ""))
            if watched and sym not in watched:
                continue
            lev = int(float(p.get("leverage", 0) or 0))
            if lev > 0:
                result[sym] = lev
        if result:
            self._symbol_leverage.update(result)
            self._symbol_leverage_at = time.time()
        return result

    def fetch_account_snapshot(self) -> AccountSnapshot | None:
        """拉取合约账户资金快照（余额/已用保证金/可用保证金）。

        仅实盘有效；模拟或未连接返回标记 is_live=False 的占位快照。
        """
        if not self.config.use_live_ba or not self._client:
            return AccountSnapshot(platform="BA", currency="USDT", is_live=False)

        def _fetch() -> AccountSnapshot:
            account = self._client.futures_account()
            # 顺带刷新各交易对真实杠杆缓存（V3 持仓接口不再带 leverage）
            try:
                self._extract_account_leverage(account)
            except Exception:
                pass
            balance = float(account.get("totalWalletBalance", 0) or 0)
            used = float(account.get("totalInitialMargin", 0) or 0)
            free = float(account.get("availableBalance", 0) or 0)
            equity = float(account.get("totalMarginBalance", 0) or 0)
            # 现货钱包 USDT 余额（best-effort：API Key 无现货读权限时回退 0）
            cash = 0.0
            try:
                spot = self._client.get_asset_balance(asset="USDT")
                if spot:
                    cash = float(spot.get("free", 0) or 0) + float(spot.get("locked", 0) or 0)
            except Exception:
                cash = 0.0
            return AccountSnapshot(
                platform="BA",
                balance=balance,
                used_margin=used,
                free_margin=free,
                equity=equity,
                cash_balance=cash,
                currency="USDT",
                is_live=True,
                timestamp=time.time(),
            )

        try:
            snap = self._run_ba_api(_fetch, log_failures=False)
        except Exception as exc:
            self._log(LogLevel.DEBUG, f"BA 账户资金读取失败: {exc}")
            return None
        if snap is not None:
            self._account_snapshot_at = time.monotonic()
            self.account_received.emit(snap)
        return snap

    _BA_REBATE_INCOME_TYPES = ("COMMISSION_REBATE", "API_REBATE", "FEE_RETURN")

    def _fetch_income_sum(
        self,
        symbol: str,
        income_types: tuple[str, ...],
        start_ms: int,
        end_ms: int,
    ) -> float:
        if not self._client:
            return 0.0
        total = 0.0
        for income_type in income_types:
            cursor = start_ms
            while cursor < end_ms:
                rows = self._client.futures_income_history(
                    symbol=symbol,
                    incomeType=income_type,
                    startTime=cursor,
                    endTime=end_ms,
                    limit=1000,
                )
                if not rows:
                    break
                for row in rows:
                    total += float(row.get("income", 0) or 0)
                if len(rows) < 1000:
                    break
                cursor = int(rows[-1].get("time", cursor)) + 1
        return round(total, 4)

    def fetch_funding_income(self, symbol: str, start_ms: int, end_ms: int) -> float:
        """拉取指定时段内 BA 合约资金费合计（币安 FUNDING_FEE，负=支出，正=收入）。"""
        if not self.config.use_live_ba or not self._client:
            return 0.0
        if start_ms >= end_ms:
            return 0.0

        def _fetch() -> float:
            return self._fetch_income_sum(symbol, ("FUNDING_FEE",), start_ms, end_ms)

        try:
            result = self._run_ba_api(_fetch, log_failures=False)
        except Exception:
            return 0.0
        return float(result or 0.0)

    def fetch_rebate_income(self, symbol: str, start_ms: int, end_ms: int) -> float:
        """拉取指定时段内 BA 合约返佣合计（COMMISSION_REBATE / API_REBATE / FEE_RETURN）。"""
        if not self.config.use_live_ba or not self._client:
            return 0.0
        if start_ms >= end_ms:
            return 0.0

        def _fetch() -> float:
            return self._fetch_income_sum(symbol, self._BA_REBATE_INCOME_TYPES, start_ms, end_ms)

        try:
            result = self._run_ba_api(_fetch, log_failures=False)
        except Exception:
            return 0.0
        return float(result or 0.0)

    def transfer_spot_futures(self, amount: float, to_futures: bool) -> tuple[bool, str]:
        """现货钱包 ↔ U 本位合约钱包划转（USDT）。

        to_futures=True：现货→合约（余额划入保证金）；False：合约→现货（保证金转出余额）。
        通过 Universal Transfer 接口（MAIN_UMFUTURE / UMFUTURE_MAIN）实现，下单线程外调用。
        """
        amount = round(float(amount), 8)
        if amount <= 0:
            return False, "划转金额必须大于 0"
        if not self.config.use_live_ba or not self._client:
            return False, "BA 未实盘连接，无法划转"
        transfer_type = "MAIN_UMFUTURE" if to_futures else "UMFUTURE_MAIN"

        def _do():
            fn = getattr(self._client, "universal_transfer", None)
            if fn is None:
                raise RuntimeError("当前 binance SDK 不支持 universal_transfer")
            return fn(type=transfer_type, asset="USDT", amount=amount)

        try:
            self._run_ba_api(_do, priority=True)
        except Exception as exc:
            msg = getattr(exc, "message", None) or str(exc)
            self._log(LogLevel.ERROR, f"BA 划转失败: {msg}")
            return False, str(msg)
        direction = "现货→合约" if to_futures else "合约→现货"
        self._log(LogLevel.INFO, f"BA 划转成功 · {direction} · {amount} USDT")
        # 划转后尽快刷新资金展示
        self._account_snapshot_at = 0.0
        self._account_dirty.set()
        return True, f"划转成功：{direction} {amount} USDT"

    def change_position_margin(
        self, symbol: str, amount: float, add: bool
    ) -> tuple[bool, str]:
        """逐仓持仓加/减保证金（USDT）。

        add=True：合约钱包→持仓（添加保证金，type=1）；
        add=False：持仓→合约钱包（减少保证金，type=2）。
        仅对「逐仓」持仓有效；全仓持仓或无持仓时交易所会返回错误，原样上抛提示。
        """
        amount = round(float(amount), 8)
        if amount <= 0:
            return False, "保证金金额必须大于 0"
        if not symbol:
            return False, "请选择品种"
        if not self.config.use_live_ba or not self._client:
            return False, "BA 未实盘连接，无法调整保证金"
        margin_type = 1 if add else 2

        def _do():
            return self._client.futures_change_position_margin(
                symbol=symbol, amount=amount, type=margin_type
            )

        try:
            self._run_ba_api(_do, priority=True)
        except Exception as exc:
            msg = getattr(exc, "message", None) or str(exc)
            self._log(LogLevel.ERROR, f"BA 调整持仓保证金失败 · {symbol}: {msg}")
            return False, str(msg)
        action = "添加保证金" if add else "减少保证金"
        self._log(LogLevel.INFO, f"BA {action}成功 · {symbol} · {amount} USDT")
        self._account_snapshot_at = 0.0
        self._account_dirty.set()
        self._invalidate_positions_cache()
        return True, f"{action}成功：{symbol} {amount} USDT"

    def _position_from_cache(
        self, symbol: str, side: Side | None = None, min_qty: float = 0.0
    ) -> Position | None:
        """从缓存中查找匹配交易对（可选方向/最小数量）的持仓。"""
        for pos in self._positions_cache:
            if pos.symbol != symbol:
                continue
            if side is not None and pos.side != side:
                continue
            if pos.quantity + 1e-12 < min_qty:
                continue
            return pos
        return None

    def _wait_for_live_position(
        self,
        symbol: str,
        side: Side,
        min_qty: float,
        *,
        timeout: float = 5.0,
        poll_sec: float = 0.25,
    ) -> bool:
        """轮询等待出现 ≥min_qty 的指定方向持仓（确认开仓已落地）。"""
        deadline = time.monotonic() + timeout
        poll_sec = max(poll_sec, self._maker_poll_sec())
        while time.monotonic() < deadline:
            try:
                self.get_positions(force=True)
            except Exception:
                pass
            if self._position_from_cache(symbol, side, min_qty):
                return True
            time.sleep(poll_sec)
        return False

    def _wait_until_flat(self, symbol: str, *, timeout: float = 5.0) -> bool:
        """轮询等待该交易对持仓清零（确认平仓完成）。"""
        deadline = time.monotonic() + timeout
        poll_sec = self._maker_poll_sec()
        while time.monotonic() < deadline:
            try:
                self.get_positions(force=True)
            except Exception:
                pass
            if self._position_from_cache(symbol) is None:
                return True
            time.sleep(poll_sec)
        return False

    def _wait_until_position_at_most(
        self,
        symbol: str,
        side: Side | None,
        max_qty: float,
        *,
        timeout: float = 5.0,
    ) -> bool:
        """轮询等待持仓数量降到 ≤max_qty（确认部分平仓到位）。"""
        deadline = time.monotonic() + timeout
        poll_sec = self._maker_poll_sec()
        while time.monotonic() < deadline:
            try:
                self.get_positions(force=True)
            except Exception:
                pass
            pos = self._position_from_cache(symbol, side)
            if pos is None:
                return max_qty <= 0
            if pos.quantity <= max_qty + 1e-9:
                return True
            time.sleep(poll_sec)
        return False

    def get_positions(
        self, *, force: bool = False, max_stale_sec: float | None = None
    ) -> list[Position]:
        """force=False 返回上次接口快照（供本地盈亏计算）；force=True 合并拉取全品种持仓。"""
        if not self.config.use_live_ba:
            return list(self._demo_positions.values())
        if not self._client:
            return []
        if not force:
            return list(self._positions_cache)
        now = time.time()
        stale_limit = (
            max_stale_sec if max_stale_sec is not None else self._position_cache_ttl()
        )
        if self._positions_cache and now - self._positions_cache_at < stale_limit:
            return list(self._positions_cache)
        try:
            return self._refresh_positions_from_api()
        except Exception as exc:
            if HAS_BINANCE and isinstance(exc, BinanceAPIException):
                code = getattr(exc, "code", None)
                if code in (-1003, 418):
                    self._log(LogLevel.ERROR, f"BA 持仓查询限频 (code={code})")
            elif is_transient_network_error(exc):
                self._log(
                    LogLevel.DEBUG,
                    "BA 持仓查询 SSL/代理失败。"
                    "请确认 Clash 已启动且 HTTP 代理端口为 7897，或切换节点后重试",
                )
            else:
                self._log(LogLevel.ERROR, f"BA 持仓查询失败: {exc}")
            if self._positions_cache:
                return list(self._positions_cache)
            return []

    def replace_demo_positions(self, positions: list[Position]) -> None:
        """覆盖模拟模式下的虚拟持仓（由模拟成交逻辑维护）。"""
        self._demo_positions = {p.symbol: p for p in positions}

    def seed_positions_cache(self, positions: list[Position]) -> None:
        """下单前置：用 UI 已有快照预热缓存，省去开/平仓前的一次实时拉取。"""
        if not self.config.use_live_ba:
            return
        with self._positions_fetch_lock:
            self._positions_cache = list(positions)
            self._positions_cache_at = time.time()
            for pos in positions:
                if pos.leverage:
                    self._symbol_leverage[pos.symbol] = int(pos.leverage)

    def open_hedge_leg(
        self,
        preset_id: str,
        mode: str = "contraction",
        order_mode: str = GoldOrderMode.LIMIT.value,
        *,
        on_fill_delta: Callable[[float], bool] | None = None,
    ) -> LegResult:
        """在 BA 端开/加一腿对冲仓。

        收缩 → 卖出（SELL），扩张 → 买入（BUY）。模拟模式直接更新虚拟持仓；
        实盘按市价/限价/Maker 下单，限价单等待成交、超时撤单，并通过复查持仓确认成交，
        状态不明时返回 needs_reconciliation 交由上层回滚。
        """
        from app.core.models import HedgeMode

        symbol_ba, _, _ = resolve_symbols(
            preset_id, self.config.symbol_ba, self.config.symbol_mt5
        )
        qty = self.config.ba_quantity_for(preset_id)
        with self._book_lock:
            quote = self._quotes.get(symbol_ba, Quote(symbol=symbol_ba))
        use_limit, maker_only = resolve_execution_flags(preset_id, order_mode)
        ba_side = Side.SELL if mode == HedgeMode.CONTRACTION.value else Side.BUY
        adding = False

        if not self.config.use_live_ba:
            price = (quote.bid or quote.mid) if ba_side == Side.BUY else (quote.ask or quote.mid)
            adding = symbol_ba in self._demo_positions
            if adding:
                existing = self._demo_positions[symbol_ba]
                if existing.side != ba_side:
                    return LegResult(
                        platform="BA",
                        success=False,
                        message="BA 持仓方向与本次开仓不一致",
                    )
                total_qty = existing.quantity + qty
                existing.entry_price = (
                    existing.entry_price * existing.quantity + price * qty
                ) / total_qty
                existing.quantity = total_qty
            else:
                self._demo_positions[symbol_ba] = Position(
                    platform="BA",
                    symbol=symbol_ba,
                    side=ba_side,
                    quantity=qty,
                    entry_price=price,
                )
            if not use_limit:
                order_mode_text = "市价"
            elif maker_only:
                order_mode_text = "Maker"
            else:
                order_mode_text = "限价"
            self._log(
                LogLevel.TRADE,
                trade_leg_success_msg(
                    "BA",
                    "open",
                    mode,
                    "demo-ba",
                    adding=adding,
                    qty=str(qty),
                    price=f"{price:.3f}",
                    order_type=order_mode_text,
                ),
            )
            # 演示 Maker/限价同样需驱动 Exness 对冲：按成交量回调一次，
            # 否则上层 mt5_legs 为空会被判为"BA 未成交"导致部分成功+回滚循环。
            if on_fill_delta is not None and qty > 0:
                on_fill_delta(float(qty))
            msg = "演示加仓成功" if adding else "演示开仓成功"
            return LegResult(
                platform="BA",
                success=True,
                message=msg,
                order_id="demo-ba",
                filled_quantity=float(qty),
            )

        if not self._client:
            return LegResult(platform="BA", success=False, message="BA 未连接")

        order_side = "SELL" if ba_side == Side.SELL else "BUY"
        try:
            self.get_positions(force=False)
        except Exception:
            pass
        before_open = self._position_from_cache(symbol_ba, ba_side)
        adding = before_open is not None and before_open.quantity > 0

        def _open() -> LegResult:
            self._apply_margin_type(symbol_ba)
            if self.config.sync_leverage_on_trade:
                self._apply_leverage(symbol_ba)
            step = get_binance_lot_step(self._client, symbol_ba)
            quantity = format_binance_qty(qty, step)
            before = self._position_from_cache(symbol_ba, ba_side)
            target_qty = (before.quantity if before else 0.0) + float(quantity)
            if use_limit:
                tick = get_binance_price_tick(self._client, symbol_ba)
                px = quote.bid if ba_side == Side.BUY else quote.ask
                price = format_binance_price(px or quote.mid, tick)
                time_in_force = "GTX" if maker_only else "GTC"
                order = self._client.futures_create_order(
                    symbol=symbol_ba,
                    side=order_side,
                    type="LIMIT",
                    timeInForce=time_in_force,
                    price=price,
                    quantity=quantity,
                )
            else:
                order = self._client.futures_create_order(
                    symbol=symbol_ba,
                    side=order_side,
                    type="MARKET",
                    quantity=quantity,
                    newOrderRespType="RESULT",
                )
            oid = str(order.get("orderId", ""))
            filled_qty = 0.0
            if use_limit:
                # 委托刚挂上即点亮委托灯（带数量），不等推送/轮询
                self._note_local_pending_order(
                    OpenOrder(
                        platform="BA",
                        symbol=symbol_ba,
                        order_id=oid,
                        side=ba_side,
                        order_type="LIMIT",
                        total_quantity=float(quantity),
                        filled_quantity=0.0,
                        remaining_quantity=float(quantity),
                        price=float(price or 0),
                        reduce_only=False,
                    )
                )
                confirmed, filled_qty = self._wait_for_limit_order_fills(
                    symbol_ba,
                    oid,
                    target_qty=float(quantity),
                    on_fill_delta=on_fill_delta,
                )
                if not confirmed:
                    self._try_cancel_order(symbol_ba, oid)
                    try:
                        order = self._fetch_order_status(symbol_ba, oid)
                        executed_after_cancel = float(order.get("executedQty", 0) or 0)
                    except Exception:
                        executed_after_cancel = filled_qty
                    final_delta = max(0.0, executed_after_cancel - filled_qty)
                    if final_delta > 1e-9:
                        if on_fill_delta is not None and not on_fill_delta(final_delta):
                            return LegResult(
                                platform="BA",
                                success=False,
                                message=f"BA Maker 部分成交 #{oid}，Exness 补对冲失败",
                                order_id=oid,
                                filled_quantity=executed_after_cancel,
                                needs_reconciliation=True,
                            )
                        filled_qty = executed_after_cancel
                    try:
                        self.get_positions(force=True)
                    except Exception:
                        pass
                    if filled_qty > 1e-9:
                        return LegResult(
                            platform="BA",
                            success=True,
                            message=f"BA Maker 部分成交 {filled_qty:g}/{quantity}，已按成交量补 Exness",
                            order_id=oid,
                            filled_quantity=filled_qty,
                        )
                    return LegResult(
                        platform="BA",
                        success=False,
                        message=(
                            f"BA Maker {self._maker_timeout_sec():.0f}s 未成交已撤单"
                        ),
                        order_id=oid,
                    )
            else:
                executed = float(order.get("executedQty", 0) or 0)
                cum_quote = float(order.get("cumQuote", 0) or 0)
                status = str(order.get("status", "")).upper()
                if executed + 1e-9 >= float(quantity) or status == "FILLED":
                    confirmed = True
                    filled_qty = executed or float(quantity)
                elif cum_quote > 0 and executed > 0:
                    confirmed = True
                    filled_qty = executed
                else:
                    confirmed = self._wait_for_live_position(
                        symbol_ba,
                        ba_side,
                        target_qty,
                        timeout=2.0,
                        poll_sec=0.08,
                    )
                    filled_qty = float(quantity) if confirmed else 0.0
            if not confirmed:
                return LegResult(
                    platform="BA",
                    success=False,
                    message=f"BA 订单 {oid} 未确认成交，请检查订单/持仓",
                    order_id=oid,
                    needs_reconciliation=True,
                )
            mode_label = ""
            price_str = ""
            if use_limit:
                mode_label = "Maker" if maker_only else "限价"
                price_str = price
            else:
                mode_label = "市价"
            self._log(
                LogLevel.TRADE,
                trade_leg_success_msg(
                    "BA",
                    "open",
                    mode,
                    oid,
                    adding=adding,
                    qty=quantity,
                    price=price_str,
                    order_type=mode_label,
                ),
            )
            return LegResult(
                platform="BA",
                success=True,
                message=f"{'加仓' if adding else '开仓'}{hedge_mode_word(mode)}成功",
                order_id=oid,
                filled_quantity=filled_qty or float(quantity),
            )

        try:
            result = self._run_ba_api(_open, priority=True)
            if result.success:
                self._invalidate_positions_cache()
            return result
        except BinanceAPIException as exc:
            msg = f"BA {hedge_action_label('open', mode, adding=adding)}失败: {translate_exchange_error(exc.message)}"
            self._log(LogLevel.ERROR, msg)
            return LegResult(platform="BA", success=False, message=msg)
        except Exception as exc:
            msg = f"BA {hedge_action_label('open', mode, adding=adding)}失败: {translate_exchange_error(exc)}"
            self._log(LogLevel.ERROR, msg)
            return LegResult(platform="BA", success=False, message=msg)

    def close_hedge_leg(
        self,
        preset_id: str,
        order_mode: str = GoldOrderMode.LIMIT.value,
        mode: str = "contraction",
        *,
        close_all: bool = False,
        qty_override: float | None = None,
    ) -> LegResult:
        """平 BA 端对冲仓（reduceOnly）。close_all=True 全平，否则按单次交易量部分平。

        与开仓对称：限价单等待成交并复查减仓量，未确认则返回 needs_reconciliation。
        qty_override 指定本次要平的数量（用于「加仓失败回滚」时只平掉本次成交的增量，
        避免误平用户原有持仓）；给定时优先于单次交易量，且忽略 close_all。
        """
        symbol_ba, _, _ = resolve_symbols(
            preset_id, self.config.symbol_ba, self.config.symbol_mt5
        )
        use_limit, maker_only = resolve_execution_flags(preset_id, order_mode)
        with self._book_lock:
            quote = self._quotes.get(symbol_ba, Quote(symbol=symbol_ba))
        if qty_override is not None and qty_override > 0:
            trade_qty = float(qty_override)
            close_all = False  # 精确回滚本次增量，绝不全平
        else:
            trade_qty = self.config.ba_quantity_for(preset_id)
        action_label = hedge_action_label("close", mode)

        if not self.config.use_live_ba:
            if symbol_ba not in self._demo_positions:
                return LegResult(platform="BA", success=True, message="演示无持仓")
            demo_pos = self._demo_positions[symbol_ba]
            qty_to_close = demo_pos.quantity if close_all else min(demo_pos.quantity, trade_qty)
            demo_pos.quantity -= qty_to_close
            if demo_pos.quantity <= 1e-9:
                del self._demo_positions[symbol_ba]
            self._log(
                LogLevel.TRADE,
                trade_leg_success_msg(
                    "BA",
                    "close",
                    mode,
                    "demo-ba-close",
                    qty=str(qty_to_close),
                ),
            )
            return LegResult(platform="BA", success=True, message="演示平仓成功", order_id="demo-ba-close")

        positions = [p for p in self.get_positions(force=False) if p.symbol == symbol_ba]
        if not positions:
            try:
                positions = [
                    p
                    for p in self.get_positions(force=True, max_stale_sec=30.0)
                    if p.symbol == symbol_ba
                ]
            except Exception:
                positions = []
        if not positions:
            return LegResult(platform="BA", success=True, message="无 BA 持仓")

        def _close() -> LegResult:
            step = get_binance_lot_step(self._client, symbol_ba)
            tick = get_binance_price_tick(self._client, symbol_ba)
            for pos in positions:
                qty_to_close = pos.quantity if close_all else min(pos.quantity, trade_qty)
                close_side = "BUY" if pos.side == Side.SELL else "SELL"
                quantity = format_binance_qty(qty_to_close, step)
                remaining = max(0.0, pos.quantity - float(quantity))
                if use_limit:
                    px = quote.bid if close_side == "BUY" else quote.ask
                    price = format_binance_price(px or quote.mid, tick)
                    time_in_force = "GTX" if maker_only else "GTC"
                    order = self._client.futures_create_order(
                        symbol=symbol_ba,
                        side=close_side,
                        type="LIMIT",
                        timeInForce=time_in_force,
                        price=price,
                        quantity=quantity,
                        reduceOnly=True,
                    )
                else:
                    order = self._client.futures_create_order(
                        symbol=symbol_ba,
                        side=close_side,
                        type="MARKET",
                        quantity=quantity,
                        reduceOnly=True,
                        newOrderRespType="RESULT",
                    )
                oid = str(order.get("orderId", ""))
                if use_limit:
                    # 平仓委托刚挂上即点亮委托灯（带数量），不等推送/轮询
                    self._note_local_pending_order(
                        OpenOrder(
                            platform="BA",
                            symbol=symbol_ba,
                            order_id=oid,
                            side=Side.BUY if close_side == "BUY" else Side.SELL,
                            order_type="LIMIT",
                            total_quantity=float(quantity),
                            filled_quantity=0.0,
                            remaining_quantity=float(quantity),
                            price=float(price or 0),
                            reduce_only=True,
                        )
                    )
                    confirmed = self._wait_for_limit_order(
                        symbol_ba,
                        oid,
                        min_executed=float(quantity),
                    )
                    if not confirmed:
                        self._try_cancel_order(symbol_ba, oid)
                        try:
                            self.get_positions(force=True)
                        except Exception:
                            pass
                        cur = self._position_from_cache(symbol_ba, pos.side)
                        if cur is not None and cur.quantity > remaining + 1e-9:
                            return LegResult(
                                platform="BA",
                                success=False,
                                message=f"BA Maker 平仓部分成交 #{oid}，请检查持仓",
                                order_id=oid,
                                needs_reconciliation=True,
                            )
                        return LegResult(
                            platform="BA",
                            success=False,
                            message=(
                                f"BA Maker 平仓 {self._maker_timeout_sec():.0f}s "
                                f"未成交已撤单"
                            ),
                            order_id=oid,
                        )
                elif not self._wait_until_position_at_most(symbol_ba, pos.side, remaining):
                    return LegResult(
                        platform="BA",
                        success=False,
                        message=f"BA 平仓订单 {oid} 未确认减仓，请检查持仓",
                        order_id=oid,
                        needs_reconciliation=True,
                    )
                self._log(
                    LogLevel.TRADE,
                    trade_leg_success_msg(
                        "BA",
                        "close",
                        mode,
                        oid,
                        qty=quantity,
                    ),
                )
                return LegResult(
                    platform="BA",
                    success=True,
                    message=f"{action_label}成功",
                    order_id=oid,
                )
            return LegResult(platform="BA", success=False, message="未找到可平仓位")

        try:
            result = self._run_ba_api(_close, priority=True)
            if result.success:
                self._invalidate_positions_cache()
            return result
        except BinanceAPIException as exc:
            msg = f"BA {action_label}失败: {translate_exchange_error(exc.message)}"
            self._log(LogLevel.ERROR, msg)
            return LegResult(platform="BA", success=False, message=msg)
        except Exception as exc:
            msg = f"BA {action_label}失败: {translate_exchange_error(exc)}"
            self._log(LogLevel.ERROR, msg)
            return LegResult(platform="BA", success=False, message=msg)

    def _apply_leverage(self, symbol: str) -> None:
        """按配置为交易对设置杠杆（仅"下单时同步"开启时），已设过则跳过。"""
        if not self.config.sync_leverage_on_trade or not self._client:
            return
        leverage = int(self.config.ba_leverage)
        if self._leverage_applied.get(symbol) == leverage:
            return
        try:
            self._client.futures_change_leverage(symbol=symbol, leverage=leverage)
            self._leverage_applied[symbol] = leverage
            self._log(LogLevel.DEBUG, f"BA 杠杆已设为 {leverage}x · {symbol}")
        except Exception as exc:
            self._log(LogLevel.DEBUG, f"BA 设置杠杆失败: {exc}")

    def _apply_margin_type(self, symbol: str) -> None:
        """按配置为交易对设置保证金模式（全仓/逐仓）；空配置=跟随平台不设置。

        - 已是目标模式时币安返回 -4046（无需修改），按成功处理；
        - 该交易对已有持仓时返回 -4048（有持仓不能改），记录后跳过、不阻断下单。
        """
        target = (self.config.ba_margin_type or "").lower()
        if target not in ("cross", "isolated") or not self._client:
            return
        if self._margin_type_applied.get(symbol) == target:
            return
        margin_type = "CROSSED" if target == "cross" else "ISOLATED"
        label = "全仓" if target == "cross" else "逐仓"
        try:
            self._client.futures_change_margin_type(symbol=symbol, marginType=margin_type)
            self._margin_type_applied[symbol] = target
            self._log(LogLevel.DEBUG, f"BA 保证金模式已设为 {label} · {symbol}")
        except Exception as exc:
            code = getattr(exc, "code", None)
            if code == -4046:  # 已是目标模式，无需修改
                self._margin_type_applied[symbol] = target
                return
            if code == -4048:  # 有持仓不能切换，沿用现有模式
                self._log(
                    LogLevel.INFO,
                    f"BA 保证金模式切换跳过 · {symbol} 有持仓，无法改为{label}，"
                    "请先平掉该品种全部持仓再切换",
                )
                return
            self._log(LogLevel.DEBUG, f"BA 设置保证金模式失败 · {symbol}: {exc}")

    def refresh_platform_leverage(self, symbol: str) -> int | None:
        """读取交易对在平台上的实际杠杆（优先缓存，否则强制拉一次持仓）。"""
        cached = self._symbol_leverage.get(symbol)
        if cached:
            self._leverage_applied[symbol] = cached
            return cached
        if not self._client:
            return None
        try:
            self.get_positions(force=True)
        except Exception:
            return None
        lev = self._symbol_leverage.get(symbol)
        if lev is not None and lev > 0:
            self._leverage_applied[symbol] = lev
        return lev

    def _start_demo(self) -> None:
        """启动模拟行情：用定时器周期性生成黄金/白银的虚拟报价与盘口。"""
        self._emit_ws_mode("off")
        self._emit_open_orders(frozenset())  # 模拟模式无真实挂单
        self.account_received.emit(AccountSnapshot(platform="BA", currency="USDT", is_live=False))
        self._set_state(ConnectionState.SIMULATED)
        self._demo_timer = QTimer(self)
        self._demo_timer.timeout.connect(self._emit_demo_quotes)
        self._demo_timer.start(self._ba_refresh_interval_ms())
        self._emit_demo_quotes()
        self._log(
            LogLevel.DEBUG,
            f"BA 模拟行情 · 黄金 + 白银 · 刷新间隔 {self.config.ba_refresh_interval_sec:.1f}s",
        )

    def _emit_demo_quotes(self) -> None:
        t = demo_tick_time(time.time(), self.config.ba_refresh_interval_sec)
        self._record_latency(random.uniform(3, 12))
        pairs = generate_all_demo_pairs(t)
        for preset_id in WATCHED_PRESETS:
            ba, _ = pairs[preset_id]
            symbol = ba.symbol
            preset = find_preset(preset_id)
            mid = (ba.bid + ba.ask) / 2
            with self._book_lock:
                self._quotes[symbol] = ba
                self._order_books[symbol] = self._build_demo_book(mid, preset_id == "xau")
            self.quote_received.emit(ba)
            self.order_book_updated.emit(symbol)

    def _depth_refresh_every(self) -> int:
        """全深度订单簿刷新频率：约每 3 秒一次，减轻限频。"""
        return max(3, int(round(3.0 / max(0.3, self.config.ba_refresh_interval_sec))))

    def _emit_ws_mode(self, mode: str) -> None:
        with self._ws_mode_lock:
            if self._ws_mode == mode:
                return
            self._ws_mode = mode
        self.ws_state_changed.emit(mode)

    @Slot()
    def _arm_ws_coalesce_timer(self) -> None:
        """主线程：合并 WS 高频推送，避免事件队列打满导致 UI 卡死。"""
        if not self._ws_coalesce_timer.isActive():
            self._ws_coalesce_timer.start(50)

    @Slot()
    def _flush_ws_coalesce(self) -> None:
        with self._ws_pending_lock:
            pending = list(self._ws_pending_quotes.values())
            self._ws_pending_quotes.clear()
        if not pending:
            return
        for q in pending:
            self.quote_received.emit(q)
        now = time.monotonic()
        if now - self._ws_latency_emit_at >= 0.5:
            self._ws_latency_emit_at = now
            self._record_latency(5.0)
        if self._ws_stream is not None and self._ws_stream.is_live():
            self._emit_ws_mode("streaming")

    def _queue_ws_quote(self, q: Quote) -> None:
        with self._ws_pending_lock:
            self._ws_pending_quotes[q.symbol] = q
        QMetaObject.invokeMethod(
            self,
            "_arm_ws_coalesce_timer",
            Qt.ConnectionType.QueuedConnection,
        )

    def _stop_ws_stream(self) -> None:
        stream = self._ws_stream
        self._ws_stream = None
        if stream is not None:
            stream.stop()

    def _start_ws_stream(self, symbols: list[str]) -> None:
        self._stop_ws_stream()
        self._emit_ws_mode("connecting")

        def _on_quote(symbol: str, bid: float, ask: float) -> None:
            q = Quote(
                symbol=symbol,
                bid=bid,
                ask=ask,
                timestamp=time.time(),
                is_simulated=False,
            )
            with self._book_lock:
                self._update_top_of_book(symbol, bid, ask)
                self._quotes[symbol] = q
            self._queue_ws_quote(q)

        def _on_state(_state: str) -> None:
            if self._stop_event.is_set():
                return
            if self._ws_stream is not None and self._ws_stream.is_live():
                self._emit_ws_mode("streaming")
            elif _state == "connecting":
                self._emit_ws_mode("connecting")

        self._ws_stream = BinanceWsStream(
            symbols,
            use_proxy=bool(self.config.use_proxy),
            proxy_host=self.config.proxy_host,
            proxy_port=self.config.proxy_port,
            on_quote=_on_quote,
            on_state=_on_state,
            on_depth=self._on_ws_depth,
            depth_ms=BA_DEPTH_WS_MS,
        )
        self._ws_stream.start()

    def _on_ws_depth(self, symbol: str, bids: list, asks: list) -> None:
        """WS 线程：用 @depth20 推送的完整快照替换订单簿多档（顶档仍由 bookTicker 维护）。"""
        new_book = OrderBook(
            bids=[OrderBookLevel(p, q) for p, q in bids[:10]],
            asks=[OrderBookLevel(p, q) for p, q in asks[:10]],
            is_simulated=False,
        )
        with self._book_lock:
            self._order_books[symbol] = new_book
        self.order_book_updated.emit(symbol)

    def _ws_quotes_live(self) -> bool:
        stream = self._ws_stream
        return stream is not None and stream.is_live(stale_sec=WS_STALE_SEC)

    def _depth_ws_live(self) -> bool:
        stream = self._ws_stream
        return stream is not None and stream.depth_is_live(stale_sec=WS_STALE_SEC)

    # ------------------------------------------------------------------
    # User Data Stream（账户私有推送）：listenKey 生命周期 + 事件处理
    # ------------------------------------------------------------------

    def _create_listen_key(self) -> str | None:
        """申请（或刷新）listenKey；供启动与重连时调用。失败返回 None。"""
        if not self._client:
            return None

        def _do() -> str | None:
            fn = getattr(self._client, "futures_stream_get_listen_key", None)
            if fn is None:
                return None
            return fn()

        try:
            key = self._run_ba_api(_do, log_failures=False)
        except Exception as exc:
            self._log(LogLevel.DEBUG, f"BA listenKey 申请失败: {exc}")
            return None
        if key:
            self._listen_key = str(key)
            self._listen_key_at = time.monotonic()
            return self._listen_key
        return None

    def _keepalive_listen_key(self) -> None:
        """续期 listenKey，避免 60 分钟后自动失效。"""
        if not self._client or not self._listen_key:
            return

        def _do():
            fn = getattr(self._client, "futures_stream_keepalive", None)
            if fn is None:
                return None
            try:
                return fn(listenKey=self._listen_key)
            except TypeError:
                return fn(self._listen_key)

        try:
            self._run_ba_api(_do, log_failures=False)
            self._listen_key_at = time.monotonic()
        except Exception as exc:
            self._log(LogLevel.DEBUG, f"BA listenKey 续期失败: {exc}")

    def _maybe_keepalive_listen_key(self) -> None:
        if not self._user_stream or not self._listen_key:
            return
        if time.monotonic() - self._listen_key_at < LISTEN_KEY_KEEPALIVE_SEC:
            return
        self._keepalive_listen_key()

    def _close_listen_key(self, key: str | None = None) -> None:
        """主动释放 listenKey（停止连接时）。key=None 时释放当前 key 并清空。"""
        target = key if key is not None else self._listen_key
        if key is None:
            self._listen_key = None
        if not self._client or not target:
            return

        def _do():
            for name in ("futures_stream_close", "futures_stream_close_listen_key"):
                fn = getattr(self._client, name, None)
                if fn is None:
                    continue
                try:
                    return fn(listenKey=target)
                except TypeError:
                    return fn(target)
            return None

        try:
            self._run_ba_api(_do, log_failures=False)
        except Exception:
            pass

    def _user_stream_active(self) -> bool:
        """User Data Stream 是否已建立连接（决定走推送还是 REST）。"""
        stream = self._user_stream
        return stream is not None and stream.is_active()

    def _start_user_stream(self) -> None:
        """申请 listenKey 并拉起账户私有推送线程；失败则静默回退 REST。"""
        self._stop_user_stream()
        key = self._create_listen_key()
        if not key:
            self._log(
                LogLevel.DEBUG,
                "BA User Data Stream 未启用（listenKey 申请失败），账户/成交回退 REST 轮询",
            )
            return
        self._user_stream = BinanceUserStream(
            use_proxy=bool(self.config.use_proxy),
            proxy_host=self.config.proxy_host,
            proxy_port=self.config.proxy_port,
            get_listen_key=self._create_listen_key,
            on_order_update=self._on_user_order_update,
            on_account_update=self._on_user_account_update,
        )
        self._user_stream.start()
        self._log(LogLevel.INFO, "BA User Data Stream 已启动 · 成交/持仓实时推送")

    def _stop_user_stream(self) -> None:
        stream = self._user_stream
        self._user_stream = None
        if stream is not None:
            stream.stop()
        # 释放 listenKey 走后台线程，避免网络慢时阻塞"停止监控"/退出
        key = self._listen_key
        self._listen_key = None
        if key and self._client:
            threading.Thread(
                target=self._close_listen_key,
                args=(key,),
                daemon=True,
                name="ba-listenkey-close",
            ).start()
        with self._order_cond:
            self._order_states.clear()
            self._order_cond.notify_all()
        with self._open_orders_emit_lock:
            self._stream_active_orders.clear()
        self._account_dirty.clear()
        self._user_stream_seeded = False

    def _prune_order_states_locked(self) -> None:
        """防止 _order_states 无限增长：超量时丢弃最旧条目（须持有 _order_cond）。
        正在等待成交的订单（_waiting_orders）必须保留，否则等待线程会漏收推送唤醒。"""
        if len(self._order_states) <= 256:
            return
        items = sorted(self._order_states.items(), key=lambda kv: kv[1].updated_at)
        for oid, _st in items:
            if len(self._order_states) <= 128:
                break
            if oid in self._waiting_orders:
                continue
            self._order_states.pop(oid, None)

    def _register_order_state(self, order_id: str, symbol: str) -> _OrderStreamState:
        """注册/取回某委托的成交状态对象（推送可能早于注册，故取已有优先）。"""
        with self._order_cond:
            st = self._order_states.get(order_id)
            if st is None:
                st = _OrderStreamState(symbol=symbol)
                self._order_states[order_id] = st
            return st

    def _on_user_order_update(self, payload: dict) -> None:
        """WS 线程：处理 ORDER_TRADE_UPDATE，更新成交状态并唤醒等待者。"""
        o = payload.get("o") or {}
        oid = str(o.get("i", "") or "")
        if not oid:
            return
        symbol = str(o.get("s", "") or "")
        status = str(o.get("X", "") or "").upper()
        try:
            executed = float(o.get("z", 0) or 0)
            avg = float(o.get("ap", 0) or 0)
        except (TypeError, ValueError):
            return
        with self._order_cond:
            st = self._order_states.get(oid)
            if st is None:
                st = _OrderStreamState(symbol=symbol)
                self._order_states[oid] = st
                self._prune_order_states_locked()
            if executed > st.executed_qty:
                st.executed_qty = executed
            if avg > 0:
                st.avg_price = avg
            st.status = status
            st.updated_at = time.monotonic()
            self._order_cond.notify_all()
        self._update_stream_open_orders(self._parse_stream_order(o), status)

    @staticmethod
    def _parse_stream_order(o: dict) -> OpenOrder:
        """将 ORDER_TRADE_UPDATE 的 o 对象解析为带数量的 OpenOrder。"""
        total = float(o.get("q", 0) or 0)        # 原始委托量
        filled = float(o.get("z", 0) or 0)       # 累计已成交量
        side_raw = str(o.get("S", "")).upper()
        if side_raw == "BUY":
            side = Side.BUY
        elif side_raw == "SELL":
            side = Side.SELL
        else:
            side = Side.NONE
        return OpenOrder(
            platform="BA",
            symbol=str(o.get("s", "")),
            order_id=str(o.get("i", "")),
            side=side,
            order_type=str(o.get("o", "")),
            total_quantity=total,
            filled_quantity=filled,
            remaining_quantity=max(0.0, total - filled),
            price=float(o.get("p", 0) or 0),
            reduce_only=bool(o.get("R", False)),
        )

    def _update_stream_open_orders(self, order: OpenOrder, status: str) -> None:
        """按推送的委托快照维护"存活委托"，驱动委托指示灯与带数量明细。"""
        symbol = order.symbol
        if not symbol:
            return
        with self._open_orders_emit_lock:
            bag = self._stream_active_orders.setdefault(symbol, {})
            if status in ("NEW", "PARTIALLY_FILLED"):
                bag[order.order_id] = order
            else:  # FILLED / CANCELED / EXPIRED / REJECTED 等终态
                bag.pop(order.order_id, None)
            active = frozenset(
                sym for sym, ids in self._stream_active_orders.items() if ids
            )
            detail = self._collect_stream_orders_locked()
        self._emit_open_orders(active, detail)

    def _seed_stream_open_orders(self, watched: set[str]) -> None:
        """启动推送后用一次 REST 现存挂单为指示灯打底（推送只覆盖增量变化）。"""
        try:
            orders = self.get_open_orders()
        except Exception:
            return
        with self._open_orders_emit_lock:
            self._stream_active_orders.clear()
            for o in orders:
                if o.symbol in watched:
                    self._stream_active_orders.setdefault(o.symbol, {})[
                        str(o.order_id)
                    ] = o
            active = frozenset(
                sym for sym, ids in self._stream_active_orders.items() if ids
            )
            detail = self._collect_stream_orders_locked()
        self._emit_open_orders(active, detail)

    def _on_user_account_update(self, payload: dict) -> None:
        """WS 线程：持仓/余额变化，失效缓存并置脏，由 poll 循环尽快强刷。"""
        self._invalidate_positions_cache()
        self._account_dirty.set()

    def _rest_quote_fallback_interval(self) -> float:
        """WS 可用时不轮询；兜底时按配置间隔拉 REST。"""
        return self.config.ba_refresh_interval_sec

    def _update_top_of_book(self, symbol: str, bid: float, ask: float) -> None:
        """用最新买卖一价更新盘口首档（深度未刷新时也能保持顶档准确）。"""
        with self._book_lock:
            book = self._order_books.get(symbol)
            if book and book.bids and book.asks:
                book.bids[0] = OrderBookLevel(bid, book.bids[0].quantity)
                book.asks[0] = OrderBookLevel(ask, book.asks[0].quantity)
                book.is_simulated = False
                return
            self._order_books[symbol] = OrderBook(
                bids=[OrderBookLevel(bid, 1.0)],
                asks=[OrderBookLevel(ask, 1.0)],
                is_simulated=False,
            )

    def _fetch_watched_quotes(self, watched: set[str]) -> list[Quote]:
        """按 symbol 拉 bookTicker（兜底路径，权重低于全市场）。"""
        now = time.time()
        out: list[Quote] = []
        for symbol in sorted(watched):
            raw = self._client.futures_orderbook_ticker(symbol=symbol)
            item = raw if isinstance(raw, dict) else {}
            bid = float(item.get("bidPrice", 0) or 0)
            ask = float(item.get("askPrice", 0) or 0)
            if bid <= 0 or ask <= 0:
                continue
            q = Quote(symbol=symbol, bid=bid, ask=ask, timestamp=now, is_simulated=False)
            with self._book_lock:
                self._update_top_of_book(symbol, bid, ask)
                self._quotes[symbol] = q
            out.append(q)
            self.order_book_updated.emit(symbol)
        return out

    def _fetch_one_depth(self, symbol: str) -> None:
        """拉取单个交易对的 10 档盘口并同步顶档报价。"""
        book = self._client.futures_order_book(symbol=symbol, limit=10)
        new_book = OrderBook(
            bids=[OrderBookLevel(float(p), float(q)) for p, q in book["bids"][:10]],
            asks=[OrderBookLevel(float(p), float(q)) for p, q in book["asks"][:10]],
            is_simulated=False,
        )
        bid = float(book["bids"][0][0])
        ask = float(book["asks"][0][0])
        new_quote = Quote(
            symbol=symbol,
            bid=bid,
            ask=ask,
            timestamp=time.time(),
            is_simulated=False,
        )
        with self._book_lock:
            self._order_books[symbol] = new_book
            self._quotes[symbol] = new_quote
        self.order_book_updated.emit(symbol)

    def _fetch_watched_depths(self, watched: set[str]) -> None:
        """逐个刷新受监控交易对的盘口；有下单等优先请求待处理则让路。"""
        for symbol in watched:
            if self._api.priority_pending():
                break
            self._fetch_one_depth(symbol)

    def _build_demo_book(self, mid: float, is_gold: bool) -> OrderBook:
        """围绕中价生成 10 档模拟盘口（黄金/白银档距不同）。"""
        step = 0.05 if is_gold else 0.002
        bids, asks = [], []
        for i in range(10):
            offset = (i + 1) * step
            bids.append(OrderBookLevel(price=mid - offset, quantity=round(random.uniform(0.5, 8), 2)))
            asks.append(OrderBookLevel(price=mid + offset, quantity=round(random.uniform(0.5, 8), 2)))
        return OrderBook(bids=bids, asks=asks, is_simulated=True)

    def _create_client(self) -> object:
        """创建并配置币安 SDK 客户端（CA 证书、可选 HTTP 代理及兜底）。"""
        _ensure_binance_loaded()  # 后台线程内首次连实盘时才真正加载 binance SDK
        client = Client(
            self.config.ba_api_key,
            self.config.ba_api_secret,
            ping=False,
        )
        verify = ensure_ca_bundle()
        through_proxy = bool(self.config.use_proxy)
        proxies: dict[str, str] = {}
        if through_proxy:
            host, port, fallback = resolve_http_proxy(
                self.config.proxy_host, self.config.proxy_port
            )
            self._effective_proxy_host = host
            self._effective_proxy_port = port
            if fallback:
                self._log(
                    LogLevel.DEBUG,
                    f"BA 代理 {self.config.proxy_host}:{self.config.proxy_port} 不可用，"
                    f"已改用 {host}:{port}",
                )
            proxy_url = f"http://{host}:{port}"
            proxies = {"http": proxy_url, "https": proxy_url}
        else:
            self._effective_proxy_host = None
            self._effective_proxy_port = None
        configure_requests_session(
            client.session,
            verify=verify,
            proxies=proxies,
            through_proxy=through_proxy,
            retry_on_rate_limit=False,
        )
        return client

    def _poll_loop(self) -> None:
        """后台主循环：建连→ping→读取元数据/杠杆→循环拉取报价与盘口直到停止。"""
        try:
            self._client = self._create_client()
            self._run_ba_api(self._client.futures_ping, log_failures=False)
            self._set_state(ConnectionState.CONNECTED)
            symbols = watched_ba_symbols()
            watched = set(symbols)
            self._log(
                LogLevel.INFO,
                f"BA 已连接 · {', '.join(symbols)} · 行情 WebSocket 推流 + REST 兜底",
            )
            self._start_ws_stream(symbols)
            self._start_user_stream()
            for sym in symbols:
                try:
                    self._run_ba_api(
                        lambda s=sym: get_binance_symbol_meta(self._client, s),
                        log_failures=False,
                    )
                except Exception:
                    pass
            if self.config.ba_margin_type:
                for sym in symbols:
                    try:
                        self._run_ba_api(lambda s=sym: self._apply_margin_type(s), log_failures=False)
                    except Exception:
                        pass
            if self.config.sync_leverage_on_trade:
                for sym in symbols:
                    try:
                        self._run_ba_api(lambda s=sym: self._apply_leverage(s), log_failures=False)
                    except Exception:
                        pass
            else:
                try:
                    self.get_positions(force=True)
                    for sym in symbols:
                        lev = self._symbol_leverage.get(sym)
                        if lev:
                            self._leverage_applied[sym] = lev
                            self._log(LogLevel.DEBUG, f"BA 平台杠杆 {lev}x · {sym}")
                except Exception:
                    pass
            depth_every = self._depth_refresh_every()
            rest_sleep = self._rest_quote_fallback_interval()
            while not self._stop_event.is_set():
                ws_live = False
                try:
                    self._quote_poll_count += 1
                    ws_live = self._ws_quotes_live()
                    if ws_live:
                        self._emit_ws_mode("streaming")
                    else:
                        self._emit_ws_mode("rest")
                        def _poll_quotes() -> list[Quote]:
                            return self._fetch_watched_quotes(watched)

                        started = time.perf_counter()
                        quotes = self._run_ba_api(_poll_quotes, log_failures=False)
                        self._record_latency((time.perf_counter() - started) * 1000)
                        for q in quotes:
                            self.quote_received.emit(q)
                        rest_sleep = self._rest_quote_fallback_interval()
                    # WS 深度（@depth20）在线时不再 REST 拉深度；
                    # 仅在无任何盘口打底、或深度 WS 不可用时按 ~3 秒兜底。
                    need_depth = not self._order_books or (
                        not self._depth_ws_live()
                        and self._quote_poll_count % depth_every == 1
                    )
                    if need_depth:
                        for sym in watched:
                            if self._api.priority_pending():
                                break
                            try:
                                self._run_ba_api(
                                    lambda s=sym: self._fetch_one_depth(s),
                                    log_failures=False,
                                )
                            except Exception:
                                pass
                    if self._user_stream_active():
                        # 推送（重）连上后用一次 REST 现存挂单为指示灯打底，
                        # 之后由 ORDER_TRADE_UPDATE 增量维护，避免重连后指示灯漂移。
                        if not self._user_stream_seeded:
                            self._seed_stream_open_orders(watched)
                            self._user_stream_seeded = True
                        # ACCOUNT_UPDATE 置脏时尽快强刷一次持仓与资金（WS 实时）
                        if self._account_dirty.is_set():
                            self._account_dirty.clear()
                            try:
                                self.get_positions(force=True)
                                self.fetch_account_snapshot()
                            except Exception:
                                # 强刷失败则保留脏标记，下一轮继续重试，避免持仓长时间过期
                                self._account_dirty.set()
                        self._maybe_keepalive_listen_key()
                    else:
                        # 推送不可用：回退 REST，每 ~2 秒刷新一次挂单状态（权重很低）
                        self._user_stream_seeded = False
                        if self._quote_poll_count % 2 == 0:
                            self._poll_open_orders(watched)
                    # 资金快照 REST 兜底：无论推送是否可用，至少每 ~5 秒刷新一次
                    if time.monotonic() - self._account_snapshot_at >= 5.0:
                        try:
                            self.fetch_account_snapshot()
                        except Exception:
                            pass
                except BinanceAPIException as exc:
                    if getattr(exc, "code", None) in (-1003, 418):
                        self._log(
                            LogLevel.ERROR,
                            f"BA 限频 code={exc.code}，行情已优先 WebSocket；"
                            "若仍限频请加大 REST 兜底间隔或避免多开客户端",
                        )
                    else:
                        self._log(LogLevel.ERROR, f"BA API 错误: {translate_exchange_error(exc.message)}")
                    self._set_state(ConnectionState.ERROR)
                except Exception as exc:
                    self._log(LogLevel.ERROR, f"BA 连接异常: {exc}")
                    self._set_state(ConnectionState.ERROR)
                if ws_live:
                    time.sleep(max(1.0, rest_sleep))
                else:
                    time.sleep(rest_sleep)
        except Exception as exc:
            self._log(LogLevel.ERROR, _format_ba_connection_error(exc, self.config))
            self._set_state(ConnectionState.ERROR)

    def _set_state(self, state: ConnectionState) -> None:
        """更新连接状态并通知 UI。"""
        self._state = state
        self.state_changed.emit(state.value)
