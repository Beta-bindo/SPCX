#!/usr/bin/env python3
"""运营后台：按条件或全量清理 trades 表（在服务器上执行）。"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.database import get_conn, log_audit  # noqa: E402
from app.routes.admin import _trade_where  # noqa: E402


def _delete_trades(where: str, params: list) -> int:
    delete_where = where.replace("t.", "") if where else ""
    with get_conn() as conn:
        count = conn.execute(
            f"SELECT COUNT(*) FROM trades{delete_where}", params
        ).fetchone()[0]
        if count <= 0:
            return 0
        conn.execute(f"DELETE FROM trades{delete_where}", params)
        log_audit(conn, "delete_trades", detail=f"脚本删除 {count} 条交易记录", actor="purge_trades")
        return count


def main() -> int:
    parser = argparse.ArgumentParser(description="清理运营后台交易明细（trades 表）")
    parser.add_argument("--all", action="store_true", help="删除全部交易记录")
    parser.add_argument("--device-id", help="仅删除指定机器码的交易")
    parser.add_argument("--display-name", help="仅删除昵称匹配的用户（精确匹配）")
    parser.add_argument("--preset-id", choices=["xau", "xag"])
    parser.add_argument("--mode", choices=["contraction", "expansion"])
    parser.add_argument("--date-from", help="结算时间起（YYYY-MM-DD）")
    parser.add_argument("--date-to", help="结算时间止（YYYY-MM-DD）")
    parser.add_argument(
        "--yes",
        action="store_true",
        help="确认执行（未指定 --all 且无任何筛选时也必须提供）",
    )
    args = parser.parse_args()

    if args.display_name:
        with get_conn() as conn:
            rows = conn.execute(
                "SELECT device_id FROM devices WHERE display_name = ?",
                (args.display_name.strip(),),
            ).fetchall()
        device_ids = [r[0] for r in rows]
        if not device_ids:
            print(f"未找到昵称「{args.display_name}」的设备")
            return 1
        total = 0
        for did in device_ids:
            where, params = _trade_where(device_id=did)
            total += _delete_trades(where, params)
        print(f"已删除 {total} 条交易记录（用户 {args.display_name}）")
        return 0

    where, params = _trade_where(
        device_id=args.device_id,
        preset_id=args.preset_id,
        mode=args.mode,
        date_from=args.date_from,
        date_to=args.date_to,
    )
    if not where and not args.all:
        parser.error("请指定筛选条件、--display-name 或 --all")
    if not args.yes:
        parser.error("危险操作：请附加 --yes 确认")

    deleted = _delete_trades(where, params)
    print(f"已删除 {deleted} 条交易记录")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
