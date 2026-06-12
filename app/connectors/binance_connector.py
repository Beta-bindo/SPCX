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
from typing import Optional

import requests
from PySide6.QtCore import QObject, QTimer, Signal

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
)
from app.core.models import AppConfig, ConnectionState, GoldOrderMode, OrderBook, OrderBookLevel, Position, Quote, Side
from app.core.order_mode import resolve_execution_flags
from app.core.demo_market import demo_tick_time, generate_all_demo_pairs
from app.core.symbols import WATCHED_PRESETS, find_preset, resolve_symbols, watched_ba_symbols
from app.core.trade_result import LegResult
from app.core.app_log import (
    LogLevel,
    hedge_action_label,
    hedge_mode_word,
    should_log,
    trade_leg_success_msg,
)

try:
    from binance.client import Client
    from binance.exceptions import BinanceAPIException
    HAS_BINANCE = True
except ImportError:
    HAS_BINANCE = False


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
    latency_updated = Signal(float)   # 接口往返延迟（ms）
    log = Signal(str)                 # 日志行

    def __init__(self, config: AppConfig, parent=None):
        super().__init__(parent)
        self.config = config
        self._client: Optional[object] = None
        self._state = ConnectionState.DISCONNECTED
        self._last_latency_ms: float | None = None
        self._order_books: dict[str, OrderBook] = {}
        self._quotes: dict[str, Quote] = {}
        self._demo_timer: Optional[QTimer] = None
        self._poll_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._api = ApiClient()
        self._demo_positions: dict[str, Position] = {}      # 模拟模式下的虚拟持仓
        self._effective_proxy_host: str | None = None
        self._effective_proxy_port: int | None = None
        self._leverage_applied: dict[str, int] = {}         # 已设置过杠杆的交易对
        self._positions_cache: list[Position] = []          # 持仓缓存（含 TTL）
        self._positions_cache_at: float = 0.0
        self._symbol_leverage: dict[str, int] = {}          # 各交易对实际杠杆
        self._positions_fetch_lock = threading.Lock()       # 持仓拉取单飞锁
        self._positions_inflight: threading.Event | None = None
        self._quote_poll_count = 0

    @property
    def order_books(self) -> dict[str, OrderBook]:
        return self._order_books

    def order_book(self, symbol: str) -> OrderBook:
        return self._order_books.get(symbol, OrderBook())

    @property
    def quotes(self) -> dict[str, Quote]:
        return self._quotes

    def quote(self, symbol: str) -> Quote:
        return self._quotes.get(symbol, Quote(symbol=symbol))

    @property
    def last_quote(self) -> Quote:
        xau = find_preset("xau").symbol_ba
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
        self._stop_event.clear()
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
        if self._demo_timer:
            self._demo_timer.stop()
            self._demo_timer = None
        thread = self._poll_thread
        if (
            thread is not None
            and thread.is_alive()
            and threading.current_thread() is not thread
        ):
            thread.join(timeout=2.0)
        self._poll_thread = None
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

    def _wait_for_limit_order(
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
        self, raw_rows: list[dict], *, cross_account_buffer: float | None = None
    ) -> list[Position]:
        """把交易所原始持仓行解析为 Position，并按逐仓/全仓计算爆仓缓冲、记录杠杆。"""
        watched = set(watched_ba_symbols())
        positions: list[Position] = []
        leverage_map: dict[str, int] = {}
        for row in raw_rows:
            symbol = str(row.get("symbol", ""))
            if symbol not in watched:
                continue
            lev = int(float(row.get("leverage", 0) or 0))
            if lev > 0:
                leverage_map[symbol] = lev
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
        self._symbol_leverage.update(leverage_map)
        return positions

    def _fetch_live_positions(self) -> list[Position]:
        """实际调用接口拉取持仓；全仓持仓额外查账户以得到全仓爆仓缓冲。"""
        watched = set(watched_ba_symbols())

        def _fetch() -> list[Position]:
            rows = self._client.futures_position_information()
            if watched:
                rows = [row for row in rows if str(row.get("symbol", "")) in watched]
            cross_buffer: float | None = None
            margin_types = {
                str(row.get("marginType", "") or "").lower()
                for row in rows
                if float(row.get("positionAmt", 0) or 0) != 0
            }
            if margin_types == {"cross"} or (
                margin_types and "isolated" not in margin_types
            ):
                try:
                    from app.core.liquidation import ba_cross_account_liq_buffer

                    account = self._client.futures_account()
                    cross_buffer = ba_cross_account_liq_buffer(
                        float(account.get("totalMarginBalance", 0) or 0),
                        float(account.get("totalMaintMargin", 0) or 0),
                    )
                except Exception:
                    cross_buffer = None
            return self._parse_live_positions(rows, cross_account_buffer=cross_buffer)

        return self._run_ba_api(_fetch, log_failures=False)

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
        self, preset_id: str, mode: str = "contraction", order_mode: str = GoldOrderMode.LIMIT.value
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
            msg = "演示加仓成功" if adding else "演示开仓成功"
            return LegResult(platform="BA", success=True, message=msg, order_id="demo-ba")

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
            if use_limit:
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
                    if self._position_from_cache(symbol_ba, ba_side, target_qty) is not None:
                        return LegResult(
                            platform="BA",
                            success=False,
                            message=f"BA Maker 部分成交 #{oid}，请检查持仓",
                            order_id=oid,
                            needs_reconciliation=True,
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
                elif cum_quote > 0 and executed > 0:
                    confirmed = True
                else:
                    confirmed = self._wait_for_live_position(
                        symbol_ba,
                        ba_side,
                        target_qty,
                        timeout=2.0,
                        poll_sec=0.08,
                    )
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
            )

        try:
            result = self._run_ba_api(_open, priority=True)
            if result.success:
                self._invalidate_positions_cache()
            return result
        except BinanceAPIException as exc:
            msg = f"BA {hedge_action_label('open', mode, adding=adding)}失败: {exc.message}"
            self._log(LogLevel.ERROR, msg)
            return LegResult(platform="BA", success=False, message=msg)
        except Exception as exc:
            msg = f"BA {hedge_action_label('open', mode, adding=adding)}失败: {exc}"
            self._log(LogLevel.ERROR, msg)
            return LegResult(platform="BA", success=False, message=msg)

    def close_hedge_leg(
        self,
        preset_id: str,
        order_mode: str = GoldOrderMode.LIMIT.value,
        mode: str = "contraction",
        *,
        close_all: bool = False,
    ) -> LegResult:
        """平 BA 端对冲仓（reduceOnly）。close_all=True 全平，否则按单次交易量部分平。

        与开仓对称：限价单等待成交并复查减仓量，未确认则返回 needs_reconciliation。
        回滚场景由上层以 close_all=True 调用。
        """
        symbol_ba, _, _ = resolve_symbols(
            preset_id, self.config.symbol_ba, self.config.symbol_mt5
        )
        use_limit, maker_only = resolve_execution_flags(preset_id, order_mode)
        quote = self._quotes.get(symbol_ba, Quote(symbol=symbol_ba))
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
            msg = f"BA {action_label}失败: {exc.message}"
            self._log(LogLevel.ERROR, msg)
            return LegResult(platform="BA", success=False, message=msg)
        except Exception as exc:
            msg = f"BA {action_label}失败: {exc}"
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
            self._quotes[symbol] = ba
            self._order_books[symbol] = self._build_demo_book(mid, preset_id == "xau")
            self.quote_received.emit(ba)

    def _depth_refresh_every(self) -> int:
        """全深度订单簿刷新频率：约每 3 秒一次，减轻限频。"""
        return max(3, int(round(3.0 / max(0.3, self.config.ba_refresh_interval_sec))))

    def _update_top_of_book(self, symbol: str, bid: float, ask: float) -> None:
        """用最新买卖一价更新盘口首档（深度未刷新时也能保持顶档准确）。"""
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
        """一次 bookTicker 拉取全市场，本地筛选黄金/白银。"""
        raw = self._client.futures_orderbook_ticker()
        rows = raw if isinstance(raw, list) else [raw]
        now = time.time()
        out: list[Quote] = []
        for item in rows:
            symbol = str(item.get("symbol", ""))
            if symbol not in watched:
                continue
            bid = float(item.get("bidPrice", 0) or 0)
            ask = float(item.get("askPrice", 0) or 0)
            if bid <= 0 or ask <= 0:
                continue
            self._update_top_of_book(symbol, bid, ask)
            q = Quote(symbol=symbol, bid=bid, ask=ask, timestamp=now, is_simulated=False)
            self._quotes[symbol] = q
            out.append(q)
        return out

    def _fetch_one_depth(self, symbol: str) -> None:
        """拉取单个交易对的 10 档盘口并同步顶档报价。"""
        book = self._client.futures_order_book(symbol=symbol, limit=10)
        self._order_books[symbol] = OrderBook(
            bids=[OrderBookLevel(float(p), float(q)) for p, q in book["bids"][:10]],
            asks=[OrderBookLevel(float(p), float(q)) for p, q in book["asks"][:10]],
            is_simulated=False,
        )
        bid = float(book["bids"][0][0])
        ask = float(book["asks"][0][0])
        self._quotes[symbol] = Quote(
            symbol=symbol,
            bid=bid,
            ask=ask,
            timestamp=time.time(),
            is_simulated=False,
        )

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
                f"BA 已连接 · {', '.join(symbols)} · 刷新间隔 "
                f"{self.config.ba_refresh_interval_sec:.1f}s · 行情合并 bookTicker",
            )
            for sym in symbols:
                try:
                    self._run_ba_api(
                        lambda s=sym: get_binance_symbol_meta(self._client, s),
                        log_failures=False,
                    )
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
            while not self._stop_event.is_set():
                try:
                    def _poll_quotes() -> list[Quote]:
                        self._quote_poll_count += 1
                        return self._fetch_watched_quotes(watched)

                    started = time.perf_counter()
                    quotes = self._run_ba_api(_poll_quotes, log_failures=False)
                    self._record_latency((time.perf_counter() - started) * 1000)
                    for q in quotes:
                        self.quote_received.emit(q)
                    need_depth = (
                        not self._order_books
                        or self._quote_poll_count % depth_every == 1
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
                except BinanceAPIException as exc:
                    if getattr(exc, "code", None) in (-1003, 418):
                        self._log(
                            LogLevel.ERROR,
                            f"BA 限频 code={exc.code}，请加大行情刷新间隔(建议≥1.0s)",
                        )
                    else:
                        self._log(LogLevel.ERROR, f"BA API 错误: {exc.message}")
                    self._set_state(ConnectionState.ERROR)
                except Exception as exc:
                    self._log(LogLevel.ERROR, f"BA 连接异常: {exc}")
                    self._set_state(ConnectionState.ERROR)
                time.sleep(self.config.ba_refresh_interval_sec)
        except Exception as exc:
            self._log(LogLevel.ERROR, _format_ba_connection_error(exc, self.config))
            self._set_state(ConnectionState.ERROR)

    def _set_state(self, state: ConnectionState) -> None:
        """更新连接状态并通知 UI。"""
        self._state = state
        self.state_changed.emit(state.value)
