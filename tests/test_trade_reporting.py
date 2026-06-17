"""Tests for trade reporting fields and offline resilience."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.core.license.client import LicenseClient, LicenseError
from app.core.license.pending_trades import clear_pending, enqueue_trades, load_pending
from app.core.license.service import LicenseService
from app.core.license.store import LicenseState, load_license, save_license
from app.core.models import AppConfig
from app.core.pnl_calculator import estimate_trade_fees
from app.core.spread_engine import SpreadEngine
from app.core.trade_ledger import TradeRecord, record_trade, trade_record_to_payload
from app.core.trade_result import HedgeTradeResult, LegResult

TEST_DEVICE_ID = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"


class TradeReportingTests(unittest.TestCase):
    def test_trade_record_payload_includes_order_fields(self):
        rec = TradeRecord(
            settled_at="2026-06-10T12:00:00",
            preset_id="xau",
            mode="contraction",
            action="open",
            spread=3.125,
            ba_price=2650.5,
            ex_price=2647.375,
            ba_quantity=500.0,
            mt5_quantity=1.0,
            ba_side="SELL",
            mt5_side="BUY",
        )
        payload = trade_record_to_payload(rec)
        self.assertEqual(payload["action"], "open")
        self.assertEqual(payload["spread"], 3.125)
        self.assertEqual(payload["ba_price"], 2650.5)
        self.assertEqual(payload["ex_price"], 2647.375)
        self.assertEqual(payload["ba_quantity"], 500.0)
        self.assertEqual(payload["mt5_quantity"], 1.0)
        self.assertEqual(payload["direction"], "BA SELL / Ex BUY")

    def test_open_trade_payload_includes_ba_fee(self):
        config = AppConfig()
        ba_fee, mt5_fee = estimate_trade_fees(
            "xau",
            config,
            ba_price=2650.5,
            ba_quantity=500.0,
            mt5_quantity=1.0,
        )
        self.assertGreater(ba_fee, 0.0)
        rec = TradeRecord(
            settled_at="2026-06-10T12:00:00",
            preset_id="xau",
            mode="contraction",
            action="open",
            spread=3.125,
            ba_price=2650.5,
            ex_price=2647.375,
            ba_quantity=500.0,
            mt5_quantity=1.0,
            ba_side="SELL",
            mt5_side="BUY",
            ba_fee=ba_fee,
            mt5_fee=mt5_fee,
        )
        payload = trade_record_to_payload(rec)
        self.assertEqual(payload["ba_fee"], ba_fee)
        self.assertGreater(payload["ba_fee"], 0.0)

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

    def test_close_payload_includes_ba_funding_fee(self):
        rec = TradeRecord(
            settled_at="2026-06-10T18:00:00",
            preset_id="xau",
            mode="contraction",
            action="close",
            ba_pnl=10.0,
            mt5_pnl=-5.0,
            ba_fee=1.0,
            mt5_fee=0.5,
            ba_funding_fee=-2.5,
        )
        payload = trade_record_to_payload(rec)
        self.assertEqual(payload["ba_funding_fee"], -2.5)
        self.assertEqual(payload["net_pnl"], 1.0)

    def test_close_payload_includes_ba_rebate(self):
        rec = TradeRecord(
            settled_at="2026-06-10T18:00:00",
            preset_id="xau",
            mode="contraction",
            action="close",
            ba_pnl=10.0,
            mt5_pnl=-5.0,
            ba_fee=1.0,
            mt5_fee=0.5,
            ba_rebate=0.8,
        )
        payload = trade_record_to_payload(rec)
        self.assertEqual(payload["ba_rebate"], 0.8)
        self.assertEqual(payload["net_pnl"], 4.3)

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

    def test_record_trade_persists_open_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger_file = Path(tmp) / "trade_ledger.json"
            with patch("app.core.trade_ledger.ledger_path", lambda: ledger_file):
                rec = record_trade(
                    "xag",
                    "expansion",
                    "open",
                    spread=-2.5,
                    ba_price=30.1,
                    ex_price=30.125,
                    ba_quantity=1000.0,
                    mt5_quantity=0.5,
                )
                self.assertEqual(rec.ba_side, "BUY")
                saved = json.loads(ledger_file.read_text(encoding="utf-8"))
                self.assertEqual(saved["records"][0]["spread"], -2.5)

    def test_pending_queue_keeps_open_and_close(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "pending_trades.json"
            with patch("app.core.license.pending_trades._path", lambda: path):
                clear_pending()
                base = {
                    "settled_at": "2026-06-10T12:00:00",
                    "preset_id": "xau",
                    "mode": "contraction",
                    "spread": 1.0,
                    "ba_price": 1.0,
                    "ex_price": 1.0,
                    "ba_quantity": 500.0,
                    "mt5_quantity": 1.0,
                    "ba_side": "SELL",
                    "mt5_side": "BUY",
                    "direction": "BA SELL / Ex BUY",
                    "ba_pnl": 0.0,
                    "mt5_pnl": 0.0,
                    "ba_fee": 0.0,
                    "mt5_fee": 0.0,
                    "ba_funding_fee": 0.0,
                    "ba_rebate": 0.0,
                    "net_pnl": 0.0,
                }
                enqueue_trades([{**base, "action": "open"}, {**base, "action": "close"}])
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
                trade = trade_record_to_payload(
                    TradeRecord(
                        settled_at="2026-06-10T12:00:00",
                        preset_id="xau",
                        mode="contraction",
                        action="open",
                        spread=3.0,
                        ba_price=100.0,
                        ex_price=97.0,
                        ba_quantity=500.0,
                        mt5_quantity=1.0,
                        ba_side="SELL",
                        mt5_side="BUY",
                    )
                )
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
                self.assertEqual(posted[0]["trades"][0]["spread"], 3.0)

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
