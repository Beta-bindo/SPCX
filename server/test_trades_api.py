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

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from app.auth import create_device_token
from app.database import get_conn, init_db
from app.routes.client import heartbeat, register, upload_trades
from app.schemas import (
    HeartbeatRequest,
    RegisterRequest,
    TradeBatchRequest,
    TradeItem,
)


@contextmanager
def patched_settings(db_path, **overrides):
    """同时替换 config/database/client 三处 settings 绑定，确保覆盖生效。"""
    from app import config as cfg_mod
    from app import database as db_mod
    from app.routes import client as client_mod

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


class ServerTradeApiTests(unittest.TestCase):
    def test_trade_upload_persists_order_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "license.db"
            from app import config as cfg_mod

            patched = replace(cfg_mod.settings, db_path=str(db_path))
            with patch.object(cfg_mod, "settings", patched), patch(
                "app.database.settings", patched
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

                body = TradeBatchRequest(
                    trades=[
                        TradeItem(
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
                            direction="BA SELL / Ex BUY",
                        )
                    ]
                )
                result = upload_trades(body, authorization=f"Bearer {token}")
                self.assertTrue(result["ok"])
                self.assertEqual(result["inserted"], 1)

                with get_conn() as conn:
                    row = conn.execute(
                        "SELECT * FROM trades WHERE device_id = ?", (device_id,)
                    ).fetchone()
                data = dict(row)
                self.assertEqual(data["action"], "open")
                self.assertEqual(data["spread"], 3.125)
                self.assertEqual(data["ba_price"], 2650.5)
                self.assertEqual(data["ex_price"], 2647.375)
                self.assertEqual(data["ba_quantity"], 500.0)
                self.assertEqual(data["mt5_quantity"], 1.0)
                self.assertEqual(data["direction"], "BA SELL / Ex BUY")

    def test_trade_migration_adds_new_columns(self):
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
                    UNIQUE(device_id, settled_at, preset_id, mode)
                );
                """
            )
            conn.commit()
            conn.close()

            from app import config as cfg_mod

            patched = replace(cfg_mod.settings, db_path=str(db_path))
            with patch.object(cfg_mod, "settings", patched), patch(
                "app.database.settings", patched
            ):
                init_db()
                with get_conn() as conn:
                    cols = {row[1] for row in conn.execute("PRAGMA table_info(trades)").fetchall()}
            self.assertIn("action", cols)
            self.assertIn("spread", cols)
            self.assertIn("ba_price", cols)


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
                body = TradeBatchRequest(
                    trades=[TradeItem(settled_at="2026-06-10T12:00:00", preset_id="xau", mode="contraction")]
                )
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
                body = TradeBatchRequest(
                    trades=[TradeItem(settled_at="2026-06-10T12:00:00", preset_id="xau", mode="contraction")]
                )
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


if __name__ == "__main__":
    unittest.main()
