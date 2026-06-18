"""Tests for trade reporting fields and offline resilience."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.core.license.client import LicenseClient, LicenseError
from app.core.license.pending_trades import clear_pending, enqueue_trades, load_pending
from app.core.license.service import LicenseService
from app.core.license.store import LicenseState, load_license, save_license
from app.core.models import AppConfig, ConnectionMode
from app.core.hedge_trade_report import (
    HedgeTradeRow,
    build_row_from_settlement,
    fetch_hedge_trade_report,
)
from app.core.trade_records import append_trade_record, load_trade_records
from app.core.pnl_calculator import estimate_trade_fees
from app.core.spread_engine import SpreadEngine
from app.core.trade_result import HedgeTradeResult, LegResult

TEST_DEVICE_ID = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"


class TradeReportingTests(unittest.TestCase):
    def test_hedge_row_payload_includes_order_fields(self):
        row = build_row_from_settlement(
            preset_id="xau",
            mode="contraction",
            action="open",
            ba_order_no="7001",
            ex_order_no="8001",
            ba_qty=500.0,
            ex_qty=1.0,
            ba_open_price=3.125,
            ba_commission=0.25,
            order_time="2026-06-10 12:00:00",
        )
        payload = row.to_payload()
        self.assertEqual(payload["ba_order_no"], "7001")
        self.assertEqual(payload["ex_order_no"], "8001")
        self.assertEqual(payload["product"], "黄金")
        self.assertEqual(payload["direction"], "收缩")
        self.assertEqual(payload["ba_open_price"], "3.1250")
        self.assertEqual(payload["ba_commission"], "-0.2500")
        self.assertTrue(payload["record_key"])

    def test_open_trade_payload_includes_ba_commission(self):
        config = AppConfig()
        ba_fee, _mt5_fee = estimate_trade_fees(
            "xau",
            config,
            ba_price=2650.5,
            ba_quantity=500.0,
            mt5_quantity=1.0,
        )
        self.assertGreater(ba_fee, 0.0)
        row = build_row_from_settlement(
            preset_id="xau",
            mode="contraction",
            action="open",
            ba_commission=ba_fee,
            order_time="2026-06-10 12:00:00",
        )
        payload = row.to_payload()
        self.assertEqual(payload["ba_commission"], f"{-abs(ba_fee):+.4f}")

    def test_official_report_fill_prices_match_order_and_deal_history(self):
        class _BA:
            def fetch_account_trade_history(self, symbols, start_ms, end_ms):
                return [
                    {
                        "symbol": "XAUUSDT",
                        "orderId": "7112786180",
                        "id": "1",
                        "side": "BUY",
                        "price": "4257.00",
                        "qty": "0.4",
                        "quoteQty": "1702.8",
                        "realizedPnl": "0",
                        "commission": "0.6",
                        "time": 1781741128000,
                    },
                    {
                        "symbol": "XAUUSDT",
                        "orderId": "7112786180",
                        "id": "2",
                        "side": "BUY",
                        "price": "4257.1666667",
                        "qty": "0.6",
                        "quoteQty": "2554.3",
                        "realizedPnl": "0",
                        "commission": "1.10284",
                        "time": 1781741129000,
                    },
                    {
                        "symbol": "XAUUSDT",
                        "orderId": "7113000000",
                        "id": "3",
                        "side": "SELL",
                        "price": "4260.50",
                        "qty": "1",
                        "quoteQty": "4260.5",
                        "realizedPnl": "7.01499998",
                        "commission": "1.7042",
                        "time": 1781741228000,
                    },
                ]

            def fetch_order_history_rows(self, symbols, start_ms, end_ms):
                return [
                    {
                        "symbol": "XAUUSDT",
                        "orderId": "7112786180",
                        "avgPrice": "4257.10",
                        "reduceOnly": "false",
                        "side": "BUY",
                    },
                    {
                        "symbol": "XAUUSDT",
                        "orderId": "7113000000",
                        "avgPrice": "4260.50",
                        "reduceOnly": "true",
                        "side": "SELL",
                    },
                ]

            def fetch_income_history_rows(self, symbols, start_ms, end_ms):
                return []

        class _MT5:
            def fetch_history_deals(self, symbols, start, end):
                return [
                    {
                        "symbol": "XAUUSD",
                        "order": "21212149",
                        "ticket": "9001",
                        "entry": "0",
                        "price": "4255.20",
                        "volume": "0.01",
                        "profit": "0",
                        "commission": "0",
                        "fee": "0",
                        "swap": "0",
                        "time_msc": 1781741128500,
                    },
                    {
                        "symbol": "XAUUSD",
                        "order": "21213000",
                        "ticket": "9002",
                        "entry": "1",
                        "price": "4258.30",
                        "volume": "0.01",
                        "profit": "-2",
                        "commission": "-0.1",
                        "fee": "0",
                        "swap": "0",
                        "time_msc": 1781741228500,
                    },
                ]

        cfg = AppConfig(connection_mode=ConnectionMode.LIVE_BOTH.value)
        report = fetch_hedge_trade_report(
            _BA(),
            _MT5(),
            cfg,
            date(2026, 6, 18),
            date(2026, 6, 18),
            "xau",
        )

        open_row = next(row for row in report.rows if row.ba_order_no == "7112786180")
        close_row = next(row for row in report.rows if row.ba_order_no == "7113000000")
        self.assertEqual(open_row.ba_open_price, "4257.1000")
        self.assertEqual(open_row.ex_open_price, "4255.2000")
        self.assertEqual(open_row.ba_close_price, "--")
        self.assertEqual(open_row.ex_close_price, "--")
        self.assertEqual(open_row.direction, "扩张")
        self.assertEqual(close_row.ba_close_price, "4260.5000")
        self.assertEqual(close_row.ex_close_price, "4258.3000")
        self.assertEqual(close_row.direction, "扩张")

    def test_official_report_prefers_saved_order_id_anchor(self):
        class _BA:
            def fetch_account_trade_history(self, symbols, start_ms, end_ms):
                return [
                    {
                        "symbol": "XAUUSDT",
                        "orderId": "BA1",
                        "side": "SELL",
                        "price": "4266.00",
                        "qty": "1",
                        "quoteQty": "4266",
                        "realizedPnl": "0",
                        "commission": "1",
                        "time": 1781741128000,
                    }
                ]

            def fetch_order_history_rows(self, symbols, start_ms, end_ms):
                return [
                    {
                        "symbol": "XAUUSDT",
                        "orderId": "BA1",
                        "avgPrice": "4266.00",
                        "reduceOnly": "false",
                        "side": "SELL",
                    }
                ]

            def fetch_income_history_rows(self, symbols, start_ms, end_ms):
                return []

        class _MT5:
            def fetch_history_deals(self, symbols, start, end):
                return [
                    {
                        "symbol": "XAUUSD",
                        "order": "EX_WRONG_NEAR_TIME",
                        "entry": "0",
                        "price": "9999.00",
                        "volume": "0.01",
                        "profit": "0",
                        "commission": "0",
                        "fee": "0",
                        "swap": "0",
                        "time_msc": 1781741128500,
                    },
                    {
                        "symbol": "XAUUSD",
                        "order": "EX_TARGET",
                        "entry": "0",
                        "price": "4264.20",
                        "volume": "0.01",
                        "profit": "0",
                        "commission": "0",
                        "fee": "0",
                        "swap": "0",
                        "time_msc": 1781741190000,
                    },
                ]

        anchors = [
            {
                "preset_id": "xau",
                "mode": "contraction",
                "action": "open",
                "ba_order_no": "BA1",
                "ex_order_no": "EX_TARGET",
                "product": "黄金",
                "direction": "收缩",
                "ba_qty": "1",
                "ex_qty": "0.01",
                "order_time": "2026-06-18 00:05:28",
                "record_key": "BA1|EX_TARGET|2026-06-18 00:05:28",
            }
        ]

        cfg = AppConfig(connection_mode=ConnectionMode.LIVE_BOTH.value)
        report = fetch_hedge_trade_report(
            _BA(),
            _MT5(),
            cfg,
            date(2026, 6, 18),
            date(2026, 6, 18),
            "xau",
            anchors=anchors,
        )

        row = next(row for row in report.rows if row.ba_order_no == "BA1")
        self.assertEqual(row.ex_order_no, "EX_TARGET")
        self.assertEqual(row.ba_open_price, "4266.0000")
        self.assertEqual(row.ex_open_price, "4264.2000")
        self.assertNotEqual(row.ex_open_price, "9999.0000")

    def test_official_report_pairs_nearby_ex_when_anchor_lacks_ex_order(self):
        class _BA:
            def fetch_account_trade_history(self, symbols, start_ms, end_ms):
                return [
                    {
                        "symbol": "XAUUSDT",
                        "orderId": "7132553716",
                        "side": "BUY",
                        "price": "4321.12",
                        "qty": "1",
                        "quoteQty": "4321.12",
                        "realizedPnl": "0",
                        "commission": "0",
                        "time": 1781741128000,
                    }
                ]

            def fetch_order_history_rows(self, symbols, start_ms, end_ms):
                return []

            def fetch_income_history_rows(self, symbols, start_ms, end_ms):
                return []

        class _MT5:
            def fetch_history_deals(self, symbols, start, end):
                return [
                    {
                        "symbol": "XAUUSD",
                        "order": "21237373",
                        "entry": "0",
                        "price": "4319.791",
                        "volume": "0.01",
                        "profit": "0",
                        "commission": "0",
                        "fee": "0",
                        "swap": "0",
                        "time_msc": 1781741128500,
                    }
                ]

        anchors = [
            {
                "preset_id": "xau",
                "mode": "expansion",
                "action": "open",
                "ba_order_no": "7132553716",
                "ex_order_no": "--",
                "product": "黄金",
                "order_time": "2026-06-18 00:05:28",
                "record_key": "7132553716|--|2026-06-18 00:05:28",
            }
        ]

        cfg = AppConfig(connection_mode=ConnectionMode.LIVE_BOTH.value)
        report = fetch_hedge_trade_report(
            _BA(),
            _MT5(),
            cfg,
            date(2026, 6, 18),
            date(2026, 6, 18),
            "xau",
            anchors=anchors,
        )

        self.assertEqual(len(report.rows), 1)
        row = report.rows[0]
        self.assertEqual(row.ba_order_no, "7132553716")
        self.assertEqual(row.ex_order_no, "21237373")
        self.assertEqual(row.ex_open_price, "4319.7910")

    def test_trade_records_persist_order_id_anchors(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "trade_records.json"
            with patch("app.core.trade_records._path", lambda: path):
                row = build_row_from_settlement(
                    preset_id="xau",
                    mode="contraction",
                    action="open",
                    ba_order_no="7001",
                    ex_order_no="8001",
                    order_time="2026-06-18 12:00:00",
                )
                append_trade_record(row, preset_id="xau", mode="contraction", action="open")
                saved = load_trade_records(date(2026, 6, 18), date(2026, 6, 18), "xau")
                self.assertEqual(len(saved), 1)
                self.assertEqual(saved[0]["ba_order_no"], "7001")
                self.assertEqual(saved[0]["ex_order_no"], "8001")
                self.assertEqual(saved[0]["action"], "open")

    def test_actual_trade_prices_prefer_filled_prices(self):
        result = HedgeTradeResult(
            action="open",
            success=True,
            legs=[
                LegResult("BA", True, filled_quantity=1.0, filled_price=2650.01),
                LegResult("MT5", True, filled_quantity=0.01, filled_price=2648.91),
            ],
        )

        spread, ba_price, ex_price = SpreadEngine._actual_trade_prices(
            result,
            ba_fallback=999.0,
            ex_fallback=998.0,
        )

        self.assertAlmostEqual(ba_price, 2650.01)
        self.assertAlmostEqual(ex_price, 2648.91)
        self.assertAlmostEqual(spread, 1.10)

    def test_leg_fee_prefers_known_exchange_fee(self):
        self.assertEqual(
            SpreadEngine._leg_fee_or_estimate(
                LegResult("BA", True, fee=0.0, fee_known=True),
                1.23,
            ),
            0.0,
        )
        self.assertEqual(
            SpreadEngine._leg_fee_or_estimate(
                LegResult("BA", True, fee=0.42, fee_known=True),
                1.23,
            ),
            0.42,
        )
        self.assertEqual(
            SpreadEngine._leg_fee_or_estimate(
                LegResult("BA", True),
                1.23,
            ),
            1.23,
        )

    def test_leg_pnl_prefers_known_exchange_realized_pnl(self):
        self.assertEqual(
            SpreadEngine._leg_pnl_or_estimate(
                LegResult("BA", True, realized_pnl=-2.35, pnl_known=True),
                9.99,
            ),
            -2.35,
        )
        self.assertEqual(
            SpreadEngine._leg_pnl_or_estimate(
                LegResult("MT5", True),
                9.99,
            ),
            9.99,
        )

    def test_close_payload_includes_ba_charges_and_net(self):
        row = build_row_from_settlement(
            preset_id="xau",
            mode="contraction",
            action="close",
            ba_pnl=10.0,
            ex_pnl=-5.0,
            ba_charges=-2.5,
            ba_commission=1.0,
            order_time="2026-06-10 18:00:00",
        )
        payload = row.to_payload()
        self.assertEqual(payload["ba_charges"], "-2.5000")
        self.assertEqual(payload["net_profit"], "+2.5000")

    def test_demo_mode_skips_platform_account_check(self):
        from app.core.license.client import LicenseClient
        from app.core.license.store import LicenseState, save_license
        from app.core.models import ConnectionMode

        with tempfile.TemporaryDirectory() as tmp:
            license_file = Path(tmp) / "license.json"
            with patch("app.core.license.store.license_path", lambda: license_file):
                save_license(
                    LicenseState(
                        device_id=TEST_DEVICE_ID,
                        status="approved",
                        access_token="token",
                        ba_account_status="pending",
                        ex_account_status="pending",
                    )
                )
                with patch("app.core.license.client.get_device_id", return_value=TEST_DEVICE_ID):
                    client = LicenseClient()
                client.require_platform_accounts_enabled(ConnectionMode.DEMO.value)

    def test_live_both_requires_ba_and_ex_enabled(self):
        from app.core.license.client import LicenseClient, LicenseError
        from app.core.license.store import LicenseState, save_license
        from app.core.models import ConnectionMode

        with tempfile.TemporaryDirectory() as tmp:
            license_file = Path(tmp) / "license.json"
            with patch("app.core.license.store.license_path", lambda: license_file):
                save_license(
                    LicenseState(
                        device_id=TEST_DEVICE_ID,
                        status="approved",
                        access_token="token",
                        ba_account_status="enabled",
                        ex_account_status="pending",
                    )
                )
                with patch("app.core.license.client.get_device_id", return_value=TEST_DEVICE_ID):
                    client = LicenseClient()
                with self.assertRaises(LicenseError) as ctx:
                    client.require_platform_accounts_enabled(ConnectionMode.LIVE_BOTH.value)
                self.assertIn("EX", str(ctx.exception))

    def test_live_ba_only_checks_ba_account(self):
        from app.core.license.client import LicenseClient, LicenseError
        from app.core.license.store import LicenseState, save_license
        from app.core.models import ConnectionMode

        with tempfile.TemporaryDirectory() as tmp:
            license_file = Path(tmp) / "license.json"
            with patch("app.core.license.store.license_path", lambda: license_file):
                save_license(
                    LicenseState(
                        device_id=TEST_DEVICE_ID,
                        status="approved",
                        access_token="token",
                        ba_account_status="pending",
                        ex_account_status="pending",
                    )
                )
                with patch("app.core.license.client.get_device_id", return_value=TEST_DEVICE_ID):
                    client = LicenseClient()
                with self.assertRaises(LicenseError) as ctx:
                    client.require_platform_accounts_enabled(ConnectionMode.LIVE_BA.value)
                self.assertIn("BA", str(ctx.exception))

    def test_live_mt5_only_checks_ex_account(self):
        from app.core.license.client import LicenseClient
        from app.core.license.store import LicenseState, save_license
        from app.core.models import ConnectionMode

        with tempfile.TemporaryDirectory() as tmp:
            license_file = Path(tmp) / "license.json"
            with patch("app.core.license.store.license_path", lambda: license_file):
                save_license(
                    LicenseState(
                        device_id=TEST_DEVICE_ID,
                        status="approved",
                        access_token="token",
                        ba_account_status="pending",
                        ex_account_status="enabled",
                    )
                )
                with patch("app.core.license.client.get_device_id", return_value=TEST_DEVICE_ID):
                    client = LicenseClient()
                client.require_platform_accounts_enabled(ConnectionMode.LIVE_MT5.value)

    def test_disabled_ba_account_blocks_live_ba_trade(self):
        from app.core.license.client import LicenseClient, LicenseError
        from app.core.license.store import LicenseState, save_license
        from app.core.models import ConnectionMode

        with tempfile.TemporaryDirectory() as tmp:
            license_file = Path(tmp) / "license.json"
            with patch("app.core.license.store.license_path", lambda: license_file):
                save_license(
                    LicenseState(
                        device_id=TEST_DEVICE_ID,
                        status="approved",
                        access_token="token",
                        ba_account_status="disabled",
                        ex_account_status="enabled",
                    )
                )
                with patch("app.core.license.client.get_device_id", return_value=TEST_DEVICE_ID):
                    client = LicenseClient()
                with self.assertRaises(LicenseError) as ctx:
                    client.require_platform_accounts_enabled(ConnectionMode.LIVE_BA.value)
                self.assertIn("BA", str(ctx.exception))

    def test_ensure_approved_for_trade_blocks_disabled_account_when_license_required(self):
        from app.core.license.service import LicenseService
        from app.core.license.store import LicenseState, save_license
        from app.core.models import ConnectionMode

        with tempfile.TemporaryDirectory() as tmp:
            license_file = Path(tmp) / "license.json"
            with patch("app.core.license.store.license_path", lambda: license_file):
                save_license(
                    LicenseState(
                        device_id=TEST_DEVICE_ID,
                        status="approved",
                        access_token="token",
                        ba_account_status="disabled",
                        ex_account_status="enabled",
                    )
                )
                with patch("app.core.license.client.get_device_id", return_value=TEST_DEVICE_ID):
                    service = LicenseService()
                with patch("app.core.build_config.LICENSE_REQUIRED", True):
                    with patch.object(LicenseService, "refresh"):
                        with self.assertRaises(LicenseError):
                            service.ensure_approved_for_trade(ConnectionMode.LIVE_BA.value)

    def test_ensure_approved_for_trade_skips_check_when_nolicense(self):
        from app.core.license.service import LicenseService
        from app.core.license.store import LicenseState, save_license

        with tempfile.TemporaryDirectory() as tmp:
            license_file = Path(tmp) / "license.json"
            with patch("app.core.license.store.license_path", lambda: license_file):
                save_license(
                    LicenseState(
                        device_id="dev-1",
                        status="pending",
                        access_token="",
                        ba_account_status="disabled",
                        ex_account_status="disabled",
                    )
                )
                service = LicenseService()
                with patch("app.core.build_config.LICENSE_REQUIRED", False):
                    service.ensure_approved_for_trade()

    def test_record_trade_anchor_persists_timestamp(self):
        with tempfile.TemporaryDirectory() as tmp:
            anchor_file = Path(tmp) / "trade_anchors.json"
            with patch("app.core.trade_anchor._path", lambda: anchor_file):
                from app.core.trade_anchor import funding_period_start, record_trade_anchor

                record_trade_anchor("xag", "expansion", "open", settled_at="2026-06-10 12:00:00")
                start = funding_period_start("xag", "expansion")
                self.assertIsNotNone(start)
                saved = json.loads(anchor_file.read_text(encoding="utf-8"))
                self.assertEqual(saved["xag:expansion"], "2026-06-10 12:00:00")

    def test_pending_queue_keeps_multiple_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "pending_trades.json"
            with patch("app.core.license.pending_trades._path", lambda: path):
                clear_pending()
                base = build_row_from_settlement(
                    preset_id="xau",
                    mode="contraction",
                    action="open",
                    ba_order_no="7001",
                    ex_order_no="8001",
                    order_time="2026-06-10 12:00:00",
                ).to_payload()
                close_row = build_row_from_settlement(
                    preset_id="xau",
                    mode="contraction",
                    action="close",
                    ba_order_no="7002",
                    ex_order_no="8002",
                    ba_pnl=1.0,
                    ex_pnl=-0.5,
                    order_time="2026-06-10 12:30:00",
                ).to_payload()
                enqueue_trades([base, close_row])
                self.assertEqual(len(load_pending()), 2)

    def test_flush_pending_replays_offline_trades(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            license_file = tmp_path / "license.json"
            pending_path = tmp_path / "pending_trades.json"
            with patch("app.core.license.store.license_path", lambda: license_file), patch(
                "app.core.license.pending_trades._path", lambda: pending_path
            ):
                save_license(
                    LicenseState(
                        device_id=TEST_DEVICE_ID,
                        status="approved",
                        access_token="token",
                        server_url="http://127.0.0.1:8787",
                    )
                )
                trade = build_row_from_settlement(
                    preset_id="xau",
                    mode="contraction",
                    action="open",
                    ba_open_price=3.0,
                    ba_order_no="7001",
                    ex_order_no="8001",
                    order_time="2026-06-10 12:00:00",
                ).to_payload()
                enqueue_trades([trade])
                with patch("app.core.license.client.get_device_id", return_value=TEST_DEVICE_ID):
                    client = LicenseClient()
                posted: list[dict] = []

                class _Resp:
                    def raise_for_status(self):
                        return None

                def _post(url, json=None, headers=None, timeout=None):
                    posted.append(json)
                    return _Resp()

                with patch.object(client._session, "post", side_effect=_post):
                    count = client.flush_pending_trades()
                self.assertEqual(count, 1)
                self.assertEqual(load_pending(), [])
                self.assertEqual(posted[0]["trades"][0]["ba_open_price"], "3.0000")

    def test_ensure_approved_allows_cached_token_when_server_down(self):
        with tempfile.TemporaryDirectory() as tmp:
            license_file = Path(tmp) / "license.json"
            with patch("app.core.license.store.license_path", lambda: license_file):
                save_license(
                    LicenseState(
                        device_id=TEST_DEVICE_ID,
                        status="approved",
                        access_token="token",
                        server_url="http://127.0.0.1:8787",
                    )
                )
                with patch("app.core.license.client.get_device_id", return_value=TEST_DEVICE_ID):
                    service = LicenseService()
                with patch("app.core.build_config.LICENSE_REQUIRED", True):
                    with patch.object(
                        service.client._session,
                        "post",
                        side_effect=requests.ConnectionError("offline"),
                    ):
                        service.ensure_approved()

    def test_ensure_approved_blocks_when_never_approved(self):
        with tempfile.TemporaryDirectory() as tmp:
            license_file = Path(tmp) / "license.json"
            with patch("app.core.license.store.license_path", lambda: license_file):
                save_license(
                    LicenseState(
                        device_id=TEST_DEVICE_ID,
                        status="pending",
                        access_token="",
                        server_url="http://127.0.0.1:8787",
                    )
                )
                with patch("app.core.license.client.get_device_id", return_value=TEST_DEVICE_ID):
                    service = LicenseService()
                with patch("app.core.build_config.LICENSE_REQUIRED", True):
                    with patch.object(
                        service.client._session,
                        "post",
                        side_effect=requests.ConnectionError("offline"),
                    ):
                        with self.assertRaises(LicenseError):
                            service.ensure_approved()


    def test_nolicense_client_keeps_pending_token_for_upload(self):
        with tempfile.TemporaryDirectory() as tmp:
            license_file = Path(tmp) / "license.json"
            with patch("app.core.license.store.license_path", lambda: license_file):
                save_license(
                    LicenseState(
                        device_id=TEST_DEVICE_ID,
                        status="pending",
                        access_token="pending-token",
                        server_url="http://127.0.0.1:8787",
                    )
                )
                with patch("app.core.license.client.get_device_id", return_value=TEST_DEVICE_ID):
                    client = LicenseClient()
                with patch("app.core.build_config.LICENSE_REQUIRED", False):
                    self.assertTrue(client.can_upload_trades)

    def test_nolicense_ensure_reporting_upgrades_pending(self):
        with tempfile.TemporaryDirectory() as tmp:
            license_file = Path(tmp) / "license.json"
            with patch("app.core.license.store.license_path", lambda: license_file):
                save_license(
                    LicenseState(
                        device_id=TEST_DEVICE_ID,
                        status="pending",
                        access_token="",
                        server_url="http://127.0.0.1:8787",
                    )
                )
                with patch("app.core.license.client.get_device_id", return_value=TEST_DEVICE_ID):
                    service = LicenseService()

                def _register(name, contact, note):
                    return service.client._save_check(
                        display_name=name,
                        contact=contact,
                        note=note,
                        status="approved",
                        access_token="approved-token",
                    )

                with patch.object(service.client, "register", side_effect=_register), patch.object(
                    service.client, "heartbeat", side_effect=LicenseError("offline")
                ), patch("app.core.build_config.LICENSE_REQUIRED", False):
                    service.ensure_reporting_ready()
                state = load_license()
                self.assertEqual(state.status, "approved")
                self.assertEqual(state.access_token, "approved-token")


if __name__ == "__main__":
    unittest.main()
