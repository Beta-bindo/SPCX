"""MetaTrader5 / Exness 连接器。

封装与 MT5 终端的交互：登录初始化、行情订阅、持仓查询、对冲单腿开/平仓。
未安装 MetaTrader5 库或未配置时退化为模拟行情。

关键约束：MetaTrader5 的 Python API 不是线程安全的，必须在同一个线程内调用。
因此本类用一个专用工作线程（_poll_loop）顺序执行所有 MT5 调用，其他线程通过
工作队列（_work_queue）+ _call_on_mt5_thread 投递任务并同步等待结果。
"""

from __future__ import annotations

from datetime import datetime
import queue
import random
import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional

from PySide6.QtCore import QObject, QTimer, Signal

from app.core.exchange_utils import get_mt5_filling_mode, translate_exchange_error
from app.core.models import AccountSnapshot, AppConfig, ConnectionState, GoldOrderMode, OpenOrder, Position, Quote, Side
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
    """Exness/MT5 连接器，对外暴露报价/持仓/下单能力，并通过信号通知 UI。"""

    quote_received = Signal(object)   # 收到新报价
    state_changed = Signal(str)       # 连接状态变化
    latency_updated = Signal(float)   # 行情延迟（ms）
    account_received = Signal(object)  # 账户资金快照（AccountSnapshot）
    log = Signal(str)                 # 日志行

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
        self._work_queue: queue.Queue = queue.Queue()        # 投递到 MT5 工作线程的任务队列
        self._demo_positions: dict[str, Position] = {}       # 模拟模式虚拟持仓
        self._account_snapshot_at = 0.0                      # 账户资金快照节流时间戳

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
        """取齐全的登录凭据 (账号, 密码, 服务器)；任一缺失返回 None。"""
        login = int(self.config.mt5_login or 0)
        password = (self.config.mt5_password or "").strip()
        server = (self.config.mt5_server or "").strip()
        if login and password and server:
            return login, password, server
        return None

    def _format_mt5_init_error(self, err: tuple[int, str] | None) -> str:
        """把 MT5 初始化错误码翻译成带排障指引的中文提示。"""
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
        """按多种方式依次尝试连接 MT5（附着已运行 / 带凭据启动 / API 登录 / 启动终端）。"""
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
        """确保终端已登录目标账户：未填凭据则沿用终端当前账户，否则校验/登录。"""
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
        """热更新配置并同步模拟行情刷新间隔。"""
        self.config = config
        if self._demo_timer is not None:
            interval_ms = max(100, int(round(config.ba_refresh_interval_sec * 1000)))
            self._demo_timer.setInterval(interval_ms)

    def _log(self, level: LogLevel, message: str) -> None:
        if should_log(self.config.log_level, level):
            self.log.emit(message)

    def start(self) -> None:
        """启动连接：实盘且库可用时拉起 MT5 工作线程，否则退化为模拟行情。"""
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
        """停止连接/模拟并复位状态。"""
        self._stop_event.set()
        self._connected = False
        if self._demo_timer:
            self._demo_timer.stop()
            self._demo_timer = None
        self._quotes.clear()
        self._set_state(ConnectionState.DISCONNECTED)

    def _call_on_mt5_thread(self, fn: Callable[[], Any], timeout: float = 30.0) -> Any:
        """在 MT5 专用线程上执行 fn 并同步取回结果（保证 MT5 API 单线程调用）。

        若调用方本身就是工作线程则直接执行；否则投递队列并阻塞等待结果。
        """
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
        """实时查询匹配交易对（可选方向/最小手数）的单个持仓。"""
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

    def _live_volume_total(self, symbol: str, side: Side | None = None) -> float:
        """汇总某交易对(可选方向)的全部持仓票据手数之和。

        Exness MT5 多为对冲(Hedging)账户，每次下单生成独立票据不会合并，
        因此确认成交/减仓必须按「所有票据之和」判断，而非单个票据。
        """
        if not self._connected or not HAS_MT5:
            return 0.0
        total = 0.0
        for pos in mt5.positions_get(symbol=symbol) or []:
            pos_side = Side.BUY if pos.type == mt5.ORDER_TYPE_BUY else Side.SELL
            if side is not None and pos_side != side:
                continue
            total += float(pos.volume)
        return total

    def _wait_for_live_position(
        self,
        symbol: str,
        side: Side,
        min_qty: float,
        *,
        timeout: float = 5.0,
        poll_sec: float = 0.25,
    ) -> bool:
        """轮询等待指定方向持仓「总手数」≥min_qty（确认开仓/加仓落地）。

        对冲账户加仓会产生多张票据，故按总手数判断，避免把已成交误判为未成交。
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._live_volume_total(symbol, side) + 1e-9 >= min_qty:
                return True
            time.sleep(poll_sec)
        return False

    def _wait_until_flat(self, symbol: str, *, timeout: float = 5.0) -> bool:
        """轮询等待该交易对持仓清零（确认平仓完成）。"""
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
        """轮询等待持仓「总手数」降到 ≤max_qty（确认平仓到位）。

        对冲账户同方向可能有多张票据，故按总手数判断，避免误判未减仓。
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._live_volume_total(symbol, side) <= max_qty + 1e-9:
                return True
            time.sleep(0.25)
        return False

    def get_positions(self, *, include_risk: bool = True) -> list[Position]:
        """查询全部受监控品种的 MT5 持仓。

        include_risk=False 时只读取官方持仓/浮盈，跳过账户强平模型计算，供 1 秒
        盈亏快刷使用，避免 order_calc_profit 拖慢界面。
        """
        if not self.config.use_live_mt5:
            return list(self._demo_positions.values())
        if not self._connected or not HAS_MT5:
            return []

        def _fetch() -> list[Position]:
            if include_risk:
                from app.core.liquidation import (
                    calc_liquidation_price_from_profit,
                    mt5_account_liq_buffer,
                )

            account = mt5.account_info() if include_risk else None
            account_buffer: float | None = None
            if account is not None:
                account_buffer = mt5_account_liq_buffer(
                    float(account.equity),
                    float(account.margin),
                    float(account.margin_so_so),
                )
            positions = []
            for symbol in watched_mt5_symbols():
                live_positions = mt5.positions_get(symbol=symbol) or []
                if not live_positions:
                    continue
                # order_calc_profit 需要该品种已在行情列表(Market Watch)且有报价，
                # 否则会抛 "returned a result with an error set"。先尝试选中提高成功率。
                try:
                    mt5.symbol_select(symbol, True)
                except Exception:
                    pass

                # Exness 多为对冲(Hedging)账户：同品种同方向会有多张独立票据，
                # 必须按「方向」合并为一条持仓（手数加总、入场价按手数加权、盈亏加总），
                # 否则界面/盈亏/对冲数量核对只会取到其中一张票，导致数量与盈亏少算。
                groups: dict[Side, dict] = {}
                for pos in live_positions:
                    side = Side.BUY if pos.type == mt5.ORDER_TYPE_BUY else Side.SELL
                    g = groups.setdefault(
                        side,
                        {"volume": 0.0, "pnl": 0.0, "notional": 0.0, "mark": 0.0, "sym": pos.symbol},
                    )
                    vol = float(pos.volume)
                    g["volume"] += vol
                    g["pnl"] += float(pos.profit)
                    g["notional"] += float(pos.price_open) * vol
                    g["mark"] = float(pos.price_current or 0.0)
                    g["sym"] = pos.symbol

                for side, g in groups.items():
                    total_vol = g["volume"]
                    if total_vol <= 0:
                        continue
                    avg_entry = g["notional"] / total_vol if total_vol else 0.0
                    total_pnl = g["pnl"]
                    liq_price = 0.0
                    if include_risk and account is not None:
                        try:
                            equity_without = float(account.equity) - total_pnl
                            otype = (
                                mt5.ORDER_TYPE_BUY if side == Side.BUY else mt5.ORDER_TYPE_SELL
                            )

                            def profit_at(
                                close: float,
                                _sym=g["sym"],
                                _otype=otype,
                                _vol=total_vol,
                                _entry=avg_entry,
                            ) -> float:
                                value = mt5.order_calc_profit(
                                    _sym, _otype, float(_vol), float(_entry), float(close)
                                )
                                if value is None:
                                    raise RuntimeError(
                                        f"order_calc_profit 无结果 (last_error={mt5.last_error()})"
                                    )
                                return float(value)

                            liq_price = calc_liquidation_price_from_profit(
                                side,
                                avg_entry,
                                equity_without,
                                float(account.margin),
                                float(account.margin_so_so),
                                profit_at,
                            )
                        except Exception as exc:
                            # 单笔爆仓价算不出（多为该品种暂无报价/非交易时段）不应拖垮
                            # 整个 EX 持仓查询：降级为未知爆仓价(0) 并保留持仓本身。
                            liq_price = 0.0
                            self._log(
                                LogLevel.DEBUG,
                                f"Exness {symbol} 爆仓价暂不可用: {exc}",
                            )
                    positions.append(
                        Position(
                            platform="MT5",
                            symbol=g["sym"],
                            side=side,
                            quantity=total_vol,
                            entry_price=avg_entry,
                            unrealized_pnl=total_pnl,
                            liquidation_price=liq_price,
                            mark_price=g["mark"],
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

    def get_open_orders(self) -> list[OpenOrder]:
        """查询受监控交易对的全部未成交挂单（pending orders）。"""
        if not self.config.use_live_mt5:
            return []
        if not self._connected or not HAS_MT5:
            return []

        def _fetch() -> list[OpenOrder]:
            orders: list[OpenOrder] = []
            buy_types = {
                mt5.ORDER_TYPE_BUY,
                mt5.ORDER_TYPE_BUY_LIMIT,
                mt5.ORDER_TYPE_BUY_STOP,
                mt5.ORDER_TYPE_BUY_STOP_LIMIT,
            }
            sell_types = {
                mt5.ORDER_TYPE_SELL,
                mt5.ORDER_TYPE_SELL_LIMIT,
                mt5.ORDER_TYPE_SELL_STOP,
                mt5.ORDER_TYPE_SELL_STOP_LIMIT,
            }
            for symbol in watched_mt5_symbols():
                for order in mt5.orders_get(symbol=symbol) or []:
                    order_type = int(order.type)
                    if order_type in buy_types:
                        side = Side.BUY
                    elif order_type in sell_types:
                        side = Side.SELL
                    else:
                        side = Side.NONE
                    total = float(order.volume_initial)
                    remaining = float(order.volume_current)
                    filled = max(0.0, total - remaining)
                    orders.append(
                        OpenOrder(
                            platform="MT5",
                            symbol=str(order.symbol),
                            order_id=str(order.ticket),
                            side=side,
                            order_type=str(order_type),
                            total_quantity=total,
                            filled_quantity=filled,
                            remaining_quantity=remaining,
                            price=float(order.price_open),
                        )
                    )
            return orders

        try:
            return self._call_on_mt5_thread(_fetch)
        except Exception as exc:
            self._log(LogLevel.ERROR, f"Exness 委托查询失败: {exc}")
            return []

    def replace_demo_positions(self, positions: list[Position]) -> None:
        """覆盖模拟模式下的虚拟持仓。"""
        self._demo_positions = {p.symbol: p for p in positions}

    def _order_send_auto_filling(self, request: dict, info):
        """下单；若返回 retcode=10030（不支持的成交模式）则自动回退其它 filling 模式重试。

        不同 Exness 服务器/品种支持的成交模式不同，单一模式可能被拒。仅对「不支持
        成交模式」这一种错误换模式重发，其它错误（资金不足/价格无效等）原样返回，
        避免无意义重试。
        """
        invalid_fill = getattr(mt5, "TRADE_RETCODE_INVALID_FILL", 10030)
        preferred = get_mt5_filling_mode(info)
        candidates = [preferred]
        for fm in (mt5.ORDER_FILLING_FOK, mt5.ORDER_FILLING_IOC, mt5.ORDER_FILLING_RETURN):
            if fm not in candidates:
                candidates.append(fm)
        result = None
        for fm in candidates:
            request["type_filling"] = fm
            result = mt5.order_send(request)
            if result is None:
                return None
            if result.retcode != invalid_fill:
                return result
        return result

    @staticmethod
    def _volume_decimals(step: float) -> int:
        """根据 MT5 volume_step 推断保留小数位，避免 0.009999 这类浮点量下单失败。"""
        text = f"{step:.8f}".rstrip("0").rstrip(".")
        if "." not in text:
            return 0
        return len(text.split(".", 1)[1])

    @staticmethod
    def _result_filled_price(result, *, fallback: float = 0.0) -> float:
        try:
            price = float(getattr(result, "price", 0) or 0)
        except (TypeError, ValueError):
            price = 0.0
        return price if price > 0 else float(fallback or 0.0)

    @staticmethod
    def _deal_charge(deal) -> float:
        """MT5 deal 中 commission/fee/swap 的净成本；正数=成本，负数=收益。"""
        total = 0.0
        for attr in ("commission", "fee", "swap"):
            try:
                total += float(getattr(deal, attr, 0) or 0)
            except (TypeError, ValueError):
                continue
        return -total

    def _fetch_deal_summary(
        self,
        symbol: str,
        order_ids: list[str],
        start_ts: float,
        end_ts: float | None = None,
    ) -> tuple[float, float, bool]:
        """从 MT5 历史成交读取官方 profit 与 commission/fee/swap。

        返回 (官方已实现盈亏, 正数费用成本, 是否匹配到成交)。
        """
        if not order_ids:
            return 0.0, 0.0, False
        ids = {str(oid) for oid in order_ids if oid}
        if not ids:
            return 0.0, 0.0, False
        start = datetime.fromtimestamp(max(0.0, start_ts - 5.0))
        end = datetime.fromtimestamp((end_ts or time.time()) + 5.0)
        try:
            deals = mt5.history_deals_get(start, end) or []
        except Exception:
            return 0.0, 0.0, False
        profit_total = 0.0
        charge_total = 0.0
        matched = False
        for deal in deals:
            if symbol and str(getattr(deal, "symbol", "") or "") != symbol:
                continue
            if str(getattr(deal, "order", "") or "") not in ids:
                continue
            matched = True
            charge_total += self._deal_charge(deal)
            try:
                profit_total += float(getattr(deal, "profit", 0) or 0)
            except (TypeError, ValueError):
                pass
        return round(profit_total, 2), round(charge_total, 4), matched

    def _fetch_deal_charges(
        self,
        symbol: str,
        order_ids: list[str],
        start_ts: float,
        end_ts: float | None = None,
    ) -> tuple[float, bool]:
        """从 MT5 历史成交读取真实 commission/fee/swap。"""
        _profit, fee, known = self._fetch_deal_summary(symbol, order_ids, start_ts, end_ts)
        return fee, known

    def fetch_history_deals(
        self, symbols: list[str], start: datetime, end: datetime
    ) -> list[dict]:
        """读取 EX/MT5 官方历史成交（history_deals_get 原始字段）。"""
        if not self._connected or not HAS_MT5:
            return []
        wanted = {s for s in symbols if s}

        def _fetch() -> list[dict]:
            deals = mt5.history_deals_get(start, end) or []
            out: list[dict] = []
            for deal in deals:
                if hasattr(deal, "_asdict"):
                    raw = dict(deal._asdict())
                else:
                    raw = {
                        key: getattr(deal, key, "")
                        for key in (
                            "ticket",
                            "order",
                            "time",
                            "time_msc",
                            "type",
                            "entry",
                            "magic",
                            "position_id",
                            "reason",
                            "volume",
                            "price",
                            "commission",
                            "fee",
                            "swap",
                            "profit",
                            "symbol",
                            "comment",
                            "external_id",
                        )
                    }
                if wanted and str(raw.get("symbol", "") or "") not in wanted:
                    continue
                out.append(raw)
            return out

        return self._call_on_mt5_thread(_fetch)

    def _normalize_mt5_volume(self, volume: float, info, *, cap: float | None = None) -> float:
        """把按 BA 成交量换算出来的 MT5 手数对齐到品种 volume_step。

        BA 可能成交 0.996，而配置比例换算得到 0.00996 手；Exness 常见最小步进是
        0.01 手，直接发送 0.00996 会失败。这里按最近步进归一化，必要时受当前持仓量 cap 限制。
        """
        volume = max(0.0, float(volume))
        if volume <= 1e-12:
            return 0.0
        step = float(getattr(info, "volume_step", 0.01) or 0.01)
        min_volume = float(getattr(info, "volume_min", step) or step)
        step = max(step, 1e-8)
        decimals = self._volume_decimals(step)
        normalized = round(volume / step) * step
        if normalized < min_volume and volume >= min_volume * 0.5:
            normalized = min_volume
        if cap is not None:
            normalized = min(normalized, max(0.0, float(cap)))
        return round(normalized, decimals)

    def open_hedge_leg(
        self,
        preset_id: str,
        mode: str = "contraction",
        order_mode: str = GoldOrderMode.LIMIT.value,
        *,
        lots_override: float | None = None,
    ) -> LegResult:
        """在 MT5 端开/加一腿对冲仓。

        收缩 → 买入（BUY），扩张 → 卖出（SELL）；与 BA 端方向相反构成对冲。
        实盘下单后轮询确认持仓出现，未确认则返回 needs_reconciliation 交由上层暂停自动补偿并提示对账。
        """
        from app.core.models import HedgeMode

        _, symbol_mt5, _ = resolve_symbols(
            preset_id, self.config.symbol_ba, self.config.symbol_mt5
        )
        lots = self.config.mt5_lot_for(preset_id)
        if lots_override is not None:
            lots = max(0.0, float(lots_override))
        if lots <= 0:
            return LegResult(platform="MT5", success=False, message="Exness 下单手数为 0")
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
            return LegResult(
                platform="MT5",
                success=True,
                message=msg,
                order_id="demo-mt5",
                filled_quantity=float(lots),
                filled_price=float(price or 0),
            )

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
            order_lots = self._normalize_mt5_volume(float(lots), info)
            if order_lots <= 0:
                return LegResult(
                    platform="MT5",
                    success=False,
                    message=(
                        f"Exness {hedge_action_label('open', mode, adding=adding)}失败: "
                        f"换算手数 {float(lots):g} 低于品种最小手数/步进，无法精确开仓"
                    ),
                )
            # 对冲账户加仓会新增独立票据，目标按同方向「总手数」累加
            before_total = self._live_volume_total(symbol_mt5, mt5_side)
            target_lots = before_total + order_lots
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
                    "volume": order_lots,
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
                    "volume": order_lots,
                    "type": order_type,
                    "price": price,
                    "deviation": 30,
                    "magic": 260604,
                    "comment": "xag_open",
                    "type_time": mt5.ORDER_TIME_GTC,
                    "type_filling": get_mt5_filling_mode(info),
                }
            history_start = time.time()
            result = self._order_send_auto_filling(request, info)
            if result is None:
                return LegResult(platform="MT5", success=False, message=f"order_send 失败: {mt5.last_error()}")
            if result.retcode != mt5.TRADE_RETCODE_DONE:
                return LegResult(
                    platform="MT5",
                    success=False,
                    message=(
                        f"Exness {hedge_action_label('open', mode, adding=adding)}失败 "
                        f"retcode={result.retcode} {translate_exchange_error(result.comment)}"
                    ),
                )
            oid = str(result.order)
            if use_limit:
                confirmed = self._wait_for_live_position(symbol_mt5, mt5_side, target_lots)
            else:
                # 市价单 order_send 已返回 DONE 即成交；放宽确认窗口到 3s，
                # 避免持仓回报稍慢被误判为「未确认成交」而触发不必要的回滚。
                confirmed = self._wait_for_live_position(
                    symbol_mt5,
                    mt5_side,
                    target_lots,
                    timeout=3.0,
                    poll_sec=0.1,
                )
            if not confirmed:
                return LegResult(
                    platform="MT5",
                    success=False,
                    message=f"Exness 订单 {oid} 未确认成交，请检查订单/持仓",
                    order_id=oid,
                    needs_reconciliation=True,
                )
            filled_price = self._result_filled_price(result, fallback=float(price or 0))
            realized_pnl, fee, fee_known = self._fetch_deal_summary(
                symbol_mt5,
                [oid],
                history_start,
            )
            self._log(
                LogLevel.TRADE,
                trade_leg_success_msg(
                    "Exness",
                    "open",
                    mode,
                    oid,
                    adding=adding,
                    lots=str(order_lots),
                    price=f"{filled_price:.3f}" if filled_price > 0 else "",
                ),
            )
            return LegResult(
                platform="MT5",
                success=True,
                message=f"{'加仓' if adding else '开仓'}{hedge_mode_word(mode)}成功",
                order_id=oid,
                filled_quantity=order_lots,
                filled_price=filled_price,
                fee=fee,
                fee_known=fee_known,
                realized_pnl=realized_pnl,
                pnl_known=fee_known,
            )

        try:
            return self._call_on_mt5_thread(_open)
        except Exception as exc:
            msg = f"Exness {hedge_action_label('open', mode, adding=adding)}失败: {translate_exchange_error(exc)}"
            self._log(LogLevel.ERROR, msg)
            return LegResult(platform="MT5", success=False, message=msg)

    def close_hedge_leg(
        self,
        preset_id: str,
        order_mode: str = GoldOrderMode.LIMIT.value,
        mode: str = "contraction",
        *,
        close_all: bool = False,
        lots_override: float | None = None,
    ) -> LegResult:
        """平 MT5 端对冲仓（按持仓 ticket 反向下单）。close_all=True 全平，否则部分平。

        下单后轮询确认减仓量，未确认则返回 needs_reconciliation。
        lots_override 指定本次要平的手数（用于「加仓失败回滚」时只平掉本次成交的增量，
        避免误平用户原有持仓）；给定时优先于单次交易量，且忽略 close_all。
        """
        _, symbol_mt5, _ = resolve_symbols(
            preset_id, self.config.symbol_ba, self.config.symbol_mt5
        )
        # 注：MT5 平仓统一走市价（对冲账户无法真正 Maker 平仓），不再按 order_mode 分流。
        if lots_override is not None and lots_override > 0:
            trade_lots = float(lots_override)
            close_all = False  # 精确回滚本次增量，绝不全平
        else:
            trade_lots = self.config.mt5_lot_for(preset_id)
        action_label = hedge_action_label("close", mode)
        quote = self._quotes.get(symbol_mt5, Quote(symbol=symbol_mt5))

        if not self.config.use_live_mt5:
            if symbol_mt5 not in self._demo_positions:
                return LegResult(platform="MT5", success=True, message="演示无持仓")
            demo_pos = self._demo_positions[symbol_mt5]
            lots_to_close = demo_pos.quantity if close_all else min(demo_pos.quantity, trade_lots)
            close_price = (
                quote.bid if demo_pos.side == Side.BUY else quote.ask
            ) or quote.mid
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
                    price=f"{close_price:.3f}" if close_price else "",
                ),
            )
            return LegResult(
                platform="MT5",
                success=True,
                message="演示平仓成功",
                order_id="demo-mt5-close",
                filled_quantity=float(lots_to_close),
                filled_price=float(close_price or 0),
            )

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

            # 对冲账户同方向可能有多张票据：close_all 全平所有票据；
            # 部分平仓按预算 trade_lots 跨票据依次平，直到平满预算。
            close_side_obj = Side.BUY if raw_positions[0].type == mt5.ORDER_TYPE_BUY else Side.SELL
            initial_total = sum(float(p.volume) for p in raw_positions)
            if close_all:
                trade_budget = initial_total
            else:
                requested = min(float(trade_lots), initial_total)
                trade_budget = self._normalize_mt5_volume(requested, info, cap=initial_total)
                if trade_budget <= 0:
                    return LegResult(
                        platform="MT5",
                        success=False,
                        message=(
                            f"Exness {action_label}失败: 换算手数 {float(trade_lots):g} "
                            "低于品种最小手数/步进，无法精确平仓"
                        ),
                    )
            budget = trade_budget
            target_remaining = 0.0 if close_all else max(0.0, initial_total - trade_budget)

            last_oid = ""
            order_ids: list[str] = []
            closed_total = 0.0
            filled_notional = 0.0
            history_start = time.time()
            for pos in raw_positions:
                if budget <= 1e-9:
                    break
                raw_lots_to_close = float(pos.volume) if close_all else min(float(pos.volume), budget)
                lots_to_close = self._normalize_mt5_volume(
                    raw_lots_to_close, info, cap=float(pos.volume)
                )
                if lots_to_close <= 0:
                    continue
                # 平仓统一走市价成交（TRADE_ACTION_DEAL + position 票据）。
                # MT5 的挂单(TRADE_ACTION_PENDING)不能携带 position 来平指定持仓，且
                # Exness 为对冲账户——反向限价单成交只会新开反向票据，无法减少原票据，
                # 故无法真正 Maker 平仓；market 平是唯一可靠原语，避免留单边敞口。
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
                    "comment": "hedge_close",
                    "type_time": mt5.ORDER_TIME_GTC,
                    "type_filling": get_mt5_filling_mode(info),
                }
                result = self._order_send_auto_filling(request, info)
                if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
                    detail = (
                        f"retcode={result.retcode} {translate_exchange_error(result.comment)}"
                        if result
                        else str(mt5.last_error())
                    )
                    return LegResult(
                        platform="MT5",
                        success=False,
                        message=f"Exness {action_label}失败: {detail}",
                    )
                last_oid = str(result.order)
                order_ids.append(last_oid)
                filled_price = self._result_filled_price(result, fallback=float(close_price or 0))
                closed_total += lots_to_close
                filled_notional += filled_price * lots_to_close
                budget -= lots_to_close

            if closed_total <= 0:
                return LegResult(platform="MT5", success=False, message="未找到可平仓位")
            avg_filled_price = filled_notional / closed_total if filled_notional > 0 else 0.0
            realized_pnl, fee, fee_known = self._fetch_deal_summary(
                symbol_mt5,
                order_ids,
                history_start if order_ids else time.time(),
            )

            # 按同方向「总手数」确认减仓到位（兼容多票据）
            if not self._wait_until_position_at_most(symbol_mt5, close_side_obj, target_remaining):
                return LegResult(
                    platform="MT5",
                    success=False,
                    message=f"Exness 平仓订单 {last_oid} 未确认减仓，请检查持仓",
                    order_id=last_oid,
                    needs_reconciliation=True,
                )
            self._log(
                LogLevel.TRADE,
                trade_leg_success_msg(
                    "Exness",
                    "close",
                    mode,
                    last_oid,
                    lots=str(closed_total),
                    price=f"{avg_filled_price:.3f}" if avg_filled_price > 0 else "",
                ),
            )
            return LegResult(
                platform="MT5",
                success=True,
                message=f"{action_label}成功",
                order_id=last_oid,
                filled_quantity=closed_total,
                filled_price=avg_filled_price,
                fee=fee,
                fee_known=fee_known,
                realized_pnl=realized_pnl,
                pnl_known=fee_known,
            )

        try:
            return self._call_on_mt5_thread(_close)
        except Exception as exc:
            msg = f"Exness {action_label}失败: {translate_exchange_error(exc)}"
            self._log(LogLevel.ERROR, msg)
            return LegResult(platform="MT5", success=False, message=msg)

    def _start_demo(self) -> None:
        """启动模拟行情：定时生成黄金/SPCXUSDT的虚拟报价。"""
        self._set_state(ConnectionState.SIMULATED)
        self.account_received.emit(AccountSnapshot(platform="MT5", is_live=False))
        self._log(LogLevel.DEBUG, "Exness 模拟行情 · 黄金 + SPCXUSDT（非真实价格）")
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
        """在 MT5 工作线程内排空任务队列，逐个执行并把结果/异常回传给调用方。"""
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
        """读取 MT5 账户杠杆（在工作线程上执行）。"""
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

    def _read_account_snapshot(self) -> AccountSnapshot | None:
        """在 MT5 工作线程内读取账户资金快照（结余/已用预付款/可用预付款）。"""
        if not self._connected or not HAS_MT5:
            return None
        info = mt5.account_info()
        if info is None:
            return None
        return AccountSnapshot(
            platform="MT5",
            balance=float(getattr(info, "balance", 0.0) or 0.0),
            used_margin=float(getattr(info, "margin", 0.0) or 0.0),
            free_margin=float(getattr(info, "margin_free", 0.0) or 0.0),
            equity=float(getattr(info, "equity", 0.0) or 0.0),
            currency=str(getattr(info, "currency", "") or ""),
            is_live=True,
            timestamp=time.time(),
        )

    def fetch_account_snapshot(self) -> AccountSnapshot | None:
        """同步读取 MT5 账户资金快照，并推送给 UI。"""
        snap = self._read_account_snapshot()
        if snap is not None:
            self._account_snapshot_at = time.monotonic()
            self.account_received.emit(snap)
        return snap

    def _poll_loop(self) -> None:
        """MT5 专用工作线程主循环：初始化登录后，循环处理任务队列并推送行情。"""
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
                # 账户资金快照：本地接口，约每 2 秒读取一次（MT5 无推送，只能轮询）
                if time.monotonic() - self._account_snapshot_at >= 2.0:
                    self._account_snapshot_at = time.monotonic()
                    try:
                        snap = self._read_account_snapshot()
                        if snap is not None:
                            self.account_received.emit(snap)
                    except Exception:
                        pass
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
        """更新连接状态并通知 UI。"""
        self._state = state
        self.state_changed.emit(state.value)
