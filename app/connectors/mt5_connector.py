from __future__ import annotations

import queue
import random
import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional

from PySide6.QtCore import QObject, QTimer, Signal

from app.core.exchange_utils import get_mt5_filling_mode
from app.core.models import AppConfig, ConnectionState, GoldOrderMode, Position, Quote, Side
from app.core.order_mode import resolve_execution_flags
from app.core.mt5_terminal import find_mt5_terminal, mt5_terminal_hint
from app.core.demo_market import demo_tick_time, generate_all_demo_pairs
from app.core.symbols import WATCHED_PRESETS, find_preset, resolve_symbols, watched_mt5_symbols
from app.core.trade_result import LegResult
from app.core.app_log import (
    LogLevel,
    hedge_action_label,
    hedge_mode_word,
    should_log,
    trade_leg_success_msg,
)

_mt5_import_error = ""
try:
    import MetaTrader5 as mt5
    HAS_MT5 = True
except ImportError as exc:
    HAS_MT5 = False
    _mt5_import_error = str(exc)


class MT5Connector(QObject):
    quote_received = Signal(object)
    state_changed = Signal(str)
    latency_updated = Signal(float)
    log = Signal(str)

    def __init__(self, config: AppConfig, parent=None):
        super().__init__(parent)
        self.config = config
        self._state = ConnectionState.DISCONNECTED
        self._last_latency_ms: float | None = None
        self._demo_timer: Optional[QTimer] = None
        self._poll_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._connected = False
        self._quotes: dict[str, Quote] = {}
        self._work_queue: queue.Queue = queue.Queue()
        self._demo_positions: dict[str, Position] = {}

    @property
    def quotes(self) -> dict[str, Quote]:
        return self._quotes

    def quote(self, symbol: str) -> Quote:
        return self._quotes.get(symbol, Quote(symbol=symbol))

    @property
    def last_quote(self) -> Quote:
        xau = find_preset("xau").symbol_mt5
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

    def _mt5_credentials(self) -> tuple[int, str, str] | None:
        login = int(self.config.mt5_login or 0)
        password = (self.config.mt5_password or "").strip()
        server = (self.config.mt5_server or "").strip()
        if login and password and server:
            return login, password, server
        return None

    def _format_mt5_init_error(self, err: tuple[int, str] | None) -> str:
        if not err:
            return "MT5 初始化失败，改用模拟行情"
        code, message = err
        if code == -10003:
            return f"MT5 初始化失败: 未找到 MetaTrader 5 终端。{mt5_terminal_hint()}"
        if code == -6:
            creds = self._mt5_credentials()
            if creds:
                return (
                    "MT5 授权失败：账户、密码或服务器名称不正确。"
                    "请在 MT5 中打开 工具→选项→服务器，核对服务器名与软件设置完全一致后重试"
                )
            return (
                "MT5 授权失败：终端尚未登录。"
                "请先在 MetaTrader 5 里登录 Exness 账户并保持终端运行，"
                "或在软件设置中填写正确的 MT5 账户、密码、服务器"
            )
        if code == -8:
            return "MT5 算法交易已禁用：请在 MT5 工具→选项→专家顾问 中勾选「允许算法交易」"
        if code == -10005:
            return (
                "MT5 连接超时：请确认 MetaTrader 5 已打开，且右下角显示已登录 Exness（不是 MetaQuotes-Demo）。"
                "通用 MT5 需手动 文件→登录→交易账户，服务器填 Exness-MT5Real41；"
                "或在 Exness 官网安装 Exness 版 MT5"
            )
        return f"MT5 初始化失败: {err}，改用模拟行情"

    def _initialize_mt5(self, terminal: Path | None) -> bool:
        creds = self._mt5_credentials()
        cred_kwargs: dict[str, Any] | None = None
        if creds:
            login, password, server = creds
            cred_kwargs = {
                "login": login,
                "password": password,
                "server": server,
                "timeout": 30000,
            }

        attempts: list[tuple[str, dict[str, Any]]] = [
            ("附着已运行的 MT5", {"timeout": 8000}),
        ]
        if cred_kwargs and terminal:
            attempts.append(
                (f"启动并登录 MT5（{cred_kwargs['server']}）", {"path": str(terminal), **cred_kwargs})
            )
        if cred_kwargs:
            attempts.append((f"API 登录（{cred_kwargs['server']}）", cred_kwargs))
        if terminal:
            attempts.append(("启动 MT5 终端", {"path": str(terminal), "timeout": 20000}))

        last_err: tuple[int, str] | None = None
        for label, kwargs in attempts:
            self._log(LogLevel.DEBUG, f"MT5 连接中 · {label}…")
            try:
                mt5.shutdown()
            except Exception:
                pass
            if mt5.initialize(**kwargs):
                self._log(LogLevel.DEBUG, f"MT5 已连接 · {label}")
                return True
            last_err = mt5.last_error()
            self._log(LogLevel.DEBUG, f"MT5 尝试失败 · {label}: {last_err}")

        self._log(LogLevel.INFO, self._format_mt5_init_error(last_err))
        return False

    def _ensure_mt5_login(self) -> bool:
        creds = self._mt5_credentials()
        if not creds:
            self._log(LogLevel.INFO, "Exness 使用终端当前登录账户（未填写账户密码）")
            account = mt5.account_info()
            if account and account.login:
                self._log(LogLevel.INFO, f"Exness 账户: {account.login} @ {account.server}")
                return True
            self._log(LogLevel.ERROR, "MT5 终端未登录账户，请在 MetaTrader 5 中登录 Exness 后再试")
            return False

        login, password, server = creds
        account = mt5.account_info()
        if account and account.login == login and account.server == server:
            self._log(LogLevel.INFO, f"Exness 账户: {account.login} @ {account.server}")
            return True

        if not mt5.login(login=login, password=password, server=server):
            self._log(LogLevel.ERROR, f"Exness 登录失败: {mt5.last_error()}（请核对账户、密码、服务器名称）")
            return False
        self._log(LogLevel.INFO, f"Exness 已登录: {login} @ {server}")
        return True

    def update_config(self, config: AppConfig) -> None:
        self.config = config
        if self._demo_timer is not None:
            interval_ms = max(100, int(round(config.ba_refresh_interval_sec * 1000)))
            self._demo_timer.setInterval(interval_ms)

    def _log(self, level: LogLevel, message: str) -> None:
        if should_log(self.config.log_level, level):
            self.log.emit(message)

    def start(self) -> None:
        self._stop_event.clear()
        if not self.config.use_live_mt5:
            self._start_demo()
            return
        if not HAS_MT5:
            detail = f"（{_mt5_import_error}）" if _mt5_import_error else ""
            self._log(
                LogLevel.INFO,
                "MetaTrader5 库不可用，Exness 使用模拟行情"
                f"{detail}；请安装 Exness MT5 终端并保持登录，然后重新 build.bat 打包",
            )
            self._start_demo()
            return
        self._set_state(ConnectionState.CONNECTING)
        self._poll_thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._poll_thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        self._connected = False
        if self._demo_timer:
            self._demo_timer.stop()
            self._demo_timer = None
        self._quotes.clear()
        self._set_state(ConnectionState.DISCONNECTED)

    def _call_on_mt5_thread(self, fn: Callable[[], Any], timeout: float = 30.0) -> Any:
        if threading.current_thread() is self._poll_thread:
            return fn()
        result_box: queue.Queue = queue.Queue(maxsize=1)
        self._work_queue.put((fn, result_box))
        ok, payload = result_box.get(timeout=timeout)
        if ok:
            return payload
        raise RuntimeError(str(payload))

    def _live_position(
        self, symbol: str, side: Side | None = None, min_qty: float = 0.0
    ) -> Position | None:
        if not self._connected or not HAS_MT5:
            return None
        for pos in mt5.positions_get(symbol=symbol) or []:
            pos_side = Side.BUY if pos.type == mt5.ORDER_TYPE_BUY else Side.SELL
            qty = float(pos.volume)
            if side is not None and pos_side != side:
                continue
            if qty + 1e-12 < min_qty:
                continue
            return Position(
                platform="MT5",
                symbol=pos.symbol,
                side=pos_side,
                quantity=qty,
                entry_price=pos.price_open,
                unrealized_pnl=pos.profit,
            )
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
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._live_position(symbol, side, min_qty):
                return True
            time.sleep(poll_sec)
        return False

    def _wait_until_flat(self, symbol: str, *, timeout: float = 5.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._live_position(symbol) is None:
                return True
            time.sleep(0.25)
        return False

    def _wait_until_position_at_most(
        self,
        symbol: str,
        side: Side | None,
        max_qty: float,
        *,
        timeout: float = 5.0,
    ) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            pos = self._live_position(symbol, side)
            if pos is None:
                return max_qty <= 0
            if pos.quantity <= max_qty + 1e-9:
                return True
            time.sleep(0.25)
        return False

    def get_positions(self) -> list[Position]:
        if not self.config.use_live_mt5:
            return list(self._demo_positions.values())
        if not self._connected or not HAS_MT5:
            return []

        def _fetch() -> list[Position]:
            from app.core.liquidation import (
                calc_liquidation_price_from_profit,
                mt5_account_liq_buffer,
            )

            account = mt5.account_info()
            account_buffer: float | None = None
            if account is not None:
                account_buffer = mt5_account_liq_buffer(
                    float(account.equity),
                    float(account.margin),
                    float(account.margin_so_so),
                )
            positions = []
            for symbol in watched_mt5_symbols():
                for pos in mt5.positions_get(symbol=symbol) or []:
                    side = Side.BUY if pos.type == mt5.ORDER_TYPE_BUY else Side.SELL
                    liq_price = 0.0
                    if account is not None:
                        equity_without = float(account.equity) - float(pos.profit)

                        def profit_at(close: float, _p=pos) -> float:
                            value = mt5.order_calc_profit(
                                _p.symbol,
                                _p.type,
                                float(_p.volume),
                                float(_p.price_open),
                                float(close),
                            )
                            return float(value or 0.0)

                        liq_price = calc_liquidation_price_from_profit(
                            side,
                            float(pos.price_open),
                            equity_without,
                            float(account.margin),
                            float(account.margin_so_so),
                            profit_at,
                        )
                    positions.append(
                        Position(
                            platform="MT5",
                            symbol=pos.symbol,
                            side=side,
                            quantity=pos.volume,
                            entry_price=pos.price_open,
                            unrealized_pnl=pos.profit,
                            liquidation_price=liq_price,
                            mark_price=float(pos.price_current or 0.0),
                            leverage=int(account.leverage if account else self.config.mt5_leverage),
                            exchange_liq_buffer=account_buffer,
                        )
                    )
            return positions

        try:
            return self._call_on_mt5_thread(_fetch)
        except Exception as exc:
            self._log(LogLevel.ERROR, f"Exness 持仓查询失败: {exc}")
            return []

    def replace_demo_positions(self, positions: list[Position]) -> None:
        self._demo_positions = {p.symbol: p for p in positions}

    def open_hedge_leg(
        self, preset_id: str, mode: str = "contraction", order_mode: str = GoldOrderMode.LIMIT.value
    ) -> LegResult:
        from app.core.models import HedgeMode

        _, symbol_mt5, _ = resolve_symbols(
            preset_id, self.config.symbol_ba, self.config.symbol_mt5
        )
        lots = self.config.mt5_lot_for(preset_id)
        quote = self._quotes.get(symbol_mt5, Quote(symbol=symbol_mt5))
        use_limit, maker_only = resolve_execution_flags(preset_id, order_mode)
        mt5_side = Side.BUY if mode == HedgeMode.CONTRACTION.value else Side.SELL
        adding = False

        if not self.config.use_live_mt5:
            price = (quote.ask or quote.mid) if mt5_side == Side.BUY else (quote.bid or quote.mid)
            adding = symbol_mt5 in self._demo_positions
            if adding:
                existing = self._demo_positions[symbol_mt5]
                if existing.side != mt5_side:
                    return LegResult(
                        platform="MT5",
                        success=False,
                        message="Exness 持仓方向与本次开仓不一致",
                    )
                total_lots = existing.quantity + lots
                existing.entry_price = (
                    existing.entry_price * existing.quantity + price * lots
                ) / total_lots
                existing.quantity = total_lots
            else:
                self._demo_positions[symbol_mt5] = Position(
                    platform="MT5",
                    symbol=symbol_mt5,
                    side=mt5_side,
                    quantity=lots,
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
                    "Exness",
                    "open",
                    mode,
                    "demo-mt5",
                    adding=adding,
                    lots=str(lots),
                    price=f"{price:.3f}",
                    order_type=order_mode_text,
                ),
            )
            msg = "演示加仓成功" if adding else "演示开仓成功"
            return LegResult(platform="MT5", success=True, message=msg, order_id="demo-mt5")

        if not self._connected:
            return LegResult(platform="MT5", success=False, message="Exness 未连接")

        before_open = self._live_position(symbol_mt5, mt5_side)
        adding = before_open is not None and before_open.quantity > 0

        def _open() -> LegResult:
            if not mt5.symbol_select(symbol_mt5, True):
                return LegResult(platform="MT5", success=False, message=f"品种 {symbol_mt5} 不可用")
            tick = mt5.symbol_info_tick(symbol_mt5)
            info = mt5.symbol_info(symbol_mt5)
            if not tick or not info:
                return LegResult(platform="MT5", success=False, message="无法获取 Exness 报价")
            before = self._live_position(symbol_mt5, mt5_side)
            target_lots = (before.quantity if before else 0.0) + float(lots)
            if use_limit:
                if mt5_side == Side.BUY:
                    order_type = mt5.ORDER_TYPE_BUY_LIMIT
                    price = tick.bid
                else:
                    order_type = mt5.ORDER_TYPE_SELL_LIMIT
                    price = tick.ask
                request = {
                    "action": mt5.TRADE_ACTION_PENDING,
                    "symbol": symbol_mt5,
                    "volume": float(lots),
                    "type": order_type,
                    "price": price,
                    "magic": 260604,
                    "comment": "xau_open_limit",
                    "type_time": mt5.ORDER_TIME_GTC,
                    "type_filling": get_mt5_filling_mode(info),
                }
            else:
                if mt5_side == Side.BUY:
                    order_type = mt5.ORDER_TYPE_BUY
                    price = tick.ask
                else:
                    order_type = mt5.ORDER_TYPE_SELL
                    price = tick.bid
                request = {
                    "action": mt5.TRADE_ACTION_DEAL,
                    "symbol": symbol_mt5,
                    "volume": float(lots),
                    "type": order_type,
                    "price": price,
                    "deviation": 30,
                    "magic": 260604,
                    "comment": "xag_open",
                    "type_time": mt5.ORDER_TIME_GTC,
                    "type_filling": get_mt5_filling_mode(info),
                }
            result = mt5.order_send(request)
            if result is None:
                return LegResult(platform="MT5", success=False, message=f"order_send 失败: {mt5.last_error()}")
            if result.retcode != mt5.TRADE_RETCODE_DONE:
                return LegResult(
                    platform="MT5",
                    success=False,
                    message=(
                        f"Exness {hedge_action_label('open', mode, adding=adding)}失败 "
                        f"retcode={result.retcode} {result.comment}"
                    ),
                )
            oid = str(result.order)
            if use_limit:
                confirmed = self._wait_for_live_position(symbol_mt5, mt5_side, target_lots)
            else:
                confirmed = self._wait_for_live_position(
                    symbol_mt5,
                    mt5_side,
                    target_lots,
                    timeout=1.5,
                    poll_sec=0.08,
                )
            if not confirmed:
                return LegResult(
                    platform="MT5",
                    success=False,
                    message=f"Exness 订单 {oid} 未确认成交，请检查订单/持仓",
                    order_id=oid,
                    needs_reconciliation=True,
                )
            self._log(
                LogLevel.TRADE,
                trade_leg_success_msg(
                    "Exness",
                    "open",
                    mode,
                    oid,
                    adding=adding,
                    lots=str(lots),
                ),
            )
            return LegResult(
                platform="MT5",
                success=True,
                message=f"{'加仓' if adding else '开仓'}{hedge_mode_word(mode)}成功",
                order_id=oid,
            )

        try:
            return self._call_on_mt5_thread(_open)
        except Exception as exc:
            msg = f"Exness {hedge_action_label('open', mode, adding=adding)}失败: {exc}"
            self._log(LogLevel.ERROR, msg)
            return LegResult(platform="MT5", success=False, message=msg)

    def close_hedge_leg(
        self,
        preset_id: str,
        order_mode: str = GoldOrderMode.LIMIT.value,
        mode: str = "contraction",
        *,
        close_all: bool = False,
    ) -> LegResult:
        _, symbol_mt5, _ = resolve_symbols(
            preset_id, self.config.symbol_ba, self.config.symbol_mt5
        )
        use_limit, maker_only = resolve_execution_flags(preset_id, order_mode)

        trade_lots = self.config.mt5_lot_for(preset_id)
        action_label = hedge_action_label("close", mode)

        if not self.config.use_live_mt5:
            if symbol_mt5 not in self._demo_positions:
                return LegResult(platform="MT5", success=True, message="演示无持仓")
            demo_pos = self._demo_positions[symbol_mt5]
            lots_to_close = demo_pos.quantity if close_all else min(demo_pos.quantity, trade_lots)
            demo_pos.quantity -= lots_to_close
            if demo_pos.quantity <= 1e-9:
                del self._demo_positions[symbol_mt5]
            self._log(
                LogLevel.TRADE,
                trade_leg_success_msg(
                    "Exness",
                    "close",
                    mode,
                    "demo-mt5-close",
                    lots=str(lots_to_close),
                ),
            )
            return LegResult(platform="MT5", success=True, message="演示平仓成功", order_id="demo-mt5-close")

        if not self._connected:
            return LegResult(platform="MT5", success=False, message="Exness 未连接")

        def _close() -> LegResult:
            raw_positions = mt5.positions_get(symbol=symbol_mt5) or []
            if not raw_positions:
                return LegResult(platform="MT5", success=True, message="无 Exness 持仓")
            info = mt5.symbol_info(symbol_mt5)
            tick = mt5.symbol_info_tick(symbol_mt5)
            if not tick or not info:
                return LegResult(platform="MT5", success=False, message="无法获取 Exness 报价")
            for pos in raw_positions:
                pos_side = Side.BUY if pos.type == mt5.ORDER_TYPE_BUY else Side.SELL
                lots_to_close = float(pos.volume) if close_all else min(float(pos.volume), trade_lots)
                remaining = max(0.0, float(pos.volume) - lots_to_close)
                if use_limit:
                    close_type = mt5.ORDER_TYPE_SELL_LIMIT if pos.type == mt5.ORDER_TYPE_BUY else mt5.ORDER_TYPE_BUY_LIMIT
                    close_price = tick.ask if close_type == mt5.ORDER_TYPE_SELL_LIMIT else tick.bid
                    request = {
                        "action": mt5.TRADE_ACTION_PENDING,
                        "symbol": symbol_mt5,
                        "position": pos.ticket,
                        "volume": lots_to_close,
                        "type": close_type,
                        "price": close_price,
                        "magic": 260604,
                        "comment": "xau_close_limit",
                        "type_time": mt5.ORDER_TIME_GTC,
                        "type_filling": get_mt5_filling_mode(info),
                    }
                else:
                    close_type = mt5.ORDER_TYPE_SELL if pos.type == mt5.ORDER_TYPE_BUY else mt5.ORDER_TYPE_BUY
                    close_price = tick.bid if close_type == mt5.ORDER_TYPE_SELL else tick.ask
                    request = {
                        "action": mt5.TRADE_ACTION_DEAL,
                        "symbol": symbol_mt5,
                        "position": pos.ticket,
                        "volume": lots_to_close,
                        "type": close_type,
                        "price": close_price,
                        "deviation": 30,
                        "magic": 260604,
                        "comment": "xag_close",
                        "type_time": mt5.ORDER_TIME_GTC,
                        "type_filling": get_mt5_filling_mode(info),
                    }
                result = mt5.order_send(request)
                if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
                    comment = result.comment if result else mt5.last_error()
                    return LegResult(platform="MT5", success=False, message=f"Exness {action_label}失败: {comment}")
                oid = str(result.order)
                if not self._wait_until_position_at_most(symbol_mt5, pos_side, remaining):
                    return LegResult(
                        platform="MT5",
                        success=False,
                        message=f"Exness 平仓订单 {oid} 未确认减仓，请检查持仓",
                        order_id=oid,
                        needs_reconciliation=True,
                    )
                self._log(
                    LogLevel.TRADE,
                    trade_leg_success_msg(
                        "Exness",
                        "close",
                        mode,
                        oid,
                        lots=str(lots_to_close),
                    ),
                )
                return LegResult(
                    platform="MT5",
                    success=True,
                    message=f"{action_label}成功",
                    order_id=oid,
                )
            return LegResult(platform="MT5", success=False, message="未找到可平仓位")

        try:
            return self._call_on_mt5_thread(_close)
        except Exception as exc:
            msg = f"Exness {action_label}失败: {exc}"
            self._log(LogLevel.ERROR, msg)
            return LegResult(platform="MT5", success=False, message=msg)

    def _start_demo(self) -> None:
        self._set_state(ConnectionState.SIMULATED)
        self._log(LogLevel.DEBUG, "Exness 模拟行情 · 黄金 + 白银（非真实价格）")
        self._demo_timer = QTimer(self)
        self._demo_timer.timeout.connect(self._emit_demo_quotes)
        interval_ms = max(100, int(round(self.config.ba_refresh_interval_sec * 1000)))
        self._demo_timer.start(interval_ms)

    def _emit_demo_quotes(self) -> None:
        t = demo_tick_time(time.time(), self.config.ba_refresh_interval_sec)
        self._record_latency(random.uniform(3, 12))
        pairs = generate_all_demo_pairs(t)
        for preset_id, (_, mt5) in pairs.items():
            self._quotes[mt5.symbol] = mt5
            self.quote_received.emit(mt5)

    def _process_work_queue(self) -> None:
        while True:
            try:
                fn, result_box = self._work_queue.get_nowait()
            except queue.Empty:
                break
            try:
                result_box.put((True, fn()))
            except Exception as exc:
                result_box.put((False, exc))

    def read_account_leverage(self) -> int | None:
        if not self._connected or not HAS_MT5:
            return None

        def _read() -> int | None:
            info = mt5.account_info()
            if info is None:
                return None
            lev = int(getattr(info, "leverage", 0) or 0)
            return lev if lev > 0 else None

        try:
            return self._call_on_mt5_thread(_read)
        except Exception:
            return None

    def _poll_loop(self) -> None:
        try:
            terminal = find_mt5_terminal(self.config.mt5_terminal_path)
            if terminal:
                self._log(LogLevel.DEBUG, f"Exness 终端: {terminal}")
            if not self._initialize_mt5(terminal):
                self._set_state(ConnectionState.ERROR)
                self._start_demo()
                return
            if not self._ensure_mt5_login():
                self._set_state(ConnectionState.ERROR)
                mt5.shutdown()
                return
            self._connected = True
            self._set_state(ConnectionState.CONNECTED)
            symbols = watched_mt5_symbols()
            for sym in symbols:
                mt5.symbol_select(sym, True)
            self._log(LogLevel.INFO, f"Exness 已连接 · {', '.join(symbols)}")
            lev = self.read_account_leverage()
            if lev:
                self._log(LogLevel.DEBUG, f"Exness 账户杠杆 {lev}x")
            while not self._stop_event.is_set():
                self._process_work_queue()
                started = time.perf_counter()
                for symbol in symbols:
                    tick = mt5.symbol_info_tick(symbol)
                    if tick:
                        quote = Quote(
                            symbol=symbol,
                            bid=tick.bid,
                            ask=tick.ask,
                            timestamp=time.time(),
                            is_simulated=False,
                        )
                        self._quotes[symbol] = quote
                        self.quote_received.emit(quote)
                self._record_latency((time.perf_counter() - started) * 1000)
                try:
                    fn, result_box = self._work_queue.get(timeout=0.3)
                except queue.Empty:
                    continue
                try:
                    result_box.put((True, fn()))
                except Exception as exc:
                    result_box.put((False, exc))
        except Exception as exc:
            self._log(LogLevel.ERROR, f"Exness 连接异常: {exc}")
            self._set_state(ConnectionState.ERROR)
        finally:
            if self._connected and HAS_MT5:
                mt5.shutdown()
                self._connected = False

    def _set_state(self, state: ConnectionState) -> None:
        self._state = state
        self.state_changed.emit(state.value)
