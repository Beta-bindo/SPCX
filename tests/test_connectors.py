import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.models import (
    AppConfig,
    ConnectionMode,
    GoldOrderMode,
    HedgeMode,
    OpenOrder,
    OrderBook,
    OrderBookLevel,
    Position,
    Quote,
    Side,
)
from app.core.exchange_utils import format_binance_price
from app.core.trading_service import close_hedge, open_hedge
from app.connectors.binance_connector import BinanceConnector
from app.connectors.mt5_connector import MT5Connector


def test_binance_uses_futures_ping():
    cfg = AppConfig(
        connection_mode=ConnectionMode.LIVE_BA.value,
        ba_api_key="key",
        ba_api_secret="secret",
    )
    conn = BinanceConnector(cfg)
    client = MagicMock()
    client.futures_ping.return_value = {}
    client.futures_order_book.return_value = {
        "bids": [["2650.0", "1"]],
        "asks": [["2650.2", "1"]],
    }
    conn._client = client
    conn._stop_event.set()

    with patch.object(conn, "_create_client", return_value=client):
        conn._poll_loop()

    client.futures_ping.assert_called_once()
    client.ping.assert_not_called()
    print("  ✓ BA 使用 futures_ping 而非 spot ping")


def test_binance_partial_close_demo():
    cfg = AppConfig(
        connection_mode=ConnectionMode.DEMO.value,
        xau_trade_lots=1.0,
        xau_ba_qty_map=500.0,
    )
    conn = BinanceConnector(cfg)
    conn._quotes["XAUUSDT"] = Quote(
        symbol="XAUUSDT", bid=2650.0, ask=2650.2, is_simulated=True
    )
    assert conn.open_hedge_leg("xau").success
    assert conn.open_hedge_leg("xau").success
    assert conn.get_positions()[0].quantity == 1000.0

    assert conn.close_hedge_leg("xau", mode="contraction").success
    assert conn.get_positions()[0].quantity == 500.0
    print("  ✓ BA 演示分批平仓")


def test_binance_open_close_demo():
    cfg = AppConfig(connection_mode=ConnectionMode.DEMO.value, xau_trade_lots=1.0, xau_ba_qty_map=500.0)
    conn = BinanceConnector(cfg)
    conn._quotes["XAUUSDT"] = Quote(
        symbol="XAUUSDT", bid=2650.0, ask=2650.2, is_simulated=True
    )

    open_result = conn.open_hedge_leg("xau")
    assert open_result.success
    positions = conn.get_positions()
    assert len(positions) == 1
    assert positions[0].side == Side.SELL
    assert positions[0].quantity == 500.0

    close_result = conn.close_hedge_leg("xau")
    assert close_result.success
    assert conn.get_positions() == []
    print("  ✓ BA 演示开平仓")


def test_mt5_thread_safe_get_positions():
    cfg = AppConfig(connection_mode=ConnectionMode.LIVE_MT5.value)
    conn = MT5Connector(cfg)
    conn._connected = True
    conn._poll_thread = MagicMock()

    import app.connectors.mt5_connector as mt5_mod

    fake_pos = MagicMock()
    fake_pos.type = 0
    fake_pos.symbol = "XAUUSD"
    fake_pos.volume = 0.01
    fake_pos.price_open = 2649.0
    fake_pos.profit = 1.5
    mt5_mock = MagicMock()
    mt5_mock.ORDER_TYPE_BUY = 0

    def _positions_get(symbol=None):
        if symbol == "XAUUSD":
            return [fake_pos]
        return []

    mt5_mock.positions_get.side_effect = _positions_get

    def fake_call(fn, timeout=30.0):
        return fn()

    with patch.object(mt5_mod, "HAS_MT5", True):
        with patch.object(mt5_mod, "mt5", mt5_mock, create=True):
            with patch.object(conn, "_call_on_mt5_thread", side_effect=fake_call):
                positions = conn.get_positions()

    assert len(positions) == 1
    assert positions[0].platform == "MT5"
    print("  ✓ MT5 持仓经工作线程队列")


def test_mt5_light_positions_skip_account_risk_calc():
    cfg = AppConfig(connection_mode=ConnectionMode.LIVE_MT5.value)
    conn = MT5Connector(cfg)
    conn._connected = True
    conn._poll_thread = MagicMock()

    import app.connectors.mt5_connector as mt5_mod

    fake_pos = MagicMock()
    fake_pos.type = 0
    fake_pos.symbol = "XAUUSD"
    fake_pos.volume = 0.01
    fake_pos.price_open = 2649.0
    fake_pos.price_current = 2650.0
    fake_pos.profit = 1.5

    mt5_mock = MagicMock()
    mt5_mock.ORDER_TYPE_BUY = 0
    mt5_mock.account_info.side_effect = AssertionError("light refresh must skip account_info")
    mt5_mock.positions_get.side_effect = lambda symbol=None: [fake_pos] if symbol == "XAUUSD" else []

    def fake_call(fn, timeout=30.0):
        return fn()

    with patch.object(mt5_mod, "HAS_MT5", True):
        with patch.object(mt5_mod, "mt5", mt5_mock, create=True):
            with patch.object(conn, "_call_on_mt5_thread", side_effect=fake_call):
                positions = conn.get_positions(include_risk=False)

    assert len(positions) == 1
    assert positions[0].unrealized_pnl == 1.5
    mt5_mock.account_info.assert_not_called()
    print("  ✓ MT5 盈亏快刷跳过账户风险计算")


def test_mt5_live_positions_merge_tickets_and_sum_profit():
    cfg = AppConfig(connection_mode=ConnectionMode.LIVE_MT5.value)
    conn = MT5Connector(cfg)
    conn._connected = True
    conn._poll_thread = MagicMock()

    import app.connectors.mt5_connector as mt5_mod

    def _pos(price: float, profit: float):
        fake = MagicMock()
        fake.type = 0
        fake.symbol = "XAUUSD"
        fake.volume = 0.01
        fake.price_open = price
        fake.price_current = 4329.0
        fake.profit = profit
        return fake

    mt5_mock = MagicMock()
    mt5_mock.ORDER_TYPE_BUY = 0
    mt5_mock.ORDER_TYPE_SELL = 1
    mt5_mock.account_info.return_value = None

    def _positions_get(symbol=None):
        if symbol == "XAUUSD":
            return [
                _pos(4330.0, -1.17),
                _pos(4329.5, -0.28),
                _pos(4330.2, -1.00),
                _pos(4329.4, -0.52),
            ]
        return []

    mt5_mock.positions_get.side_effect = _positions_get

    def fake_call(fn, timeout=30.0):
        return fn()

    with patch.object(mt5_mod, "HAS_MT5", True):
        with patch.object(mt5_mod, "mt5", mt5_mock, create=True):
            with patch.object(conn, "_call_on_mt5_thread", side_effect=fake_call):
                positions = conn.get_positions()

    assert len(positions) == 1
    pos = positions[0]
    assert pos.platform == "MT5"
    assert pos.symbol == "XAUUSD"
    assert pos.quantity == 0.04
    assert round(pos.entry_price, 3) == round((4330.0 + 4329.5 + 4330.2 + 4329.4) / 4, 3)
    assert pos.unrealized_pnl == round(-1.17 - 0.28 - 1.00 - 0.52, 2)
    print("  ✓ MT5 多票据按方向合并并累加官方盈亏")


def test_mt5_open_close_demo():
    cfg = AppConfig(connection_mode=ConnectionMode.DEMO.value, xau_trade_lots=0.05)
    conn = MT5Connector(cfg)
    conn._quotes["XAUUSD"] = Quote(
        symbol="XAUUSD", bid=2649.0, ask=2649.2, is_simulated=True
    )

    open_result = conn.open_hedge_leg("xau")
    assert open_result.success
    assert conn.get_positions()[0].quantity == 0.05

    close_result = conn.close_hedge_leg("xau")
    assert close_result.success
    assert conn.get_positions() == []
    print("  ✓ MT5 演示开平仓")


def test_hedge_open_both_demo():
    cfg = AppConfig(connection_mode=ConnectionMode.DEMO.value)
    ba = BinanceConnector(cfg)
    mt5 = MT5Connector(cfg)
    ba._quotes["XAUUSDT"] = Quote("XAUUSDT", 2650, 2650.2, is_simulated=True)
    mt5._quotes["XAUUSD"] = Quote("XAUUSD", 2649, 2649.2, is_simulated=True)

    result = open_hedge(ba, mt5, "xau")
    assert result.success
    assert len(result.legs) == 2
    assert ba.get_positions() and mt5.get_positions()

    result = close_hedge(ba, mt5, "xau")
    assert result.success
    assert not ba.get_positions() and not mt5.get_positions()
    print("  ✓ 一键开平仓（演示双端）")


def test_hedge_expansion_demo():
    from app.core.models import HedgeMode

    cfg = AppConfig(connection_mode=ConnectionMode.DEMO.value)
    ba = BinanceConnector(cfg)
    mt5 = MT5Connector(cfg)
    ba._quotes["XAUUSDT"] = Quote("XAUUSDT", 2650, 2650.2, is_simulated=True)
    mt5._quotes["XAUUSD"] = Quote("XAUUSD", 2649, 2649.2, is_simulated=True)

    result = open_hedge(ba, mt5, "xau", HedgeMode.EXPANSION.value)
    assert result.success
    assert ba.get_positions()[0].side == Side.BUY
    assert mt5.get_positions()[0].side == Side.SELL
    print("  ✓ 扩张开仓 BA多+Ex空")


def test_binance_get_positions_uses_lock():
    cfg = AppConfig(connection_mode=ConnectionMode.LIVE_BA.value, ba_api_key="k", ba_api_secret="s")
    conn = BinanceConnector(cfg)
    client = MagicMock()

    client.futures_position_information.return_value = [
        {
            "positionAmt": "-0.01",
            "entryPrice": "2650",
            "unRealizedProfit": "1.2",
            "symbol": "XAUUSDT",
            "leverage": "20",
            "liquidationPrice": "2800",
            "markPrice": "2640",
            "marginType": "isolated",
            "isolatedWallet": "100",
            "maintMargin": "10",
        }
    ]
    conn._client = client

    positions = conn.get_positions(force=True)
    assert len(positions) == 1
    assert positions[0].side == Side.SELL
    assert positions[0].liquidation_price == 2800.0
    assert positions[0].exchange_liq_buffer == 91.2
    assert client.futures_position_information.call_count == 1

    positions_cached = conn.get_positions()
    assert len(positions_cached) == 1
    assert client.futures_position_information.call_count == 1
    print("  ✓ BA 持仓查询 futures API")


def test_binance_reads_account_commission_rate_and_order_fee():
    cfg = AppConfig(connection_mode=ConnectionMode.LIVE_BA.value, ba_api_key="k", ba_api_secret="s")
    conn = BinanceConnector(cfg)
    client = MagicMock()
    client.futures_commission_rate.return_value = {
        "symbol": "XAUUSDT",
        "makerCommissionRate": "0.0002",
        "takerCommissionRate": "0.00035",
    }
    client.futures_account_trades.return_value = [
        {"orderId": 123, "commission": "0.12", "commissionAsset": "USDT", "realizedPnl": "1.25"},
        {"orderId": 123, "commission": "0.03", "commissionAsset": "USDT", "realizedPnl": "-0.25"},
    ]
    conn._client = client

    rate = conn.fetch_user_commission_rate("XAUUSDT")
    pnl, summary_fee, summary_known = conn._fetch_order_trade_summary("XAUUSDT", "123")
    fee, known = conn._fetch_order_commission("XAUUSDT", "123")

    assert rate == (0.0002, 0.00035)
    assert conn.sync_user_commission_rates(["XAUUSDT"]) == 0.00035
    assert summary_known is True
    assert pnl == 1.0
    assert summary_fee == 0.15
    assert known is True
    assert fee == 0.15
    print("  ✓ BA 读取账户费率与订单真实 commission")


def test_cancel_all_open_orders_cancels_pending():
    cfg = AppConfig(connection_mode=ConnectionMode.LIVE_BA.value, ba_api_key="k", ba_api_secret="s")
    conn = BinanceConnector(cfg)
    client = MagicMock()
    conn._client = client
    conn._open_orders_cache = [
        OpenOrder(
            platform="BA",
            symbol="XAUUSDT",
            order_id="123",
            side=Side.BUY,
            order_type="LIMIT",
            total_quantity=0.01,
            remaining_quantity=0.01,
            price=2650.0,
        )
    ]

    count = conn.cancel_all_open_orders()

    assert count == 1
    client.futures_get_open_orders.assert_not_called()
    client.futures_cancel_all_open_orders.assert_any_call(symbol="XAUUSDT")
    client.futures_cancel_all_open_orders.assert_any_call(symbol="XAGUSDT")
    assert client.futures_cancel_all_open_orders.call_count == 2
    print("  ✓ 手动撤单：撤销委托中的挂单")


def test_cancel_all_open_orders_clears_ui_before_rest_cancel_returns():
    cfg = AppConfig(connection_mode=ConnectionMode.LIVE_BA.value, ba_api_key="k", ba_api_secret="s")
    conn = BinanceConnector(cfg)
    client = MagicMock()
    conn._client = client
    conn._open_order_symbols = frozenset({"XAUUSDT"})
    conn._stream_active_orders = {
        "XAUUSDT": {
            "123": OpenOrder(
                platform="BA",
                symbol="XAUUSDT",
                order_id="123",
                side=Side.BUY,
                order_type="LIMIT",
                total_quantity=0.01,
                remaining_quantity=0.01,
                price=2650.0,
            )
        }
    }
    events: list[tuple[str, object]] = []
    conn.open_orders_detail.connect(lambda orders: events.append(("detail", list(orders))))

    def _cancel(symbol=None):
        events.append(("cancel", symbol))
        return {}

    client.futures_cancel_all_open_orders.side_effect = _cancel

    count = conn.cancel_all_open_orders()

    assert count == 1
    assert events[0] == ("detail", [])
    assert ("cancel", "XAUUSDT") in events
    assert conn._open_orders_cache == []
    assert conn._stream_active_orders == {}
    print("  ✓ 手动撤单：先熄灭委托灯，再后台请求 BA 撤单")


def test_cancel_all_open_orders_noop_without_pending():
    cfg = AppConfig(connection_mode=ConnectionMode.LIVE_BA.value, ba_api_key="k", ba_api_secret="s")
    conn = BinanceConnector(cfg)
    client = MagicMock()
    client.futures_get_open_orders.return_value = []
    conn._client = client

    count = conn.cancel_all_open_orders()

    assert count == 0
    client.futures_cancel_all_open_orders.assert_any_call(symbol="XAUUSDT")
    client.futures_cancel_all_open_orders.assert_any_call(symbol="XAGUSDT")
    assert client.futures_cancel_all_open_orders.call_count == 2
    print("  ✓ 手动撤单：无本地挂单时仍兜底调用撤单接口")


def test_rest_open_orders_snapshot_clears_stale_stream_order():
    cfg = AppConfig(connection_mode=ConnectionMode.LIVE_BA.value, ba_api_key="k", ba_api_secret="s")
    conn = BinanceConnector(cfg)
    client = MagicMock()
    client.futures_get_open_orders.return_value = []
    conn._client = client
    stale = OpenOrder(
        platform="BA",
        symbol="XAUUSDT",
        order_id="123",
        side=Side.SELL,
        order_type="LIMIT",
        total_quantity=1.0,
        remaining_quantity=1.0,
        price=4363.16,
    )
    conn._open_order_symbols = frozenset({"XAUUSDT"})
    conn._open_orders_cache = [stale]
    conn._stream_active_orders = {"XAUUSDT": {"123": stale}}
    symbols_events: list[object] = []
    detail_events: list[list[OpenOrder]] = []
    conn.open_orders_changed.connect(symbols_events.append)
    conn.open_orders_detail.connect(lambda orders: detail_events.append(list(orders)))

    conn._poll_open_orders({"XAUUSDT", "XAGUSDT"})

    assert conn._open_orders_cache == []
    assert conn._stream_active_orders == {}
    assert conn._open_order_symbols == frozenset()
    assert symbols_events[-1] == frozenset()
    assert detail_events[-1] == []
    print("  ✓ REST 委托快照为空时清掉旧 WS 委托缓存")


def test_cancel_all_open_orders_skips_when_not_live():
    cfg = AppConfig(connection_mode=ConnectionMode.DEMO.value)
    conn = BinanceConnector(cfg)
    conn._client = MagicMock()

    assert conn.cancel_all_open_orders() == 0
    print("  ✓ 手动撤单：非实盘直接跳过")


def test_gold_maker_vs_market_demo():
    cfg = AppConfig(connection_mode=ConnectionMode.DEMO.value, xau_trade_lots=1.0, xau_ba_qty_map=500.0)
    conn = BinanceConnector(cfg)
    conn._quotes["XAUUSDT"] = Quote(symbol="XAUUSDT", bid=2650.0, ask=2650.2, is_simulated=True)
    logs: list[str] = []
    conn._log = lambda _level, msg: logs.append(msg)

    assert conn.open_hedge_leg("xau", order_mode=GoldOrderMode.MAKER.value).success
    assert any("Maker" in msg for msg in logs)

    logs.clear()
    assert conn.open_hedge_leg("xau", order_mode=GoldOrderMode.MARKET.value).success
    assert any("市价" in msg for msg in logs)
    print("  ✓ 黄金 Maker/市价 demo 下单模式")


def test_binance_live_maker_open_uses_inside_tick_and_fill_price():
    cfg = AppConfig(
        connection_mode=ConnectionMode.LIVE_BA.value,
        ba_api_key="k",
        ba_api_secret="s",
        xau_ba_qty_map=1.0,
    )
    conn = BinanceConnector(cfg)
    conn._quotes["XAUUSDT"] = Quote(
        symbol="XAUUSDT",
        bid=2650.0,
        ask=2650.2,
        is_simulated=False,
    )
    conn._order_books["XAUUSDT"] = OrderBook(
        bids=[OrderBookLevel(2650.03, 3.0)],
        asks=[OrderBookLevel(2650.23, 2.0)],
    )
    client = MagicMock()
    client.futures_create_order.return_value = {"orderId": 901}
    client.futures_get_order.return_value = {
        "status": "FILLED",
        "executedQty": "1",
        "avgPrice": "2650.015",
    }
    conn._client = client

    with patch.object(conn, "_run_ba_api", side_effect=lambda fn, **_kw: fn()):
        with patch.object(conn, "get_positions", return_value=[]):
            with patch.object(conn, "_position_from_cache", return_value=None):
                with patch.object(conn, "_apply_margin_type"):
                    with patch("app.connectors.binance_connector.get_binance_lot_step", return_value=0.001):
                        with patch("app.connectors.binance_connector.get_binance_price_tick", return_value=0.01):
                            with patch.object(conn, "_wait_for_limit_order_fills", return_value=(True, 1.0)):
                                result = conn.open_hedge_leg(
                                    "xau",
                                    HedgeMode.CONTRACTION.value,
                                    GoldOrderMode.MAKER.value,
                                )

                                assert result.success
                                sell_kwargs = client.futures_create_order.call_args.kwargs
                                assert sell_kwargs["side"] == "SELL"
                                assert sell_kwargs["price"] == "2650.04"
                                assert abs(result.filled_price - 2650.015) < 1e-9

                                client.futures_create_order.reset_mock()
                                client.futures_get_order.return_value = {
                                    "status": "FILLED",
                                    "executedQty": "1",
                                    "avgPrice": "2650.185",
                                }
                                result = conn.open_hedge_leg(
                                    "xau",
                                    HedgeMode.EXPANSION.value,
                                    GoldOrderMode.MAKER.value,
                                )

    assert result.success
    buy_kwargs = client.futures_create_order.call_args.kwargs
    assert buy_kwargs["side"] == "BUY"
    assert buy_kwargs["price"] == "2650.22"
    assert abs(result.filled_price - 2650.185) < 1e-9
    print("  ✓ BA Maker 开仓优先用订单簿一跳内侧价并返回成交均价")


def test_binance_blocks_second_limit_order_when_symbol_has_pending():
    cfg = AppConfig(
        connection_mode=ConnectionMode.LIVE_BA.value,
        ba_api_key="k",
        ba_api_secret="s",
        xau_ba_qty_map=1.0,
    )
    conn = BinanceConnector(cfg)
    conn._client = MagicMock()
    conn._open_orders_cache = [
        OpenOrder(
            platform="BA",
            symbol="XAUUSDT",
            order_id="old",
            side=Side.SELL,
            order_type="LIMIT",
            total_quantity=1.0,
            remaining_quantity=1.0,
        )
    ]

    open_result = conn.open_hedge_leg(
        "xau",
        HedgeMode.CONTRACTION.value,
        GoldOrderMode.MAKER.value,
    )
    close_result = conn.close_hedge_leg(
        "xau",
        GoldOrderMode.MAKER.value,
        HedgeMode.CONTRACTION.value,
    )

    assert not open_result.success
    assert not close_result.success
    assert "已有 BA 委托未完成" in open_result.message
    assert "已有 BA 委托未完成" in close_result.message
    conn._client.futures_create_order.assert_not_called()
    print("  ✓ 同品种已有 BA 委托时拦截新的限价/Maker 委托")


def test_binance_live_maker_close_uses_order_book_inside_tick():
    cfg = AppConfig(
        connection_mode=ConnectionMode.LIVE_BA.value,
        ba_api_key="k",
        ba_api_secret="s",
        xau_ba_qty_map=1.0,
    )
    conn = BinanceConnector(cfg)
    conn._quotes["XAUUSDT"] = Quote(
        symbol="XAUUSDT",
        bid=2650.0,
        ask=2650.2,
        is_simulated=False,
    )
    conn._order_books["XAUUSDT"] = OrderBook(
        bids=[OrderBookLevel(2650.03, 3.0)],
        asks=[OrderBookLevel(2650.23, 2.0)],
    )
    pos = Position(
        platform="BA",
        symbol="XAUUSDT",
        side=Side.SELL,
        quantity=1.0,
        entry_price=2651.0,
    )
    client = MagicMock()
    client.futures_create_order.return_value = {"orderId": 902}
    client.futures_get_order.return_value = {
        "status": "FILLED",
        "executedQty": "1",
        "avgPrice": "2650.225",
    }
    conn._client = client

    with patch.object(conn, "_run_ba_api", side_effect=lambda fn, **_kw: fn()):
        with patch.object(conn, "get_positions", return_value=[pos]):
            with patch("app.connectors.binance_connector.get_binance_lot_step", return_value=0.001):
                with patch("app.connectors.binance_connector.get_binance_price_tick", return_value=0.01):
                    with patch.object(conn, "_wait_for_limit_order_fills", return_value=(True, 1.0)):
                        result = conn.close_hedge_leg(
                            "xau",
                            GoldOrderMode.MAKER.value,
                            HedgeMode.CONTRACTION.value,
                        )

    assert result.success
    kwargs = client.futures_create_order.call_args.kwargs
    assert kwargs["side"] == "BUY"
    assert kwargs["reduceOnly"] is True
    assert kwargs["price"] == "2650.22"
    print("  ✓ BA Maker 平仓优先用订单簿一跳内侧价")


def test_binance_maker_price_handles_one_tick_spread():
    assert BinanceConnector._maker_price_from_book(
        2650.03,
        2650.04,
        Side.SELL,
        0.01,
    ) == 2650.04
    assert BinanceConnector._maker_price_from_book(
        2650.03,
        2650.04,
        Side.BUY,
        0.01,
    ) == 2650.03
    print("  ✓ BA Maker 窄价差时仍贴近盘口且不穿对手价")


def test_binance_price_format_tolerates_float_tick_noise():
    assert format_binance_price(1.2345 + 0.0001, 0.0001) == "1.2346"
    print("  ✓ BA 价格按 tick 格式化时容忍浮点误差")


def test_ws_stream_dispatches_depth_vs_book_ticker():
    """组合流消息按 b/a 类型分发：字符串→bookTicker，数组→depth20。"""
    import json as _json

    from app.connectors.binance_ws_stream import BinanceWsStream

    quotes: list[tuple] = []
    depths: list[tuple] = []
    stream = BinanceWsStream(
        ["XAUUSDT"],
        use_proxy=False,
        proxy_host="",
        proxy_port=0,
        on_quote=lambda s, b, a: quotes.append((s, b, a)),
        on_state=lambda _s: None,
        on_depth=lambda s, b, a: depths.append((s, b, a)),
        depth_ms=500,
    )

    # bookTicker：b/a 为字符串最优价
    stream._handle_message(
        _json.dumps({"stream": "xauusdt@bookTicker",
                     "data": {"s": "XAUUSDT", "b": "2650.0", "a": "2650.2"}})
    )
    assert quotes == [("XAUUSDT", 2650.0, 2650.2)]
    assert not depths

    # depth20：b/a 为 [[价,量],...] 数组
    stream._handle_message(
        _json.dumps({"stream": "xauusdt@depth20@500ms", "data": {
            "s": "XAUUSDT",
            "b": [["2650.0", "3"], ["2649.5", "5"]],
            "a": [["2650.2", "2"], ["2650.7", "4"]],
        }})
    )
    assert len(depths) == 1
    sym, bids, asks = depths[0]
    assert sym == "XAUUSDT"
    assert bids[0] == (2650.0, 3.0)
    assert asks[1] == (2650.7, 4.0)
    assert stream.depth_is_live() is False  # 未连接，仅收到消息不算 live
    print("  ✓ WS 行情流分发 bookTicker/depth20")


def test_ws_depth_url_includes_depth_stream():
    """订阅 URL 同时含 bookTicker 与 depth20。"""
    from app.connectors.binance_ws_stream import BinanceWsStream

    stream = BinanceWsStream(
        ["XAUUSDT", "XAGUSDT"],
        use_proxy=False,
        proxy_host="",
        proxy_port=0,
        on_quote=lambda *_: None,
        on_state=lambda _s: None,
        on_depth=lambda *_: None,
        depth_ms=500,
    )
    url = stream._build_url()
    assert "xauusdt@bookTicker" in url
    assert "xauusdt@depth20@500ms" in url
    assert "xagusdt@depth20@500ms" in url
    print("  ✓ WS 订阅 URL 含 depth20")


def test_connector_on_ws_depth_replaces_order_book():
    """连接器 _on_ws_depth 用快照替换多档订单簿。"""
    cfg = AppConfig(
        connection_mode=ConnectionMode.LIVE_BA.value,
        ba_api_key="key",
        ba_api_secret="secret",
    )
    conn = BinanceConnector(cfg)
    conn._on_ws_depth(
        "XAUUSDT",
        [(2650.0, 3.0), (2649.5, 5.0)],
        [(2650.2, 2.0), (2650.7, 4.0)],
    )
    book = conn.order_book("XAUUSDT")
    assert book.bids[0].price == 2650.0
    assert book.bids[0].quantity == 3.0
    assert book.asks[1].price == 2650.7
    assert book.is_simulated is False
    print("  ✓ _on_ws_depth 替换订单簿")


if __name__ == "__main__":
    print("Connector tests:")
    test_binance_uses_futures_ping()
    test_binance_open_close_demo()
    test_mt5_thread_safe_get_positions()
    test_mt5_light_positions_skip_account_risk_calc()
    test_mt5_live_positions_merge_tickets_and_sum_profit()
    test_mt5_open_close_demo()
    test_hedge_open_both_demo()
    test_hedge_expansion_demo()
    test_binance_get_positions_uses_lock()
    test_cancel_all_open_orders_clears_ui_before_rest_cancel_returns()
    test_cancel_all_open_orders_noop_without_pending()
    test_rest_open_orders_snapshot_clears_stale_stream_order()
    test_cancel_all_open_orders_skips_when_not_live()
    test_gold_maker_vs_market_demo()
    test_binance_live_maker_open_uses_inside_tick_and_fill_price()
    test_binance_blocks_second_limit_order_when_symbol_has_pending()
    test_binance_live_maker_close_uses_order_book_inside_tick()
    test_binance_maker_price_handles_one_tick_spread()
    test_binance_price_format_tolerates_float_tick_noise()
    test_ws_stream_dispatches_depth_vs_book_ticker()
    test_ws_depth_url_includes_depth_stream()
    test_connector_on_ws_depth_replaces_order_book()
    print("ALL CONNECTOR TESTS PASSED")
