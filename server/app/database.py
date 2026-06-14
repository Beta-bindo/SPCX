from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from app.config import settings

ONLINE_WINDOW_SEC = 900  # 15 分钟内视为在线

ACCOUNT_STATUS_PENDING = "pending"
ACCOUNT_STATUS_ENABLED = "enabled"
ACCOUNT_STATUS_DISABLED = "disabled"


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def init_db() -> None:
    path = Path(settings.db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with get_conn() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS devices (
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
                expires_at TEXT,
                ba_account TEXT NOT NULL DEFAULT '',
                mt5_account TEXT NOT NULL DEFAULT '',
                ba_account_status TEXT NOT NULL DEFAULT 'pending',
                ex_account_status TEXT NOT NULL DEFAULT 'pending',
                position_summary TEXT NOT NULL DEFAULT '',
                xau_position TEXT NOT NULL DEFAULT '',
                xag_position TEXT NOT NULL DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                device_id TEXT NOT NULL,
                settled_at TEXT NOT NULL,
                preset_id TEXT NOT NULL,
                mode TEXT NOT NULL,
                action TEXT NOT NULL DEFAULT 'close',
                spread REAL NOT NULL DEFAULT 0,
                ba_price REAL NOT NULL DEFAULT 0,
                ex_price REAL NOT NULL DEFAULT 0,
                ba_quantity REAL NOT NULL DEFAULT 0,
                mt5_quantity REAL NOT NULL DEFAULT 0,
                ba_side TEXT NOT NULL DEFAULT '',
                mt5_side TEXT NOT NULL DEFAULT '',
                direction TEXT NOT NULL DEFAULT '',
                ba_pnl REAL NOT NULL DEFAULT 0,
                mt5_pnl REAL NOT NULL DEFAULT 0,
                ba_fee REAL NOT NULL DEFAULT 0,
                mt5_fee REAL NOT NULL DEFAULT 0,
                ba_funding_fee REAL NOT NULL DEFAULT 0,
                ba_rebate REAL NOT NULL DEFAULT 0,
                net_pnl REAL NOT NULL DEFAULT 0,
                uploaded_at TEXT NOT NULL,
                UNIQUE(device_id, settled_at, preset_id, mode, action)
            );

            CREATE TABLE IF NOT EXISTS audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                at TEXT NOT NULL,
                actor TEXT NOT NULL DEFAULT 'admin',
                action TEXT NOT NULL,
                target_device_id TEXT NOT NULL DEFAULT '',
                detail TEXT NOT NULL DEFAULT '',
                ip TEXT NOT NULL DEFAULT ''
            );

            CREATE INDEX IF NOT EXISTS idx_devices_status ON devices(status);
            CREATE INDEX IF NOT EXISTS idx_trades_device ON trades(device_id);
            CREATE INDEX IF NOT EXISTS idx_trades_settled ON trades(settled_at);
            CREATE INDEX IF NOT EXISTS idx_audit_at ON audit_log(at);
            """
        )
        _migrate_devices(conn)
        _migrate_trades(conn)


def log_audit(
    conn: sqlite3.Connection,
    action: str,
    *,
    target_device_id: str = "",
    detail: str = "",
    ip: str = "",
    actor: str = "admin",
) -> None:
    """写一条运营审计记录；失败不应影响主流程。"""
    try:
        conn.execute(
            """
            INSERT INTO audit_log (at, actor, action, target_device_id, detail, ip)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (_utc_now(), actor, action, target_device_id or "", detail or "", ip or ""),
        )
    except Exception:
        pass


def _migrate_trades(conn: sqlite3.Connection) -> None:
    cols = {row[1] for row in conn.execute("PRAGMA table_info(trades)").fetchall()}
    if not cols:
        return
    for name, ddl in (
        ("action", "ALTER TABLE trades ADD COLUMN action TEXT NOT NULL DEFAULT 'close'"),
        ("spread", "ALTER TABLE trades ADD COLUMN spread REAL NOT NULL DEFAULT 0"),
        ("ba_price", "ALTER TABLE trades ADD COLUMN ba_price REAL NOT NULL DEFAULT 0"),
        ("ex_price", "ALTER TABLE trades ADD COLUMN ex_price REAL NOT NULL DEFAULT 0"),
        ("ba_quantity", "ALTER TABLE trades ADD COLUMN ba_quantity REAL NOT NULL DEFAULT 0"),
        ("mt5_quantity", "ALTER TABLE trades ADD COLUMN mt5_quantity REAL NOT NULL DEFAULT 0"),
        ("ba_side", "ALTER TABLE trades ADD COLUMN ba_side TEXT NOT NULL DEFAULT ''"),
        ("mt5_side", "ALTER TABLE trades ADD COLUMN mt5_side TEXT NOT NULL DEFAULT ''"),
        ("direction", "ALTER TABLE trades ADD COLUMN direction TEXT NOT NULL DEFAULT ''"),
        ("ba_funding_fee", "ALTER TABLE trades ADD COLUMN ba_funding_fee REAL NOT NULL DEFAULT 0"),
        ("ba_rebate", "ALTER TABLE trades ADD COLUMN ba_rebate REAL NOT NULL DEFAULT 0"),
    ):
        if name not in cols:
            conn.execute(ddl)

    index_rows = conn.execute("PRAGMA index_list(trades)").fetchall()
    has_action_unique = False
    for index_row in index_rows:
        index_name = index_row[1]
        if not index_name.startswith("sqlite_autoindex"):
            continue
        info = conn.execute(f"PRAGMA index_info({index_name})").fetchall()
        col_names = [row[2] for row in info]
        if col_names == ["device_id", "settled_at", "preset_id", "mode", "action"]:
            has_action_unique = True
            break
        if col_names == ["device_id", "settled_at", "preset_id", "mode"]:
            conn.executescript(
                """
                CREATE TABLE trades_migrated (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    device_id TEXT NOT NULL,
                    settled_at TEXT NOT NULL,
                    preset_id TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    action TEXT NOT NULL DEFAULT 'close',
                    spread REAL NOT NULL DEFAULT 0,
                    ba_price REAL NOT NULL DEFAULT 0,
                    ex_price REAL NOT NULL DEFAULT 0,
                    ba_quantity REAL NOT NULL DEFAULT 0,
                    mt5_quantity REAL NOT NULL DEFAULT 0,
                    ba_side TEXT NOT NULL DEFAULT '',
                    mt5_side TEXT NOT NULL DEFAULT '',
                    direction TEXT NOT NULL DEFAULT '',
                    ba_pnl REAL NOT NULL DEFAULT 0,
                    mt5_pnl REAL NOT NULL DEFAULT 0,
                    ba_fee REAL NOT NULL DEFAULT 0,
                    mt5_fee REAL NOT NULL DEFAULT 0,
                    ba_funding_fee REAL NOT NULL DEFAULT 0,
                    ba_rebate REAL NOT NULL DEFAULT 0,
                    net_pnl REAL NOT NULL DEFAULT 0,
                    uploaded_at TEXT NOT NULL,
                    UNIQUE(device_id, settled_at, preset_id, mode, action)
                );
                INSERT INTO trades_migrated (
                    id, device_id, settled_at, preset_id, mode, action,
                    spread, ba_price, ex_price, ba_quantity, mt5_quantity,
                    ba_side, mt5_side, direction,
                    ba_pnl, mt5_pnl, ba_fee, mt5_fee, ba_funding_fee, ba_rebate, net_pnl, uploaded_at
                )
                SELECT
                    id, device_id, settled_at, preset_id, mode, 'close',
                    COALESCE(spread, 0), COALESCE(ba_price, 0), COALESCE(ex_price, 0),
                    COALESCE(ba_quantity, 0), COALESCE(mt5_quantity, 0),
                    COALESCE(ba_side, ''), COALESCE(mt5_side, ''), COALESCE(direction, ''),
                    ba_pnl, mt5_pnl, ba_fee, mt5_fee, COALESCE(ba_funding_fee, 0), COALESCE(ba_rebate, 0), net_pnl, uploaded_at
                FROM trades;
                DROP TABLE trades;
                ALTER TABLE trades_migrated RENAME TO trades;
                CREATE INDEX IF NOT EXISTS idx_trades_device ON trades(device_id);
                """
            )
            return
    if not has_action_unique:
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_trades_event "
            "ON trades(device_id, settled_at, preset_id, mode, action)"
        )


def _migrate_devices(conn: sqlite3.Connection) -> None:
    cols = {row[1] for row in conn.execute("PRAGMA table_info(devices)").fetchall()}
    for name, ddl in (
        ("expires_at", "ALTER TABLE devices ADD COLUMN expires_at TEXT"),
        ("ba_account", "ALTER TABLE devices ADD COLUMN ba_account TEXT NOT NULL DEFAULT ''"),
        ("mt5_account", "ALTER TABLE devices ADD COLUMN mt5_account TEXT NOT NULL DEFAULT ''"),
        (
            "ba_account_status",
            "ALTER TABLE devices ADD COLUMN ba_account_status TEXT NOT NULL DEFAULT 'pending'",
        ),
        (
            "ex_account_status",
            "ALTER TABLE devices ADD COLUMN ex_account_status TEXT NOT NULL DEFAULT 'pending'",
        ),
        (
            "position_summary",
            "ALTER TABLE devices ADD COLUMN position_summary TEXT NOT NULL DEFAULT ''",
        ),
        ("xau_position", "ALTER TABLE devices ADD COLUMN xau_position TEXT NOT NULL DEFAULT ''"),
        ("xag_position", "ALTER TABLE devices ADD COLUMN xag_position TEXT NOT NULL DEFAULT ''"),
        (
            "open_orders_summary",
            "ALTER TABLE devices ADD COLUMN open_orders_summary TEXT NOT NULL DEFAULT ''",
        ),
        (
            "xau_open_orders",
            "ALTER TABLE devices ADD COLUMN xau_open_orders TEXT NOT NULL DEFAULT ''",
        ),
        (
            "xag_open_orders",
            "ALTER TABLE devices ADD COLUMN xag_open_orders TEXT NOT NULL DEFAULT ''",
        ),
    ):
        if name not in cols:
            conn.execute(ddl)
    conn.execute(
        """
        UPDATE devices SET ba_account_status = 'enabled'
        WHERE status = 'approved' AND ba_account != '' AND ba_account_status = 'pending'
        """
    )
    conn.execute(
        """
        UPDATE devices SET ex_account_status = 'enabled'
        WHERE status = 'approved' AND mt5_account != '' AND ex_account_status = 'pending'
        """
    )


def normalize_account_status(value: str | None) -> str:
    raw = (value or "").strip().lower()
    if raw in (
        ACCOUNT_STATUS_PENDING,
        ACCOUNT_STATUS_ENABLED,
        ACCOUNT_STATUS_DISABLED,
    ):
        return raw
    return ACCOUNT_STATUS_PENDING


def sync_platform_account(
    old_account: str,
    new_account: str,
    current_status: str,
) -> tuple[str, str]:
    """账号同步：有变更则替换并置为待审核。"""
    stored = (old_account or "").strip()
    incoming = (new_account or "").strip()
    status = normalize_account_status(current_status)
    if not incoming:
        return stored, status
    if incoming != stored:
        return incoming, ACCOUNT_STATUS_PENDING
    return stored or incoming, status


def enable_accounts_on_device_approve(conn: sqlite3.Connection, device_id: str) -> None:
    """设备审核通过时，将仍为待审且已填写的平台账号置为启用（不覆盖已停用）。"""
    conn.execute(
        """
        UPDATE devices SET
            ba_account_status = CASE
                WHEN ba_account != '' AND ba_account_status = 'pending' THEN 'enabled'
                ELSE ba_account_status END,
            ex_account_status = CASE
                WHEN mt5_account != '' AND ex_account_status = 'pending' THEN 'enabled'
                ELSE ex_account_status END
        WHERE device_id = ?
        """,
        (device_id,),
    )


def parse_iso(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None


def device_is_online(last_seen_at: str | None, *, now: datetime | None = None) -> bool:
    seen = parse_iso(last_seen_at)
    if seen is None:
        return False
    if seen.tzinfo is None:
        seen = seen.replace(tzinfo=timezone.utc)
    now = now or datetime.now(timezone.utc)
    return (now - seen).total_seconds() <= ONLINE_WINDOW_SEC


def device_is_expired(expires_at: str | None, *, now: datetime | None = None) -> bool:
    exp = parse_iso(expires_at)
    if exp is None:
        return False
    if exp.tzinfo is None:
        exp = exp.replace(tzinfo=timezone.utc)
    now = now or datetime.now(timezone.utc)
    return now >= exp


def normalize_expires_at(value: str | None) -> str | None:
    if value is None:
        return None
    raw = value.strip()
    if not raw:
        return None
    try:
        if "T" in raw:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        else:
            dt = datetime.fromisoformat(raw.replace(" ", "T"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        else:
            dt = dt.astimezone(timezone.utc)
        return dt.replace(microsecond=0).isoformat()
    except ValueError:
        return raw


def enrich_device(row: sqlite3.Row | dict | None) -> dict | None:
    device = row_to_dict(row) if not isinstance(row, dict) else dict(row)
    if device is None:
        return None
    device["online"] = device_is_online(device.get("last_seen_at"))
    device["expired"] = device_is_expired(device.get("expires_at"))
    return device


_pragma_initialized = False


@contextmanager
def get_conn():
    global _pragma_initialized
    conn = sqlite3.connect(settings.db_path, timeout=5.0)
    conn.row_factory = sqlite3.Row
    try:
        # WAL 提升读写并发；busy_timeout 缓解并发写锁；NORMAL 在 WAL 下兼顾安全与性能
        conn.execute("PRAGMA busy_timeout=5000")
        if not _pragma_initialized:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            _pragma_initialized = True
        yield conn
        conn.commit()
    finally:
        conn.close()


def row_to_dict(row: Optional[sqlite3.Row]) -> Optional[dict]:
    if row is None:
        return None
    return dict(row)
