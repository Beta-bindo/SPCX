"""User Data Stream（账户私有推送）相关单元测试。

覆盖：
- _stream_wait_fills 通过推送的 ORDER_TRADE_UPDATE 即时感知成交（无需 REST）
- 部分成交按新增量驱动 on_fill_delta 回调（用于 Exness 分批补腿）
- 推送早于注册仍能被拾取；撤单/拒单返回未成交
- 推送在线/离线时的下单等待分发（推送优先，REST 兜底）
- 委托指示灯与持仓缓存随推送更新
- listenKey 申请 / 续期 / 释放生命周期
- BinanceUserStream 消息分发与 listenKey 失效信号
"""

import json
import sys
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.models import AppConfig, ConnectionMode
from app.connectors.binance_connector import BinanceConnector
from app.connectors.binance_user_stream import BinanceUserStream


def _live_ba_conn() -> BinanceConnector:
    cfg = AppConfig(
        connection_mode=ConnectionMode.LIVE_BA.value,
        ba_api_key="key",
        ba_api_secret="secret",
    )
    return BinanceConnector(cfg)


def _order_event(symbol: str, oid: int, status: str, z: float, ap: float = 0.0) -> dict:
    """构造一条 ORDER_TRADE_UPDATE 事件（z=累计成交量，ap=均价）。"""
    return {
        "e": "ORDER_TRADE_UPDATE",
        "o": {"s": symbol, "i": oid, "X": status, "z": str(z), "ap": str(ap)},
    }


def _activate_stream(conn: BinanceConnector) -> MagicMock:
    """让连接器认为 User Data Stream 已在线。"""
    stream = MagicMock()
    stream.is_active.return_value = True
    conn._user_stream = stream
    return stream


def test_stream_wait_fills_push_full_no_rest():
    """整单成交推送到达即返回，且不触发 REST 查询。"""
    conn = _live_ba_conn()
    client = MagicMock()
    conn._client = client
    _activate_stream(conn)

    def _push():
        time.sleep(0.05)
        conn._on_user_order_update(_order_event("XAUUSDT", 111, "FILLED", 500.0))

    threading.Thread(target=_push, daemon=True).start()
    ok, executed = conn._wait_for_limit_order_fills(
        "XAUUSDT", "111", target_qty=500.0
    )
    assert ok is True
    assert executed == 500.0
    client.futures_get_order.assert_not_called()
    print("  ✓ 整单成交推送即时返回（无 REST）")


def test_stream_wait_fills_partial_drives_deltas():
    """部分成交按新增量回调，累计驱动量等于成交总量。"""
    conn = _live_ba_conn()
    conn._client = MagicMock()
    _activate_stream(conn)

    deltas: list[float] = []

    def _on_delta(d: float) -> bool:
        deltas.append(d)
        return True

    def _push():
        time.sleep(0.05)
        conn._on_user_order_update(_order_event("XAUUSDT", 222, "PARTIALLY_FILLED", 200.0))
        time.sleep(0.08)
        conn._on_user_order_update(_order_event("XAUUSDT", 222, "FILLED", 500.0))

    threading.Thread(target=_push, daemon=True).start()
    ok, executed = conn._wait_for_limit_order_fills(
        "XAUUSDT", "222", target_qty=500.0, on_fill_delta=_on_delta
    )
    assert ok is True
    assert executed == 500.0
    assert deltas, "应至少回调一次新增成交"
    assert abs(sum(deltas) - 500.0) < 1e-9
    assert all(d > 0 for d in deltas)
    print("  ✓ 部分成交按新增量驱动补腿")


def test_stream_event_before_registration():
    """推送早于等待注册时，等待开始即能拾取既有成交状态。"""
    conn = _live_ba_conn()
    conn._client = MagicMock()
    _activate_stream(conn)

    conn._on_user_order_update(_order_event("XAUUSDT", 333, "FILLED", 500.0))
    ok, executed = conn._wait_for_limit_order_fills(
        "XAUUSDT", "333", target_qty=500.0
    )
    assert ok is True
    assert executed == 500.0
    print("  ✓ 推送早于注册仍被拾取")


def test_stream_wait_fills_canceled():
    """撤单（未成交）返回未达成且成交量为 0。"""
    conn = _live_ba_conn()
    conn._client = MagicMock()
    _activate_stream(conn)

    def _push():
        time.sleep(0.05)
        conn._on_user_order_update(_order_event("XAUUSDT", 444, "CANCELED", 0.0))

    threading.Thread(target=_push, daemon=True).start()
    ok, executed = conn._wait_for_limit_order_fills(
        "XAUUSDT", "444", target_qty=500.0
    )
    assert ok is False
    assert executed == 0.0
    print("  ✓ 撤单返回未成交")


def test_wait_for_limit_order_dispatch_stream():
    """平仓用的 _wait_for_limit_order 在推送在线时走推送路径。"""
    conn = _live_ba_conn()
    client = MagicMock()
    conn._client = client
    _activate_stream(conn)

    def _push():
        time.sleep(0.05)
        conn._on_user_order_update(_order_event("XAUUSDT", 555, "FILLED", 300.0))

    threading.Thread(target=_push, daemon=True).start()
    assert conn._wait_for_limit_order("XAUUSDT", "555", min_executed=300.0) is True
    client.futures_get_order.assert_not_called()
    print("  ✓ 平仓等待走推送")


def test_wait_fills_falls_back_to_rest_when_no_stream():
    """推送不可用时回退 REST 轮询。"""
    conn = _live_ba_conn()
    client = MagicMock()
    client.futures_get_order.return_value = {"status": "FILLED", "executedQty": "500"}
    conn._client = client
    conn._user_stream = None  # 推送不在线

    ok, executed = conn._wait_for_limit_order_fills(
        "XAUUSDT", "666", target_qty=500.0
    )
    assert ok is True
    assert executed == 500.0
    assert client.futures_get_order.called
    print("  ✓ 无推送回退 REST")


def test_order_update_drives_open_orders_indicator():
    """委托新建/成交通过推送驱动委托指示灯集合。"""
    conn = _live_ba_conn()
    conn._client = MagicMock()

    conn._on_user_order_update(_order_event("XAUUSDT", 777, "NEW", 0.0))
    assert "XAUUSDT" in conn._open_order_symbols

    conn._on_user_order_update(_order_event("XAUUSDT", 777, "FILLED", 500.0))
    assert "XAUUSDT" not in conn._open_order_symbols
    print("  ✓ 推送维护委托指示灯")


def test_account_update_invalidates_cache():
    """ACCOUNT_UPDATE 推送失效持仓缓存并置脏。"""
    conn = _live_ba_conn()
    conn._positions_cache_at = time.time()

    conn._on_user_account_update({"e": "ACCOUNT_UPDATE", "a": {}})
    assert conn._positions_cache_at == 0.0
    assert conn._account_dirty.is_set()
    print("  ✓ 持仓推送失效缓存")


def test_listen_key_lifecycle():
    """listenKey 申请 / 续期 / 释放调用对应 SDK 接口。"""
    conn = _live_ba_conn()
    client = MagicMock()
    client.futures_stream_get_listen_key.return_value = "abc123"
    conn._client = client

    key = conn._create_listen_key()
    assert key == "abc123"
    assert conn._listen_key == "abc123"

    conn._keepalive_listen_key()
    client.futures_stream_keepalive.assert_called_once()

    conn._close_listen_key()
    client.futures_stream_close.assert_called_once()
    assert conn._listen_key is None
    print("  ✓ listenKey 生命周期")


def test_seed_stream_open_orders_from_rest():
    """启动/重连后用 REST 现存挂单为指示灯打底。"""
    from app.core.models import OpenOrder, Side

    conn = _live_ba_conn()
    conn._client = MagicMock()
    conn.get_open_orders = lambda: [  # type: ignore[method-assign]
        OpenOrder(platform="BA", symbol="XAUUSDT", order_id="900", side=Side.SELL),
        OpenOrder(platform="BA", symbol="XAGUSDT", order_id="901", side=Side.BUY),
    ]

    conn._seed_stream_open_orders({"XAUUSDT", "XAGUSDT"})
    assert conn._open_order_symbols == frozenset({"XAUUSDT", "XAGUSDT"})

    # 打底后由推送增量维护：其中一腿成交后该交易对应从指示灯移除
    conn._on_user_order_update(_order_event("XAUUSDT", 900, "FILLED", 500.0))
    assert "XAUUSDT" not in conn._open_order_symbols
    assert "XAGUSDT" in conn._open_order_symbols
    print("  ✓ REST 打底 + 推送增量维护指示灯")


def test_close_listen_key_with_explicit_key():
    """后台释放指定 listenKey 不依赖（也不清空）当前 key 字段。"""
    conn = _live_ba_conn()
    client = MagicMock()
    conn._client = client
    conn._listen_key = "current"

    conn._close_listen_key("stale-key")
    client.futures_stream_close.assert_called_once()
    # 传入显式 key 时不应改动当前 key
    assert conn._listen_key == "current"
    print("  ✓ 按 key 释放 listenKey")


def test_user_stream_message_dispatch():
    """BinanceUserStream 消息按事件类型分发；listenKey 失效返回需重连。"""
    orders: list[dict] = []
    accounts: list[dict] = []
    stream = BinanceUserStream(
        use_proxy=False,
        proxy_host="",
        proxy_port=0,
        get_listen_key=lambda: "k",
        on_order_update=orders.append,
        on_account_update=accounts.append,
    )

    assert stream._handle_message(
        json.dumps({"e": "ORDER_TRADE_UPDATE", "o": {}})
    ) is True
    assert len(orders) == 1

    assert stream._handle_message(
        json.dumps({"e": "ACCOUNT_UPDATE", "a": {}})
    ) is True
    assert len(accounts) == 1

    assert stream._handle_message(json.dumps({"e": "listenKeyExpired"})) is False
    assert stream._handle_message("not-json") is True  # 坏消息忽略，不中断
    print("  ✓ 账户流消息分发")


if __name__ == "__main__":
    print("User stream tests:")
    test_stream_wait_fills_push_full_no_rest()
    test_stream_wait_fills_partial_drives_deltas()
    test_stream_event_before_registration()
    test_stream_wait_fills_canceled()
    test_wait_for_limit_order_dispatch_stream()
    test_wait_fills_falls_back_to_rest_when_no_stream()
    test_order_update_drives_open_orders_indicator()
    test_account_update_invalidates_cache()
    test_listen_key_lifecycle()
    test_seed_stream_open_orders_from_rest()
    test_close_listen_key_with_explicit_key()
    test_user_stream_message_dispatch()
    print("ALL USER STREAM TESTS PASSED")
