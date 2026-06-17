"""Server-side trade API field tests."""

from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from server.app.auth import create_device_token
from server.app.database import enable_accounts_on_device_approve, get_conn, init_db
from server.app.routes.client import heartbeat, register, upload_trades
from server.app.schemas import (
    HeartbeatRequest,
    RegisterRequest,
    TradeBatchRequest,
    TradeItem,
)


@contextmanager
def patched_settings(db_path, **overrides):
    """同时替换 config/database/client 三处 settings 绑定，确保覆盖生效。"""
    from server.app import config as cfg_mod
    from server.app import database as db_mod
    from server.app.routes import client as client_mod

    patched = replace(cfg_mod.settings, db_path=str(db_path), **overrides)
    with patch.object(cfg_mod, "settings", patched), patch.object(
        db_mod, "settings", patched
    ), patch.object(client_mod, "settings", patched):
        yield patched


def _insert_device(conn, device_id, status="approved", expires_at=None):
    conn.execute(
        """
        INSERT INTO devices (device_id, display_name, contact, note, status, created_at, last_seen_at, expires_at)
        VALUES (?, '测试', '13800138000', '', ?, '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00', ?)
        """,
        (device_id, status, expires_at),
    )


def _sample_trade(**overrides) -> TradeItem:
    data = {
        "ba_order_no": "7001",
        "ex_order_no": "12345",
        "product": "黄金",
        "direction": "收缩",
        "ba_qty": "500",
        "ex_qty": "1",
        "ba_open_spread": "+3.125",
        "ba_close_spread": "--",
        "ba_pnl": "--",
        "ex_open_spread": "+3.125",
        "ex_close_spread": "--",
        "ba_charges": "--",
        "ba_commission": "-0.2500",
        "order_time": "2026-06-10 12:00:00",
        "net_profit": "-0.2500",
        "record_key": "7001|12345|2026-06-10 12:00:00",
    }
    data.update(overrides)
    return TradeItem(**data)


class ServerTradeApiTests(unittest.TestCase):
    def test_trade_upload_persists_hedge_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "license.db"
            from server.app import config as cfg_mod

            patched = replace(cfg_mod.settings, db_path=str(db_path))
            with patch.object(cfg_mod, "settings", patched), patch(
                "server.app.database.settings", patched
            ):
                init_db()

                device_id = "test-device-fields"
                token = create_device_token(device_id, "approved")
                with get_conn() as conn:
                    conn.execute(
                        """
                        INSERT INTO devices (device_id, display_name, contact, note, status, created_at, last_seen_at)
                        VALUES (?, '测试', '13800138000', '', 'approved', '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00')
                        """,
                        (device_id,),
                    )

                body = TradeBatchRequest(trades=[_sample_trade()])
                result = upload_trades(body, authorization=f"Bearer {token}")
                self.assertTrue(result["ok"])
                self.assertEqual(result["inserted"], 1)

                with get_conn() as conn:
                    row = conn.execute(
                        "SELECT * FROM trades WHERE device_id = ?", (device_id,)
                    ).fetchone()
                data = dict(row)
                self.assertEqual(data["ba_order_no"], "7001")
                self.assertEqual(data["ex_order_no"], "12345")
                self.assertEqual(data["product"], "黄金")
                self.assertEqual(data["direction"], "收缩")
                self.assertEqual(data["ba_open_spread"], "+3.125")
                self.assertEqual(data["net_profit"], "-0.2500")

    def test_trade_upload_dedupes_by_record_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "license.db"
            with patched_settings(db_path):
                init_db()
                device_id = "dedup-dev-001"
                token = create_device_token(device_id, "approved")
                with get_conn() as conn:
                    _insert_device(conn, device_id, status="approved")

                body = TradeBatchRequest(
                    trades=[
                        _sample_trade(record_key="k1|k2|t1", net_profit="+1.2500"),
                        _sample_trade(
                            ba_order_no="7002",
                            record_key="k3|k4|t2",
                            net_profit="-0.5000",
                        ),
                    ]
                )

                result = upload_trades(body, authorization=f"Bearer {token}")
                self.assertEqual(result["inserted"], 2)
                duplicate = upload_trades(body, authorization=f"Bearer {token}")
                self.assertEqual(duplicate["inserted"], 0)

                with get_conn() as conn:
                    rows = conn.execute(
                        "SELECT * FROM trades WHERE device_id = ? ORDER BY ba_order_no",
                        (device_id,),
                    ).fetchall()
                self.assertEqual(len(rows), 2)

    def test_trade_migration_rebuilds_legacy_table(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "legacy.db"
            conn = sqlite3.connect(db_path)
            conn.executescript(
                """
                CREATE TABLE devices (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    device_id TEXT NOT NULL UNIQUE,
                    display_name TEXT NOT NULL DEFAULT '',
                    contact TEXT NOT NULL DEFAULT '',
                    note TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'pending',
                    created_at TEXT NOT NULL,
                    approved_at TEXT,
                    last_seen_at TEXT,
                    reject_reason TEXT NOT NULL DEFAULT '',
                    expires_at TEXT
                );
                CREATE TABLE trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    device_id TEXT NOT NULL,
                    settled_at TEXT NOT NULL,
                    preset_id TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    ba_pnl REAL NOT NULL DEFAULT 0,
                    mt5_pnl REAL NOT NULL DEFAULT 0,
                    ba_fee REAL NOT NULL DEFAULT 0,
                    mt5_fee REAL NOT NULL DEFAULT 0,
                    net_pnl REAL NOT NULL DEFAULT 0,
                    uploaded_at TEXT NOT NULL,
                    report_source TEXT NOT NULL DEFAULT 'ledger',
                    UNIQUE(device_id, settled_at, preset_id, mode)
                );
                """
            )
            conn.commit()
            conn.close()

            from server.app import config as cfg_mod

            patched = replace(cfg_mod.settings, db_path=str(db_path))
            with patch.object(cfg_mod, "settings", patched), patch(
                "server.app.database.settings", patched
            ):
                init_db()
                with get_conn() as conn:
                    cols = {row[1] for row in conn.execute("PRAGMA table_info(trades)").fetchall()}
            self.assertIn("ba_order_no", cols)
            self.assertIn("record_key", cols)
            self.assertNotIn("report_source", cols)
            self.assertNotIn("net_pnl", cols)

    def test_nolicense_register_auto_approves(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "license.db"
            with patched_settings(db_path, nolicense_auto_approve=True):
                init_db()
                result = register(
                    RegisterRequest(
                        device_id="nolicense-dev-1",
                        display_name="免授权用户",
                        contact="13800138000",
                        note="免授权版自动注册",
                        app_version="1.0.0",
                    )
                )
                self.assertEqual(result["status"], "approved")
                self.assertTrue(result["access_token"])

                with get_conn() as conn:
                    row = conn.execute(
                        "SELECT status FROM devices WHERE device_id = ?",
                        ("nolicense-dev-1",),
                    ).fetchone()
                self.assertEqual(dict(row)["status"], "approved")

    def test_auto_approve_disabled_by_flag(self):
        """关闭开关后，含暗号备注的注册不应自动通过。"""
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "license.db"
            with patched_settings(db_path, nolicense_auto_approve=False):
                init_db()
                result = register(
                    RegisterRequest(
                        device_id="nolicense-dev-2",
                        display_name="免授权用户",
                        contact="13800138000",
                        note="免授权版自动注册",
                    )
                )
                self.assertEqual(result["status"], "pending")

    def test_upload_blocked_for_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "license.db"
            with patched_settings(db_path):
                init_db()
                dev = "rejected-dev-001"
                with get_conn() as conn:
                    _insert_device(conn, dev, status="rejected")
                token = create_device_token(dev, "approved")
                body = TradeBatchRequest(trades=[_sample_trade()])
                with self.assertRaises(HTTPException) as ctx:
                    upload_trades(body, authorization=f"Bearer {token}")
                self.assertEqual(ctx.exception.status_code, 403)

    def test_upload_blocked_for_expired(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "license.db"
            with patched_settings(db_path):
                init_db()
                dev = "expired-dev-001"
                with get_conn() as conn:
                    _insert_device(conn, dev, status="approved", expires_at="2000-01-01T00:00:00+00:00")
                token = create_device_token(dev, "approved")
                body = TradeBatchRequest(trades=[_sample_trade()])
                with self.assertRaises(HTTPException) as ctx:
                    upload_trades(body, authorization=f"Bearer {token}")
                self.assertEqual(ctx.exception.status_code, 403)

    def test_heartbeat_ignores_positions_without_token(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "license.db"
            with patched_settings(db_path):
                init_db()
                dev = "heartbeat-dev-01"
                with get_conn() as conn:
                    _insert_device(conn, dev, status="approved")
                # 无令牌：不应写入持仓
                heartbeat(
                    HeartbeatRequest(device_id=dev, xau_position="SPOOFED"),
                    authorization=None,
                )
                with get_conn() as conn:
                    row = conn.execute(
                        "SELECT xau_position FROM devices WHERE device_id = ?", (dev,)
                    ).fetchone()
                self.assertEqual(dict(row)["xau_position"], "")
                # 有令牌：应写入持仓
                token = create_device_token(dev, "approved")
                heartbeat(
                    HeartbeatRequest(device_id=dev, xau_position="REAL"),
                    authorization=f"Bearer {token}",
                )
                with get_conn() as conn:
                    row = conn.execute(
                        "SELECT xau_position FROM devices WHERE device_id = ?", (dev,)
                    ).fetchone()
                self.assertEqual(dict(row)["xau_position"], "REAL")

    def test_heartbeat_rejected_cannot_write_even_with_token(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "license.db"
            with patched_settings(db_path):
                init_db()
                dev = "rejected-hb-dev-1"
                with get_conn() as conn:
                    _insert_device(conn, dev, status="rejected")
                token = create_device_token(dev, "approved")
                heartbeat(
                    HeartbeatRequest(device_id=dev, xau_position="SPOOFED"),
                    authorization=f"Bearer {token}",
                )
                with get_conn() as conn:
                    row = conn.execute(
                        "SELECT xau_position FROM devices WHERE device_id = ?", (dev,)
                    ).fetchone()
                self.assertEqual(dict(row)["xau_position"], "")

    def test_heartbeat_syncs_accounts_when_pending_device(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "license.db"
            with patched_settings(db_path):
                init_db()
                dev = "pending-acct-dev"
                with get_conn() as conn:
                    _insert_device(conn, dev, status="pending")
                token = create_device_token(dev, "pending")
                heartbeat(
                    HeartbeatRequest(
                        device_id=dev,
                        ba_account="ABCD...WXYZ",
                        mt5_account="12345@Exness",
                    ),
                    authorization=f"Bearer {token}",
                )
                with get_conn() as conn:
                    row = conn.execute(
                        "SELECT ba_account, mt5_account, ba_account_status, ex_account_status FROM devices WHERE device_id = ?",
                        (dev,),
                    ).fetchone()
                data = dict(row)
                self.assertEqual(data["ba_account"], "ABCD...WXYZ")
                self.assertEqual(data["mt5_account"], "12345@Exness")
                self.assertEqual(data["ba_account_status"], "pending")
                self.assertEqual(data["ex_account_status"], "pending")

    def test_heartbeat_account_change_sets_pending(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "license.db"
            with patched_settings(db_path):
                init_db()
                dev = "change-acct-dev"
                with get_conn() as conn:
                    _insert_device(conn, dev, status="approved")
                    conn.execute(
                        """
                        UPDATE devices SET ba_account = ?, ba_account_status = 'enabled',
                        mt5_account = ?, ex_account_status = 'enabled'
                        WHERE device_id = ?
                        """,
                        ("OLD1...OLD2", "100@Exness", dev),
                    )
                token = create_device_token(dev, "approved")
                resp = heartbeat(
                    HeartbeatRequest(
                        device_id=dev,
                        ba_account="NEW1...NEW2",
                        mt5_account="100@Exness",
                    ),
                    authorization=f"Bearer {token}",
                )
                self.assertEqual(resp.ba_account_status, "pending")
                self.assertEqual(resp.ex_account_status, "enabled")
                with get_conn() as conn:
                    row = conn.execute(
                        "SELECT ba_account_status, ex_account_status FROM devices WHERE device_id = ?",
                        (dev,),
                    ).fetchone()
                self.assertEqual(dict(row)["ba_account_status"], "pending")

    def test_enable_accounts_on_device_approve(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "license.db"
            with patched_settings(db_path):
                init_db()
                dev = "approve-acct-dev"
                with get_conn() as conn:
                    _insert_device(conn, dev, status="pending")
                    conn.execute(
                        """
                        UPDATE devices SET ba_account = ?, mt5_account = ?
                        WHERE device_id = ?
                        """,
                        ("BA1...BA2", "200@Exness", dev),
                    )
                    conn.execute(
                        "UPDATE devices SET status = 'approved' WHERE device_id = ?",
                        (dev,),
                    )
                    enable_accounts_on_device_approve(conn, dev)
                    row = conn.execute(
                        "SELECT ba_account_status, ex_account_status FROM devices WHERE device_id = ?",
                        (dev,),
                    ).fetchone()
                data = dict(row)
                self.assertEqual(data["ba_account_status"], "enabled")
                self.assertEqual(data["ex_account_status"], "enabled")

    def test_heartbeat_returns_expires_at(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "license.db"
            with patched_settings(db_path):
                init_db()
                dev = "expires-dev"
                expires = "2026-12-31T16:00:00+00:00"
                with get_conn() as conn:
                    _insert_device(conn, dev, status="approved", expires_at=expires)
                token = create_device_token(dev, "approved")
                resp = heartbeat(
                    HeartbeatRequest(device_id=dev),
                    authorization=f"Bearer {token}",
                )
                self.assertEqual(resp.expires_at, expires)


if __name__ == "__main__":
    unittest.main()
