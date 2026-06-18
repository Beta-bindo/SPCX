from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .config import settings

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
                auto_trade_enabled INTEGER NOT NULL DEFAULT 0,
                position_summary TEXT NOT NULL DEFAULT '',
                xau_position TEXT NOT NULL DEFAULT '',
                xag_position TEXT NOT NULL DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                device_id TEXT NOT NULL,
                ba_order_no TEXT NOT NULL DEFAULT '',
                ex_order_no TEXT NOT NULL DEFAULT '',
                product TEXT NOT NULL DEFAULT '',
                direction TEXT NOT NULL DEFAULT '',
                ba_qty TEXT NOT NULL DEFAULT '',
                ex_qty TEXT NOT NULL DEFAULT '',
                ba_open_price TEXT NOT NULL DEFAULT '',
                ba_close_price TEXT NOT NULL DEFAULT '',
                ba_pnl TEXT NOT NULL DEFAULT '',
                ex_open_price TEXT NOT NULL DEFAULT '',
                ex_close_price TEXT NOT NULL DEFAULT '',
                ba_charges TEXT NOT NULL DEFAULT '',
                ba_commission TEXT NOT NULL DEFAULT '',
                order_time TEXT NOT NULL DEFAULT '',
                net_profit TEXT NOT NULL DEFAULT '',
                record_key TEXT NOT NULL DEFAULT '',
                uploaded_at TEXT NOT NULL
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
            CREATE INDEX IF NOT EXISTS idx_audit_at ON audit_log(at);
            """
        )
        _migrate_devices(conn)
        _migrate_trades(conn)
        _migrate_admin_rbac(conn)


def audit_log_where_excluding_superadmin(*, action: str | None = None) -> tuple[str, list]:
    """构建 audit_log 查询条件，排除角色为超级管理员的用户产生的记录。"""
    from .rbac import SUPERADMIN_ROLE_NAME

    clauses = [
        """actor NOT IN (
            SELECT u.username FROM admin_users u
            INNER JOIN admin_roles r ON u.role_id = r.id
            WHERE r.name = ?
        )"""
    ]
    params: list = [SUPERADMIN_ROLE_NAME]
    if action:
        clauses.insert(0, "action = ?")
        params.insert(0, action)
    return " WHERE " + " AND ".join(clauses), params


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


_NEW_TRADE_COLUMNS = {
    "id",
    "device_id",
    "ba_order_no",
    "ex_order_no",
    "product",
    "direction",
    "ba_qty",
    "ex_qty",
    "ba_open_price",
    "ba_close_price",
    "ba_pnl",
    "ex_open_price",
    "ex_close_price",
    "ba_charges",
    "ba_commission",
    "order_time",
    "net_profit",
    "record_key",
    "uploaded_at",
}


def _create_trade_indexes(conn: sqlite3.Connection) -> None:
    conn.execute("DROP INDEX IF EXISTS idx_trades_event")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_trades_device ON trades(device_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_trades_order_time ON trades(order_time)")
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_trades_record_key
        ON trades(device_id, record_key) WHERE record_key != ''
        """
    )


def _migrate_trades(conn: sqlite3.Connection) -> None:
    cols = {row[1] for row in conn.execute("PRAGMA table_info(trades)").fetchall()}
    if not cols:
        return
    if cols == _NEW_TRADE_COLUMNS:
        _create_trade_indexes(conn)
        return
    conn.execute("DROP TABLE IF EXISTS trades")
    conn.executescript(
        """
        CREATE TABLE trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            device_id TEXT NOT NULL,
            ba_order_no TEXT NOT NULL DEFAULT '',
            ex_order_no TEXT NOT NULL DEFAULT '',
            product TEXT NOT NULL DEFAULT '',
            direction TEXT NOT NULL DEFAULT '',
            ba_qty TEXT NOT NULL DEFAULT '',
            ex_qty TEXT NOT NULL DEFAULT '',
            ba_open_price TEXT NOT NULL DEFAULT '',
            ba_close_price TEXT NOT NULL DEFAULT '',
            ba_pnl TEXT NOT NULL DEFAULT '',
            ex_open_price TEXT NOT NULL DEFAULT '',
            ex_close_price TEXT NOT NULL DEFAULT '',
            ba_charges TEXT NOT NULL DEFAULT '',
            ba_commission TEXT NOT NULL DEFAULT '',
            order_time TEXT NOT NULL DEFAULT '',
            net_profit TEXT NOT NULL DEFAULT '',
            record_key TEXT NOT NULL DEFAULT '',
            uploaded_at TEXT NOT NULL
        );
        """
    )
    _create_trade_indexes(conn)


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
            "auto_trade_enabled",
            "ALTER TABLE devices ADD COLUMN auto_trade_enabled INTEGER NOT NULL DEFAULT 0",
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
    conn.execute(
        """
        UPDATE devices SET ba_account_status = 'unknown'
        WHERE (ba_account = '' OR ba_account IS NULL) AND ba_account_status = 'pending'
        """
    )
    conn.execute(
        """
        UPDATE devices SET ex_account_status = 'unknown'
        WHERE (mt5_account = '' OR mt5_account IS NULL) AND ex_account_status = 'pending'
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
                WHEN ba_account = '' OR ba_account IS NULL THEN 'unknown'
                ELSE ba_account_status END,
            ex_account_status = CASE
                WHEN mt5_account != '' AND ex_account_status = 'pending' THEN 'enabled'
                WHEN mt5_account = '' OR mt5_account IS NULL THEN 'unknown'
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


def _migrate_admin_rbac(conn: sqlite3.Connection) -> None:
    import os

    from .auth import hash_admin_password
    from .rbac import SUPERADMIN_ROLE_NAME, modules_to_json

    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS admin_roles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            description TEXT NOT NULL DEFAULT '',
            modules TEXT NOT NULL DEFAULT '[]',
            is_builtin INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS admin_users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            display_name TEXT NOT NULL DEFAULT '',
            role_id INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'active',
            created_at TEXT NOT NULL,
            last_login_at TEXT,
            FOREIGN KEY(role_id) REFERENCES admin_roles(id)
        );
        CREATE INDEX IF NOT EXISTS idx_admin_users_role ON admin_users(role_id);
        """
    )
    now = _utc_now()
    role_count = conn.execute("SELECT COUNT(*) FROM admin_roles").fetchone()[0]
    if role_count == 0:
        conn.execute(
            """
            INSERT INTO admin_roles (name, description, modules, is_builtin, created_at)
            VALUES (?, ?, ?, 1, ?)
            """,
            (
                SUPERADMIN_ROLE_NAME,
                "拥有全部后台模块权限",
                modules_to_json(["*"]),
                now,
            ),
        )
        conn.execute(
            """
            INSERT INTO admin_roles (name, description, modules, is_builtin, created_at)
            VALUES (?, ?, ?, 0, ?)
            """,
            (
                "运营人员",
                "审核设备、查看数据，不可管理角色与用户",
                modules_to_json(["devices", "dashboard", "trades", "positions", "audit"]),
                now,
            ),
        )
        conn.execute(
            """
            INSERT INTO admin_roles (name, description, modules, is_builtin, created_at)
            VALUES (?, ?, ?, 0, ?)
            """,
            (
                "只读访客",
                "仅可查看数据看板与交易明细",
                modules_to_json(["dashboard", "trades"]),
                now,
            ),
        )
    user_count = conn.execute("SELECT COUNT(*) FROM admin_users").fetchone()[0]
    if user_count == 0:
        super_row = conn.execute(
            "SELECT id FROM admin_roles WHERE name = ?", (SUPERADMIN_ROLE_NAME,)
        ).fetchone()
        if super_row:
            stored_hash = os.environ.get("TA_ADMIN_PASSWORD_HASH") or settings.admin_password_hash
            plain = os.environ.get("TA_ADMIN_PASSWORD") or settings.admin_password
            if stored_hash:
                pwd_hash = stored_hash
            elif plain:
                pwd_hash = hash_admin_password(plain)
            else:
                pwd_hash = hash_admin_password("TradeAdmin@2026!BS")
            conn.execute(
                """
                INSERT INTO admin_users (
                    username, password_hash, display_name, role_id, status, created_at
                ) VALUES (?, ?, ?, ?, 'active', ?)
                """,
                (
                    "admin",
                    pwd_hash,
                    "系统管理员",
                    super_row[0],
                    now,
                ),
            )


def get_admin_user_by_username(username: str) -> dict | None:
    with get_conn() as conn:
        row = conn.execute(
            """
            SELECT u.*, r.name AS role_name, r.modules AS role_modules
            FROM admin_users u
            JOIN admin_roles r ON r.id = u.role_id
            WHERE u.username = ?
            """,
            (username.strip(),),
        ).fetchone()
    return row_to_dict(row)


def get_admin_user_by_id(user_id: int) -> dict | None:
    with get_conn() as conn:
        row = conn.execute(
            """
            SELECT u.*, r.name AS role_name, r.modules AS role_modules
            FROM admin_users u
            JOIN admin_roles r ON r.id = u.role_id
            WHERE u.id = ?
            """,
            (user_id,),
        ).fetchone()
    return row_to_dict(row)


def enrich_device(row: sqlite3.Row | dict | None) -> dict | None:
    device = row_to_dict(row) if not isinstance(row, dict) else dict(row)
    if device is None:
        return None
    device["online"] = device_is_online(device.get("last_seen_at"))
    device["expired"] = device_is_expired(device.get("expires_at"))
    device["auto_trade_enabled"] = bool(device.get("auto_trade_enabled"))
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
