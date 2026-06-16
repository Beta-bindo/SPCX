"""点差引擎：连接两端、计算点差/盈亏/风险、编排下单并向 UI 发信号。

SpreadEngine 是整个应用的核心枢纽，职责：
- 持有 Binance 与 MT5 两个连接器，订阅其报价并实时重建点差快照；
- 周期性轮询持仓、计算盈亏与风险，驱动告警；
- 提供对冲开/平仓入口（在后台线程执行，避免阻塞 UI）；
- 通过 Qt 信号把行情、持仓、交易结果、网络状态推送给界面。

线程模型：报价回调与定时器跑在 Qt 主线程；下单和持仓刷新放到守护线程，
结果通过信号（_positions_refresh_ready 等）切回主线程，保证线程安全。
"""

from __future__ import annotations

import threading
import time

from PySide6.QtCore import QObject, QTimer, Signal

from app.core.alerts import AlertService
from app.core.demo_market import align_sim_mt5_to_ba, spread_is_sane
from app.core.models import (
    AppConfig,
    ConnectionMode,
    ConnectionState,
    MarketUpdate,
    OpenOrder,
    Position,
    Quote,
    SpreadSnapshot,
)
from app.core.network_status import NetworkStatus
from app.core.pnl_calculator import (
    PnlSummary,
    build_spread_snapshot,
    calculate_pnl,
    estimate_trade_fees,
)
from app.core.risk import build_risk_snapshot
from app.core.symbols import WATCHED_PRESETS, find_preset, watched_ba_symbols
from app.core.app_log import LogLevel, hedge_mode_word, should_log
from app.core.trade_ledger import funding_period_start, hedge_sides, record_close_settlement, record_trade
from app.core.trade_result import HedgeTradeResult
from app.core.trading_service import close_hedge, open_hedge, position_entry_spread
from app.core.order_mode import order_mode_log_label
from app.connectors.binance_connector import BinanceConnector
from app.connectors.mt5_connector import MT5Connector


class SpreadEngine(QObject):
    """行情/交易编排引擎，对外通过下列信号与 UI 通信。"""

    market_updated = Signal(object)            # 行情刷新（MarketUpdate）
    connection_changed = Signal(str, str)      # (平台, 新状态)
    network_status_changed = Signal(object)    # 网络状态快照
    log_message = Signal(str)                  # 日志行
    positions_updated = Signal(list, object)   # (持仓列表, 盈亏汇总)
    open_orders_updated = Signal(list)         # 委托单列表 OpenOrder[]
    order_book_updated = Signal(str)           # BA 盘口已更新（symbol），驱动 UI 重绘订单簿
    account_updated = Signal(object)           # 账户资金快照（AccountSnapshot）
    trade_finished = Signal(object)            # 交易完成结果
    trade_started = Signal(str, str, str)      # (动作, 品种, 下单模式)
    alert_triggered = Signal(str)              # 告警文字
    trade_recorded = Signal(object)            # 成交/结算记录
    _positions_refresh_ready = Signal(list, object, object, list)  # 内部：后台刷新结果回主线程

    def __init__(self, config: AppConfig, parent=None):
        super().__init__(parent)
        self.config = config
        self._running = False
        self._positions: list[Position] = []
        self._open_orders: list[OpenOrder] = []
        self._last_summary = PnlSummary()
        self._ba_quotes: dict[str, Quote] = {}
        self._mt5_quotes: dict[str, Quote] = {}
        self._spreads: dict[str, SpreadSnapshot] = {}
        self._last_market_update = None   # 最近一次行情快照，供勾选后立即评估自动交易

        self.binance = BinanceConnector(config)
        self.mt5 = MT5Connector(config)
        self.alerts = AlertService(self)

        self.binance.quote_received.connect(self._on_ba_quote)
        self.binance.state_changed.connect(lambda s: self.connection_changed.emit("BA", s))
        self.binance.account_received.connect(self.account_updated.emit)
        self.binance.open_orders_detail.connect(self._on_ba_open_orders_detail)
        self.binance.order_book_updated.connect(self.order_book_updated.emit)
        self.binance.log.connect(self.log_message.emit)

        self.mt5.quote_received.connect(self._on_mt5_quote)
        self.mt5.state_changed.connect(lambda s: self.connection_changed.emit("MT5", s))
        self.mt5.account_received.connect(self.account_updated.emit)
        self.mt5.log.connect(self.log_message.emit)

        self.alerts.alert_triggered.connect(self.alert_triggered.emit)
        self.alerts.alert_triggered.connect(lambda m: self._log(LogLevel.INFO, m))

        self._poll_timer = QTimer(self)
        self._poll_timer.timeout.connect(self.refresh_positions)
        self._network_timer = QTimer(self)
        self._network_timer.timeout.connect(self._emit_network_status)
        self._trading = False
        self._refresh_inflight = False
        self._positions_refresh_ready.connect(self._apply_positions_refresh)
        self._spread_rebuild_timer = QTimer(self)
        self._spread_rebuild_timer.setSingleShot(True)
        self._spread_rebuild_timer.timeout.connect(self._rebuild_spreads_now)

    def _log(self, level: LogLevel, message: str) -> None:
        """按当前日志级别过滤后发出日志信号。"""
        if should_log(self.config.log_level, level):
            self.log_message.emit(message)

    def _seed_trade_positions_cache(self) -> None:
        """下单前把已知 BA 持仓预置进连接器缓存，减少热路径上的实时拉取。"""
        ba_positions = [p for p in self._positions if p.platform == "BA"]
        self.binance.seed_positions_cache(ba_positions)

    def open_hedge(
        self, preset_id: str, mode: str = "contraction", order_mode: str = "limit"
    ) -> None:
        """对外的开仓入口：前置校验通过后，在后台线程执行开仓。"""
        if self._trading:
            self._log(LogLevel.INFO, "交易进行中，请稍候")
            return
        error = self._trade_preflight_error(preset_id)
        if error:
            self._log(LogLevel.ERROR, error)
            return
        self._seed_trade_positions_cache()
        self._trading = True
        self.trade_started.emit("open", preset_id, order_mode)
        threading.Thread(
            target=self._run_open, args=(preset_id, mode, order_mode), daemon=True
        ).start()

    def close_hedge(
        self, preset_id: str, mode: str = "contraction", order_mode: str = "limit"
    ) -> None:
        """对外的平仓入口：前置校验通过后，在后台线程执行平仓。"""
        if self._trading:
            self._log(LogLevel.INFO, "交易进行中，请稍候")
            return
        error = self._trade_preflight_error(preset_id)
        if error:
            self._log(LogLevel.ERROR, error)
            return
        self._seed_trade_positions_cache()
        self._trading = True
        self.trade_started.emit("close", preset_id, order_mode)
        threading.Thread(
            target=self._run_close, args=(preset_id, mode, order_mode), daemon=True
        ).start()

    def _trade_preflight_error(self, preset_id: str) -> str | None:
        """实盘下单前置校验，返回拦截原因；通过则返回 None。

        实盘要求：连接模式为"实盘双端"、两端均已真实连接、且有非模拟的最新双端报价，
        以杜绝单边敞口与实盘/模拟混合下单。模拟模式直接放行。
        """
        if self.config.demo_mode:
            return None
        if self.config.connection_mode != ConnectionMode.LIVE_BOTH.value:
            return "交易已拦截：实盘对冲必须选择「实盘双端」，不能在仅 BA 或仅 Exness 模式下下单"
        if self.binance.state != ConnectionState.CONNECTED or self.mt5.state != ConnectionState.CONNECTED:
            return "交易已拦截：BA 与 Exness 必须同时真实连接后才能实盘下单"
        preset = find_preset(preset_id)
        ba_quote = self._ba_quotes.get(preset.symbol_ba)
        mt5_quote = self._mt5_quotes.get(preset.symbol_mt5)
        if not ba_quote or not mt5_quote:
            return "交易已拦截：缺少最新双端报价"
        if ba_quote.is_simulated or mt5_quote.is_simulated:
            return "交易已拦截：检测到模拟报价，禁止与实盘混合下单"
        return None

    def _spread_log(self, preset_id: str, prefix: str) -> None:
        snap = self._spreads.get(preset_id)
        if snap is None:
            return
        self._log(
            LogLevel.TRADE,
            f"{prefix} · 点差指数 {snap.mid_spread:+.3f} "
            f"(BA {snap.ba_bid:.3f} / Ex {snap.mt5_bid:.3f})",
        )

    def _order_snapshot(self, preset_id: str) -> tuple[float, float, float]:
        snap = self._spreads.get(preset_id)
        if snap is None:
            return 0.0, 0.0, 0.0
        return snap.mid_spread, snap.ba_bid, snap.mt5_bid

    def _order_quantities(self, preset_id: str) -> tuple[float, float]:
        return (
            self.config.ba_quantity_for(preset_id),
            self.config.mt5_lot_for(preset_id),
        )

    def _settlement_positions(
        self, preset_id: str
    ) -> tuple[Position | None, Position | None]:
        """用最近一次轮询的持仓快照 + 本地行情 mark 计算平仓前盈亏。

        复用引擎已缓存的 self._positions，避免在点击平仓瞬间做一次实时
        MT5 持仓拉取（IPC 阻塞），保证下单不卡顿。
        """
        preset = find_preset(preset_id)
        raw: list[Position] = list(self._positions)
        if not raw:
            return None, None
        updated, _ = calculate_pnl(
            raw,
            dict(self._ba_quotes),
            dict(self._mt5_quotes),
            self.config,
            self._spreads.get(preset_id),
        )
        ba_pos = next(
            (p for p in updated if p.platform == "BA" and p.symbol == preset.symbol_ba),
            None,
        )
        mt5_pos = next(
            (p for p in updated if p.platform == "MT5" and p.symbol == preset.symbol_mt5),
            None,
        )
        return ba_pos, mt5_pos

    def _run_open(self, preset_id: str, mode: str, order_mode: str) -> None:
        """后台线程：执行开仓、记录成交、刷新持仓，并发出相应信号。"""
        finished = False
        try:
            om = order_mode_log_label(preset_id, order_mode)
            self._log(
                LogLevel.TRADE,
                f"正在{hedge_mode_word(mode)}开仓 · {om}",
            )
            self._spread_log(preset_id, "下单前")
            spread, ba_price, ex_price = self._order_snapshot(preset_id)
            ba_qty, mt5_qty = self._order_quantities(preset_id)
            ba_side, mt5_side = hedge_sides(mode)
            preset = find_preset(preset_id)
            had_position = any(
                p.quantity > 0
                and (
                    (p.platform == "BA" and p.symbol == preset.symbol_ba)
                    or (p.platform == "MT5" and p.symbol == preset.symbol_mt5)
                )
                for p in self._positions
            )
            result = open_hedge(
                self.binance,
                self.mt5,
                preset_id,
                mode,
                order_mode,
                had_position=had_position,
            )
            self._log(LogLevel.TRADE, result.message)
            if result.success:
                self._spread_log(preset_id, "成交后")
                ba_leg = next((leg for leg in result.legs if leg.platform == "BA"), None)
                mt5_leg = next((leg for leg in result.legs if leg.platform == "MT5"), None)
                actual_ba_qty = ba_leg.filled_quantity if ba_leg and ba_leg.filled_quantity > 0 else ba_qty
                actual_mt5_qty = (
                    mt5_leg.filled_quantity if mt5_leg and mt5_leg.filled_quantity > 0 else mt5_qty
                )
                ba_fee, mt5_fee = estimate_trade_fees(
                    preset_id,
                    self.config,
                    ba_price=ba_price,
                    ba_quantity=actual_ba_qty,
                    mt5_quantity=actual_mt5_qty,
                )
                rec = record_trade(
                    preset_id,
                    mode,
                    "open",
                    spread=spread,
                    ba_price=ba_price,
                    ex_price=ex_price,
                    ba_quantity=actual_ba_qty,
                    mt5_quantity=actual_mt5_qty,
                    ba_side=ba_side,
                    mt5_side=mt5_side,
                    ba_fee=ba_fee,
                    mt5_fee=mt5_fee,
                )
                label = "黄金" if preset_id == "xau" else "白银"
                mlabel = "收缩" if mode == "contraction" else "扩张"
                self._log(
                    LogLevel.TRADE,
                    f"【上报】{label} 开仓{mlabel} · 点差 {rec.spread:+.3f} "
                    f"BA {rec.ba_price:.3f} / Ex {rec.ex_price:.3f}",
                )
                self.trade_recorded.emit(rec)
                preset = find_preset(preset_id)
                ba_pos = next(
                    (
                        p
                        for p in self.binance.get_positions()
                        if p.symbol == preset.symbol_ba
                    ),
                    None,
                )
                mt5_pos = next(
                    (
                        p
                        for p in self.mt5.get_positions()
                        if p.symbol == preset.symbol_mt5
                    ),
                    None,
                )
                entry_spread = position_entry_spread(ba_pos, mt5_pos)
                if entry_spread is not None:
                    self._log(LogLevel.TRADE, f"持仓入场点差指数 {entry_spread:+.3f}")
            self.trade_finished.emit(result)
            finished = True
            self.refresh_positions()
        except Exception as exc:  # noqa: BLE001 — 兜底：异常也要让 UI 解锁
            self._log(LogLevel.ERROR, f"开仓异常：{exc}")
        finally:
            self._trading = False
            if not finished:
                self.trade_finished.emit(
                    HedgeTradeResult(action="open", success=False, message="开仓异常")
                )

    def _run_close(self, preset_id: str, mode: str, order_mode: str) -> None:
        """后台线程：执行平仓、按平仓比例结算盈亏并记账，最后刷新持仓。"""
        finished = False
        try:
            om = order_mode_log_label(preset_id, order_mode)
            self._log(
                LogLevel.TRADE,
                f"正在{hedge_mode_word(mode)}平仓 · {om}",
            )
            self._spread_log(preset_id, "平仓前")
            spread, ba_price, ex_price = self._order_snapshot(preset_id)
            ba_qty_cfg, mt5_qty_cfg = self._order_quantities(preset_id)
            ba_side, mt5_side = hedge_sides(mode)
            ba_pos, mt5_pos = self._settlement_positions(preset_id)
            result = close_hedge(self.binance, self.mt5, preset_id, mode, order_mode)
            self._log(LogLevel.TRADE, result.message)
            if result.success:
                self._spread_log(preset_id, "平仓后")
            if result.success and (ba_pos or mt5_pos):
                close_ba_qty = (
                    min(self.config.ba_quantity_for(preset_id), ba_pos.quantity)
                    if ba_pos
                    else 0.0
                )
                close_mt5_qty = (
                    min(self.config.mt5_lot_for(preset_id), mt5_pos.quantity)
                    if mt5_pos
                    else 0.0
                )

                def _scaled(pos: Position | None, close_qty: float) -> tuple[float, float]:
                    # 部分平仓时按平仓比例折算应结算的盈亏与手续费
                    if not pos or pos.quantity <= 0 or close_qty <= 0:
                        return 0.0, 0.0
                    ratio = min(1.0, close_qty / pos.quantity)
                    return (
                        round(pos.unrealized_pnl * ratio, 2),
                        round(pos.estimated_fee * ratio, 4),
                    )

                ba_pnl, ba_fee = _scaled(ba_pos, close_ba_qty)
                mt5_pnl, mt5_fee = _scaled(mt5_pos, close_mt5_qty)
                ba_funding_fee = 0.0
                ba_rebate = 0.0
                if ba_pos and close_ba_qty > 0 and self.config.use_live_ba:
                    anchor = funding_period_start(preset_id, mode)
                    if anchor is not None:
                        preset = find_preset(preset_id)
                        start_ms = int(anchor.timestamp() * 1000)
                        end_ms = int(time.time() * 1000)
                        ratio = (
                            min(1.0, close_ba_qty / ba_pos.quantity)
                            if ba_pos.quantity > 0
                            else 1.0
                        )
                        raw_funding = self.binance.fetch_funding_income(
                            preset.symbol_ba, start_ms, end_ms
                        )
                        raw_rebate = self.binance.fetch_rebate_income(
                            preset.symbol_ba, start_ms, end_ms
                        )
                        ba_funding_fee = round(raw_funding * ratio, 4)
                        ba_rebate = round(raw_rebate * ratio, 4)
                rec = record_close_settlement(
                    preset_id,
                    mode,
                    ba_pnl,
                    mt5_pnl,
                    ba_fee,
                    mt5_fee,
                    ba_funding_fee,
                    ba_rebate,
                    spread=spread,
                    ba_price=ba_price,
                    ex_price=ex_price,
                    ba_quantity=close_ba_qty or ba_qty_cfg,
                    mt5_quantity=close_mt5_qty or mt5_qty_cfg,
                    ba_side=ba_side,
                    mt5_side=mt5_side,
                )
                label = "黄金" if preset_id == "xau" else "白银"
                mlabel = "收缩" if mode == "contraction" else "扩张"
                self._log(LogLevel.TRADE, f"【结算】{label} 平仓{mlabel} · 净利 {rec.net_pnl:+.2f}")
                self.trade_recorded.emit(rec)
            self.trade_finished.emit(result)
            finished = True
            self.refresh_positions()
        except Exception as exc:  # noqa: BLE001 — 兜底：异常也要让 UI 解锁
            self._log(LogLevel.ERROR, f"平仓异常：{exc}")
        finally:
            self._trading = False
            if not finished:
                self.trade_finished.emit(
                    HedgeTradeResult(action="close", success=False, message="平仓异常")
                )

    def _position_poll_ms(self) -> int:
        """持仓轮询间隔（毫秒），随报价刷新间隔联动，但不低于 4 秒。"""
        return max(4000, int(round(self.config.ba_refresh_interval_sec * 4000)))

    def start(self) -> None:
        """启动两端连接、持仓轮询与网络监控，并延迟同步平台杠杆。"""
        if self._running:
            return
        self._running = True
        mode_labels = {
            "demo": "演示模式",
            "live_both": "实盘双端",
            "live_ba": "仅 BA 实盘",
            "live_mt5": "仅 Exness 实盘",
        }
        self._log(LogLevel.INFO, f"启动连接 · {mode_labels.get(self.config.connection_mode, '未知')}")
        self._ba_quotes.clear()
        self._mt5_quotes.clear()
        self._spreads.clear()
        self.binance.start()
        self.mt5.start()
        self._poll_timer.start(self._position_poll_ms())
        self._network_timer.start(1000)
        self._emit_network_status()
        if not self.config.demo_mode:
            QTimer.singleShot(3000, self._sync_platform_leverage)

    def _sync_platform_leverage(self) -> None:
        """从交易所读取实际杠杆并回写配置，使风险估算更贴近真实。"""
        if not self._running:
            return
        threading.Thread(
            target=self._sync_platform_leverage_worker,
            daemon=True,
            name="sync-platform-leverage",
        ).start()

    def _sync_platform_leverage_worker(self) -> None:
        """后台读取平台杠杆；避免 BA/MT5 连接异常时阻塞 UI 线程。"""
        changed = False
        if not self.config.sync_leverage_on_trade:
            try:
                self.binance.get_positions(force=True)
                for sym in watched_ba_symbols():
                    lev = self.binance._symbol_leverage.get(sym)
                    if lev and lev != self.config.ba_leverage:
                        self.config.ba_leverage = lev
                        changed = True
                        break
            except Exception as exc:
                self._log(LogLevel.DEBUG, f"同步 BA 杠杆失败: {exc}")
        try:
            lev_mt5 = self.mt5.read_account_leverage()
        except Exception as exc:
            self._log(LogLevel.DEBUG, f"同步 Ex 杠杆失败: {exc}")
            lev_mt5 = None
        if lev_mt5 and lev_mt5 != self.config.mt5_leverage:
            self.config.mt5_leverage = lev_mt5
            changed = True
        if changed and self._running:
            self._log(
                LogLevel.INFO,
                f"已同步平台杠杆 · BA {self.config.ba_leverage}x · Ex {self.config.mt5_leverage}x",
            )

    def stop(self) -> None:
        """停止连接与所有定时器，清空缓存并静音告警。"""
        if not self._running:
            return
        self._running = False
        self._poll_timer.stop()
        self._network_timer.stop()
        self.binance.stop()
        self.mt5.stop()
        self._ba_quotes.clear()
        self._mt5_quotes.clear()
        self._spreads.clear()
        self._open_orders.clear()
        self.open_orders_updated.emit([])
        self.alerts.stop()
        self._emit_network_status()
        self._log(LogLevel.INFO, "已停止连接")

    @property
    def is_trading(self) -> bool:
        return self._trading

    @property
    def is_running(self) -> bool:
        return self._running

    def reevaluate_alerts(self) -> None:
        """配置变更后立即按当前行情重判告警（无需等下一轮）。"""
        if not self._running:
            return
        risk = build_risk_snapshot(
            self._positions, self._ba_quotes, self._mt5_quotes, self.config
        )
        self.alerts.evaluate(self.config, self._spreads, risk)

    def sync_config(self, config: AppConfig) -> None:
        """热更新配置：不重启连接，仅同步连接器参数与轮询间隔。"""
        self.config = config
        self.binance.update_config(config)
        self.mt5.update_config(config)
        if self._poll_timer.isActive():
            self._poll_timer.setInterval(self._position_poll_ms())

    def update_config(self, config: AppConfig) -> None:
        """重型更新配置：若在运行则先停后启，使连接参数完全生效。"""
        was_running = self._running
        if was_running:
            self.stop()
        self.config = config
        self.binance.update_config(config)
        self.mt5.update_config(config)
        if was_running:
            self.start()

    def refresh_positions(self) -> None:
        """触发一次后台持仓刷新；用 _refresh_inflight 去重，避免并发重入。"""
        if not self._running or self._refresh_inflight:
            return
        self._refresh_inflight = True
        ba_quotes = dict(self._ba_quotes)
        mt5_quotes = dict(self._mt5_quotes)
        primary = self._spreads.get("xau")
        config = self.config

        def _work() -> None:
            try:
                positions: list[Position] = []
                positions.extend(self.binance.get_positions(force=True))
                positions.extend(self.mt5.get_positions())
                open_orders: list[OpenOrder] = []
                open_orders.extend(self.binance.get_open_orders())
                open_orders.extend(self.mt5.get_open_orders())
                updated, summary = calculate_pnl(
                    positions, ba_quotes, mt5_quotes, config, primary
                )
                risk = build_risk_snapshot(updated, ba_quotes, mt5_quotes, config)
                self._positions_refresh_ready.emit(updated, summary, risk, open_orders)
            except Exception as exc:
                self._log(LogLevel.ERROR, f"刷新持仓失败: {exc}")
            finally:
                self._refresh_inflight = False

        threading.Thread(target=_work, daemon=True, name="refresh-positions").start()

    def cancel_all_open_orders(self) -> None:
        """后台撤销 BA 全部未成交委托，完成后刷新一次持仓与委托。"""
        if not self._running:
            return

        def _work() -> None:
            try:
                count = self.binance.cancel_all_open_orders()
                if count > 0:
                    self._log(LogLevel.TRADE, f"手动撤单 · 已撤销 {count} 笔委托")
                else:
                    self._log(LogLevel.INFO, "手动撤单 · 当前无可撤委托")
            except Exception as exc:
                self._log(LogLevel.ERROR, f"手动撤单失败: {exc}")
            finally:
                self.refresh_positions()

        threading.Thread(target=_work, daemon=True, name="cancel-all-orders").start()

    def _apply_positions_refresh(
        self, updated: list[Position], summary: PnlSummary, risk, open_orders: list[OpenOrder]
    ) -> None:
        """主线程槽：接收后台刷新结果，更新缓存、重判告警并推送 UI。"""
        self._positions = updated
        self._open_orders = open_orders
        self._last_summary = summary
        self.alerts.evaluate(self.config, self._spreads, risk)
        self.positions_updated.emit(updated, summary)
        self.open_orders_updated.emit(open_orders)
        self._emit_market(risk)

    def _on_ba_open_orders_detail(self, ba_orders: list) -> None:
        """BA User Data Stream 推送的带数量委托快照：立即合并 MT5 上次快照并推送 UI。

        实现"WS 速度 + 真实数量"：BA 端用推送的最新委托（含总量/已成交/剩余）即时刷新，
        MT5 端无私有推送，沿用最近一次 REST 轮询结果，待下一轮持仓刷新补齐校正。
        """
        if not self._running:
            return
        mt5_orders = [o for o in self._open_orders if o.platform != "BA"]
        merged = list(ba_orders) + mt5_orders
        self._open_orders = merged
        self.open_orders_updated.emit(merged)

    @property
    def last_summary(self) -> PnlSummary:
        return self._last_summary

    @property
    def positions(self) -> list[Position]:
        return list(self._positions)

    @property
    def open_orders(self) -> list[OpenOrder]:
        return list(self._open_orders)

    @property
    def ba_order_books(self) -> dict:
        return self.binance.order_books

    def ba_order_book(self, symbol: str):
        return self.binance.order_book(symbol)

    @property
    def ba_quotes(self) -> dict[str, Quote]:
        return self._ba_quotes

    @property
    def mt5_quotes(self) -> dict[str, Quote]:
        return self._mt5_quotes

    @property
    def spreads(self) -> dict[str, SpreadSnapshot]:
        return self._spreads

    def _on_ba_quote(self, quote: Quote) -> None:
        # 缓存最新报价，并以 80ms 防抖合并两端高频报价后统一重建点差
        self._ba_quotes[quote.symbol] = quote
        if not self._spread_rebuild_timer.isActive():
            self._spread_rebuild_timer.start(80)

    def _on_mt5_quote(self, quote: Quote) -> None:
        self._mt5_quotes[quote.symbol] = quote
        if not self._spread_rebuild_timer.isActive():
            self._spread_rebuild_timer.start(80)

    def _rebuild_spreads_now(self) -> None:
        """重建所有受监控品种的点差快照，剔除异常值后重判告警并推送 UI。"""
        for preset_id in WATCHED_PRESETS:
            preset = find_preset(preset_id)
            ba = self._ba_quotes.get(preset.symbol_ba)
            mt5 = self._mt5_quotes.get(preset.symbol_mt5)
            if ba and mt5:
                mt5 = align_sim_mt5_to_ba(
                    ba,
                    mt5,
                    preset_id,
                    interval_sec=self.config.ba_refresh_interval_sec,
                )
                if mt5 is not self._mt5_quotes.get(preset.symbol_mt5):
                    self._mt5_quotes[preset.symbol_mt5] = mt5
                snap = build_spread_snapshot(ba, mt5, preset_id)
                if snap and spread_is_sane(preset_id, snap.mid_spread):
                    self._spreads[preset_id] = snap
                else:
                    self._spreads.pop(preset_id, None)
            else:
                self._spreads.pop(preset_id, None)
        risk = build_risk_snapshot(self._positions, self._ba_quotes, self._mt5_quotes, self.config)
        self.alerts.evaluate(self.config, self._spreads, risk)
        self._emit_market(risk)

    def _emit_market(self, risk) -> None:
        """打包当前报价/点差/风险为 MarketUpdate 并发给 UI。"""
        update = MarketUpdate(
            ba_quotes=dict(self._ba_quotes),
            mt5_quotes=dict(self._mt5_quotes),
            spreads=dict(self._spreads),
            risk=risk,
        )
        self._last_market_update = update
        self.market_updated.emit(update)

    @property
    def last_market_update(self):
        """最近一次行情快照（无则 None）；供勾选自动交易后立即评估一次。"""
        return self._last_market_update

    def _emit_network_status(self) -> None:
        self.network_status_changed.emit(NetworkStatus.from_engine(self, self._running))
