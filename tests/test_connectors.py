import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.models import AppConfig, ConnectionMode, GoldOrderMode, Quote, Side
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


def test_cancel_all_open_orders_cancels_pending():
    cfg = AppConfig(connection_mode=ConnectionMode.LIVE_BA.value, ba_api_key="k", ba_api_secret="s")
    conn = BinanceConnector(cfg)
    client = MagicMock()

    def _open_orders(symbol=None):
        if symbol == "XAUUSDT":
            return [{
                "symbol": "XAUUSDT", "orderId": 123, "side": "BUY",
                "type": "LIMIT", "origQty": "0.01", "executedQty": "0",
                "price": "2650.0",
            }]
        return []

    client.futures_get_open_orders.side_effect = _open_orders
    conn._client = client

    count = conn.cancel_all_open_orders()

    assert count == 1
    client.futures_cancel_all_open_orders.assert_called_once_with(symbol="XAUUSDT")
    print("  ✓ 手动撤单：撤销委托中的挂单")


def test_cancel_all_open_orders_noop_without_pending():
    cfg = AppConfig(connection_mode=ConnectionMode.LIVE_BA.value, ba_api_key="k", ba_api_secret="s")
    conn = BinanceConnector(cfg)
    client = MagicMock()
    client.futures_get_open_orders.return_value = []
    conn._client = client

    count = conn.cancel_all_open_orders()

    assert count == 0
    client.futures_cancel_all_open_orders.assert_not_called()
    print("  ✓ 手动撤单：无挂单时不调用撤单接口")


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


if __name__ == "__main__":
    print("Connector tests:")
    test_binance_uses_futures_ping()
    test_binance_open_close_demo()
    test_mt5_thread_safe_get_positions()
    test_mt5_open_close_demo()
    test_hedge_open_both_demo()
    test_hedge_expansion_demo()
    test_binance_get_positions_uses_lock()
    test_gold_maker_vs_market_demo()
    print("ALL CONNECTOR TESTS PASSED")
