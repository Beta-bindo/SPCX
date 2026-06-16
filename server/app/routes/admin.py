from __future__ import annotations

import csv
import io
import time
from datetime import datetime, timedelta, timezone
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response, StreamingResponse

from app.auth import (
    change_admin_password,
    change_admin_user_password,
    create_admin_token,
    create_legacy_admin_token,
    verify_admin_password,
    verify_admin_user_password,
)
from app.config import bump_admin_token_version, settings
from app.database import (
    _utc_now,
    ACCOUNT_STATUS_DISABLED,
    ACCOUNT_STATUS_ENABLED,
    audit_log_where_excluding_superadmin,
    device_is_expired,
    enable_accounts_on_device_approve,
    enrich_device,
    get_admin_user_by_username,
    get_conn,
    log_audit,
    normalize_expires_at,
    row_to_dict,
)
from app.rbac import AdminUser, get_admin_user, parse_modules, require_module
from app.schemas import AdminLoginRequest, ChangePasswordRequest, DeviceActionRequest, DeviceUpdateRequest

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])

RequireAuth = Annotated[AdminUser, Depends(get_admin_user)]
RequireDevices = Annotated[AdminUser, Depends(require_module("devices"))]
RequireDashboard = Annotated[AdminUser, Depends(require_module("dashboard"))]
RequireTrades = Annotated[AdminUser, Depends(require_module("trades"))]
RequirePositions = Annotated[AdminUser, Depends(require_module("positions"))]
RequireAudit = Annotated[AdminUser, Depends(require_module("audit"))]

LOGIN_WINDOW_SEC = 300
LOGIN_MAX_FAILURES = 5
_login_failures: dict[str, list[float]] = {}


def _client_key(request: Request) -> str:
    # 仅在显式信任反向代理时才采用 X-Forwarded-For，否则攻击者可伪造该头绕过限流
    if settings.trust_forwarded:
        forwarded = request.headers.get("x-forwarded-for", "")
        if forwarded:
            return forwarded.split(",", 1)[0].strip()
    return request.client.host if request.client else "unknown"


def _check_login_rate_limit(request: Request) -> None:
    key = _client_key(request)
    now = time.time()
    recent = [ts for ts in _login_failures.get(key, []) if now - ts < LOGIN_WINDOW_SEC]
    _login_failures[key] = recent
    if len(recent) >= LOGIN_MAX_FAILURES:
        raise HTTPException(status_code=429, detail="登录失败次数过多，请稍后再试")


def _record_login_failure(request: Request) -> None:
    key = _client_key(request)
    _login_failures.setdefault(key, []).append(time.time())


def _clear_login_failures(request: Request) -> None:
    _login_failures.pop(_client_key(request), None)


def _issue_token_for_user(user: dict) -> str:
    modules = parse_modules(user.get("role_modules"))
    return create_admin_token(
        user_id=int(user["id"]),
        username=user["username"],
        display_name=user.get("display_name") or user["username"],
        role_id=int(user["role_id"]),
        role_name=user.get("role_name") or "",
        modules=modules,
    )


@router.post("/login")
def admin_login(body: AdminLoginRequest, request: Request) -> dict:
    _check_login_rate_limit(request)
    username = body.username.strip()
    user = get_admin_user_by_username(username)
    if user and user.get("status") == "active":
        if verify_admin_user_password(body.password, user["password_hash"]):
            _clear_login_failures(request)
            with get_conn() as conn:
                conn.execute(
                    "UPDATE admin_users SET last_login_at = ? WHERE id = ?",
                    (_utc_now(), user["id"]),
                )
                log_audit(
                    conn,
                    "admin_login",
                    detail=username,
                    ip=_client_key(request),
                    actor=username,
                )
            return {
                "access_token": _issue_token_for_user(user),
                "token_type": "bearer",
                "user": {
                    "username": user["username"],
                    "display_name": user.get("display_name") or user["username"],
                    "role_name": user.get("role_name") or "",
                },
            }
    # 兼容尚未迁移用户表时的旧版单密码登录
    if username == "admin" and verify_admin_password(body.password):
        _clear_login_failures(request)
        with get_conn() as conn:
            log_audit(conn, "admin_login", detail="legacy", ip=_client_key(request), actor="admin")
        return {"access_token": create_legacy_admin_token(), "token_type": "bearer"}
    _record_login_failure(request)
    raise HTTPException(status_code=401, detail="用户名或密码错误")


@router.post("/logout")
def admin_logout(request: Request, admin: RequireAuth) -> dict:
    # 自增令牌版本，使所有已签发的管理员令牌立即失效
    bump_admin_token_version()
    with get_conn() as conn:
        log_audit(conn, "admin_logout", ip=_client_key(request), actor=admin.username)
    return {"ok": True}


@router.post("/change-password")
def admin_change_password_route(
    body: ChangePasswordRequest,
    request: Request,
    admin: RequireAuth,
) -> dict:
    try:
        if admin.user_id:
            change_admin_user_password(admin.user_id, body.old_password, body.new_password)
        else:
            change_admin_password(body.old_password, body.new_password)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    with get_conn() as conn:
        log_audit(conn, "change_password", ip=_client_key(request), actor=admin.username)
    return {"ok": True}


@router.post("/verify-password")
def admin_verify_password_route(
    body: AdminLoginRequest,
    admin: RequireAuth,
) -> dict:
    if admin.user_id:
        from app.database import get_admin_user_by_id

        user = get_admin_user_by_id(admin.user_id)
        if not user or not verify_admin_user_password(body.password, user["password_hash"]):
            raise HTTPException(status_code=400, detail="当前密码错误")
    elif not verify_admin_password(body.password):
        raise HTTPException(status_code=400, detail="当前密码错误")
    return {"ok": True}


def _trade_where(
    *,
    device_id: Optional[str] = None,
    preset_id: Optional[str] = None,
    mode: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    pnl: Optional[str] = None,
) -> tuple[str, list]:
    clauses: list[str] = []
    params: list = []
    if device_id:
        clauses.append("t.device_id = ?")
        params.append(device_id)
    if preset_id:
        clauses.append("t.preset_id = ?")
        params.append(preset_id)
    if mode:
        clauses.append("t.mode = ?")
        params.append(mode)
    if date_from:
        clauses.append("datetime(replace(t.settled_at, 'T', ' ')) >= datetime(?)")
        params.append(date_from.replace("T", " "))
    if date_to:
        clauses.append("datetime(replace(t.settled_at, 'T', ' ')) <= datetime(?)")
        params.append(date_to.replace("T", " "))
    if pnl == "profit":
        clauses.append("t.net_pnl > 0")
    elif pnl == "loss":
        clauses.append("t.net_pnl < 0")
    elif pnl == "flat":
        clauses.append("t.net_pnl = 0")
    return (" WHERE " + " AND ".join(clauses), params) if clauses else ("", params)


@router.get("/devices")
def list_devices(
    status: Optional[str] = None,
    q: Optional[str] = None,
    expiring: Optional[int] = None,
    page: int = 1,
    page_size: int = 20,
    *,
    admin: RequireDevices,
) -> dict:
    page = max(1, page)
    page_size = min(100, max(1, page_size))
    offset = (page - 1) * page_size
    clauses: list[str] = []
    params: list = []
    if status:
        clauses.append("status = ?")
        params.append(status)
    if q:
        kw = f"%{q.strip()}%"
        clauses.append("(display_name LIKE ? OR contact LIKE ? OR device_id LIKE ? OR ba_account LIKE ? OR mt5_account LIKE ?)")
        params.extend([kw, kw, kw, kw, kw])
    if expiring and expiring > 0:
        # 即将到期：已通过、设置了到期时间、且在 expiring 天内（含已过期）
        cutoff = (datetime.now(timezone.utc) + timedelta(days=expiring)).replace(microsecond=0).isoformat()
        clauses.append("status = 'approved' AND expires_at IS NOT NULL AND expires_at != '' AND expires_at <= ?")
        params.append(cutoff)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    with get_conn() as conn:
        total = conn.execute(f"SELECT COUNT(*) FROM devices{where}", params).fetchone()[0]
        rows = conn.execute(
            f"SELECT * FROM devices{where} ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (*params, page_size, offset),
        ).fetchall()
        devices = [enrich_device(r) for r in rows]
    pages = max(1, (total + page_size - 1) // page_size)
    return {
        "devices": [d for d in devices if d is not None],
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": pages,
    }


@router.get("/positions")
def list_positions(
    page: int = 1,
    page_size: int = 20,
    *,
    admin: RequirePositions,
) -> dict:
    page = max(1, page)
    page_size = min(100, max(1, page_size))
    offset = (page - 1) * page_size
    with get_conn() as conn:
        total = conn.execute(
            "SELECT COUNT(*) FROM devices WHERE status = 'approved'"
        ).fetchone()[0]
        rows = conn.execute(
            """
            SELECT * FROM devices
            WHERE status = 'approved'
            ORDER BY last_seen_at DESC, created_at DESC
            LIMIT ? OFFSET ?
            """,
            (page_size, offset),
        ).fetchall()
        items = [enrich_device(r) for r in rows]
    pages = max(1, (total + page_size - 1) // page_size)
    return {
        "positions": [item for item in items if item is not None],
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": pages,
    }


@router.get("/trades")
def list_trades(
    page: int = 1,
    page_size: int = 50,
    device_id: Optional[str] = None,
    preset_id: Optional[str] = None,
    mode: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    pnl: Optional[str] = None,
    *,
    admin: RequireTrades,
) -> dict:
    page = max(1, page)
    page_size = min(200, max(1, page_size))
    offset = (page - 1) * page_size
    where, params = _trade_where(
        device_id=device_id,
        preset_id=preset_id,
        mode=mode,
        date_from=date_from,
        date_to=date_to,
        pnl=pnl,
    )
    with get_conn() as conn:
        total = conn.execute(
            f"SELECT COUNT(*) FROM trades t{where}", params
        ).fetchone()[0]
        rows = conn.execute(
            f"""
            SELECT t.*, d.display_name
            FROM trades t
            LEFT JOIN devices d ON d.device_id = t.device_id
            {where}
            ORDER BY t.settled_at DESC
            LIMIT ? OFFSET ?
            """,
            (*params, page_size, offset),
        ).fetchall()
        trades = [row_to_dict(r) for r in rows]
        summary_row = conn.execute(
            f"""
            SELECT
                COALESCE(SUM(ba_pnl), 0),
                COALESCE(SUM(mt5_pnl), 0),
                COALESCE(SUM(ba_fee), 0),
                COALESCE(SUM(mt5_fee), 0),
                COALESCE(SUM(ba_funding_fee), 0),
                COALESCE(SUM(ba_rebate), 0),
                COALESCE(SUM(net_pnl), 0)
            FROM trades t{where}
            """,
            params,
        ).fetchone()
    pages = max(1, (total + page_size - 1) // page_size)
    return {
        "trades": trades,
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": pages,
        "summary": {
            "count": total,
            "ba_pnl": round(summary_row[0], 2),
            "mt5_pnl": round(summary_row[1], 2),
            "ba_fee": round(summary_row[2], 4),
            "mt5_fee": round(summary_row[3], 4),
            "ba_funding_fee": round(summary_row[4], 4),
            "ba_rebate": round(summary_row[5], 4),
            "net_pnl": round(summary_row[6], 2),
        },
    }


@router.delete("/trades")
def delete_trades(
    device_id: Optional[str] = None,
    preset_id: Optional[str] = None,
    mode: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    pnl: Optional[str] = None,
    confirm: bool = False,
    *,
    request: Request,
    admin: RequireTrades,
) -> dict:
    """按与列表/导出相同的筛选条件批量删除交易记录。"""
    where, params = _trade_where(
        device_id=device_id,
        preset_id=preset_id,
        mode=mode,
        date_from=date_from,
        date_to=date_to,
        pnl=pnl,
    )
    if not where and not confirm:
        raise HTTPException(
            status_code=400,
            detail="未指定筛选条件时删除全部记录需传 confirm=true",
        )
    delete_where = where.replace("t.", "") if where else ""
    with get_conn() as conn:
        count = conn.execute(
            f"SELECT COUNT(*) FROM trades{delete_where}", params
        ).fetchone()[0]
        if count <= 0:
            return {"ok": True, "deleted": 0}
        conn.execute(f"DELETE FROM trades{delete_where}", params)
        log_audit(
            conn,
            "delete_trades",
            detail=f"删除 {count} 条交易记录",
            ip=_client_key(request),
            actor=admin.username,
        )
    return {"ok": True, "deleted": count}


@router.get("/trades/export")
def export_trades(
    device_id: Optional[str] = None,
    preset_id: Optional[str] = None,
    mode: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    pnl: Optional[str] = None,
    *,
    admin: RequireTrades,
) -> Response:
    where, params = _trade_where(
        device_id=device_id,
        preset_id=preset_id,
        mode=mode,
        date_from=date_from,
        date_to=date_to,
        pnl=pnl,
    )
    max_rows = settings.export_max_rows
    header = [
        "用户", "联系方式", "机器码", "类型", "品种", "模式", "时间", "点差",
        "BA价", "Ex价", "BA数量", "Ex数量", "方向", "BA盈亏", "Exness盈亏",
        "BA手续费", "Exness手续费", "BA资金费", "BA返佣", "净利", "上报时间",
    ]

    def _generate():
        # 流式输出：边查边写，避免一次性把全部行读入内存导致 OOM
        buf = io.StringIO()
        writer = csv.writer(buf)
        buf.write("\ufeff")
        writer.writerow(header)
        yield buf.getvalue()
        buf.seek(0)
        buf.truncate(0)
        with get_conn() as conn:
            cursor = conn.execute(
                f"""
                SELECT t.*, d.display_name, d.contact
                FROM trades t
                LEFT JOIN devices d ON d.device_id = t.device_id
                {where}
                ORDER BY t.settled_at DESC
                LIMIT ?
                """,
                (*params, max_rows),
            )
            while True:
                rows = cursor.fetchmany(1000)
                if not rows:
                    break
                for row in rows:
                    item = row_to_dict(row) or {}
                    writer.writerow(
                        [
                            item.get("display_name") or "",
                            item.get("contact") or "",
                            item.get("device_id") or "",
                            "开仓" if item.get("action") == "open" else "平仓",
                            item.get("preset_id") or "",
                            item.get("mode") or "",
                            item.get("settled_at") or "",
                            item.get("spread") or 0,
                            item.get("ba_price") or 0,
                            item.get("ex_price") or 0,
                            item.get("ba_quantity") or 0,
                            item.get("mt5_quantity") or 0,
                            item.get("direction") or "",
                            item.get("ba_pnl") or 0,
                            item.get("mt5_pnl") or 0,
                            item.get("ba_fee") or 0,
                            item.get("mt5_fee") or 0,
                            item.get("ba_funding_fee") or 0,
                            item.get("ba_rebate") or 0,
                            item.get("net_pnl") or 0,
                            item.get("uploaded_at") or "",
                        ]
                    )
                yield buf.getvalue()
                buf.seek(0)
                buf.truncate(0)

    return StreamingResponse(
        _generate(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="trades.csv"'},
    )


@router.delete("/devices/{device_id}")
def delete_device(device_id: str, request: Request, admin: RequireDevices) -> dict:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id FROM devices WHERE device_id = ?", (device_id,)
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="设备不存在")
        trade_count = conn.execute(
            "SELECT COUNT(*) FROM trades WHERE device_id = ?", (device_id,)
        ).fetchone()[0]
        conn.execute("DELETE FROM trades WHERE device_id = ?", (device_id,))
        conn.execute("DELETE FROM devices WHERE device_id = ?", (device_id,))
        log_audit(
            conn,
            "delete_device",
            target_device_id=device_id,
            detail=f"连带删除交易 {trade_count} 条",
            ip=_client_key(request),
        )
    return {"ok": True}


@router.get("/devices/{device_id}/trades")
def device_trades(device_id: str, admin: RequireDevices) -> dict:
    with get_conn() as conn:
        dev = conn.execute(
            "SELECT * FROM devices WHERE device_id = ?", (device_id,)
        ).fetchone()
        if not dev:
            raise HTTPException(status_code=404, detail="设备不存在")
        rows = conn.execute(
            """
            SELECT * FROM trades WHERE device_id = ?
            ORDER BY settled_at DESC LIMIT 500
            """,
            (device_id,),
        ).fetchall()
    trades = [row_to_dict(r) for r in rows]
    summary = {
        "count": len(trades),
        "net_pnl": round(sum(t.get("net_pnl") or 0 for t in trades), 2),
    }
    return {"device": enrich_device(dev), "summary": summary, "trades": trades}


@router.patch("/devices/{device_id}")
def update_device(
    device_id: str,
    body: DeviceUpdateRequest,
    request: Request,
    admin: RequireDevices,
) -> dict:
    return _update_device_fields(device_id, body, request)


@router.post("/devices/{device_id}/update")
def update_device_post(
    device_id: str,
    body: DeviceUpdateRequest,
    request: Request,
    admin: RequireDevices,
) -> dict:
    return _update_device_fields(device_id, body, request)


def _update_device_fields(
    device_id: str, body: DeviceUpdateRequest, request: Request | None = None
) -> dict:
    updates: list[str] = []
    params: list = []
    detail_parts: list[str] = []
    if body.display_name is not None:
        updates.append("display_name = ?")
        params.append(body.display_name.strip())
        detail_parts.append(f"昵称={body.display_name.strip()}")
    if body.expires_at is not None:
        normalized = normalize_expires_at(body.expires_at)
        updates.append("expires_at = ?")
        params.append(normalized)
        detail_parts.append(f"到期={normalized or '永久'}")
    if not updates:
        raise HTTPException(status_code=400, detail="没有可更新的字段")
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id FROM devices WHERE device_id = ?", (device_id,)
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="设备不存在")
        conn.execute(
            f"UPDATE devices SET {', '.join(updates)} WHERE device_id = ?",
            (*params, device_id),
        )
        log_audit(
            conn,
            "update_device",
            target_device_id=device_id,
            detail="；".join(detail_parts),
            ip=_client_key(request) if request else "",
        )
        updated = conn.execute(
            "SELECT * FROM devices WHERE device_id = ?", (device_id,)
        ).fetchone()
    device = enrich_device(updated)
    return {"ok": True, "device": device}


@router.post("/devices/{device_id}/approve")
def approve_device(
    device_id: str,
    request: Request,
    body: DeviceActionRequest | None = None,
    *,
    admin: RequireDevices,
) -> dict:
    payload = body or DeviceActionRequest()
    now = _utc_now()
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id FROM devices WHERE device_id = ?", (device_id,)
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="设备不存在")
        if payload.expires_at is not None:
            expires_at = normalize_expires_at(payload.expires_at)
            conn.execute(
                """
                UPDATE devices SET status = 'approved', approved_at = ?, reject_reason = '', expires_at = ?
                WHERE device_id = ?
                """,
                (now, expires_at, device_id),
            )
            detail = f"到期={expires_at or '永久'}"
        else:
            conn.execute(
                """
                UPDATE devices SET status = 'approved', approved_at = ?, reject_reason = ''
                WHERE device_id = ?
                """,
                (now, device_id),
            )
            detail = "到期=不变"
        enable_accounts_on_device_approve(conn, device_id)
        log_audit(
            conn,
            "approve",
            target_device_id=device_id,
            detail=detail,
            ip=_client_key(request),
        )
    return {"ok": True, "status": "approved"}


def _set_platform_account_status(
    device_id: str,
    *,
    platform: str,
    status: str,
    request: Request,
) -> dict:
    column = "ba_account_status" if platform == "ba" else "ex_account_status"
    account_col = "ba_account" if platform == "ba" else "mt5_account"
    label = "BA" if platform == "ba" else "EX"
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM devices WHERE device_id = ?", (device_id,)
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="设备不存在")
        device = row_to_dict(row)
        assert device
        account = (device.get(account_col) or "").strip()
        if status == ACCOUNT_STATUS_ENABLED:
            if not account:
                raise HTTPException(status_code=400, detail=f"{label} 账号为空，无法启用")
        conn.execute(
            f"UPDATE devices SET {column} = ? WHERE device_id = ?",
            (status, device_id),
        )
        action = f"{'enable' if status == ACCOUNT_STATUS_ENABLED else 'disable'}_{platform}_account"
        log_audit(
            conn,
            action,
            target_device_id=device_id,
            detail=f"{label}={account or '-'}",
            ip=_client_key(request),
        )
        updated = conn.execute(
            "SELECT * FROM devices WHERE device_id = ?", (device_id,)
        ).fetchone()
    device = enrich_device(updated)
    return {"ok": True, "device": device}


@router.post("/devices/{device_id}/accounts/ba/enable")
def enable_ba_account(
    device_id: str, request: Request, admin: RequireDevices
) -> dict:
    return _set_platform_account_status(
        device_id, platform="ba", status=ACCOUNT_STATUS_ENABLED, request=request
    )


@router.post("/devices/{device_id}/accounts/ba/disable")
def disable_ba_account(
    device_id: str, request: Request, admin: RequireDevices
) -> dict:
    return _set_platform_account_status(
        device_id, platform="ba", status=ACCOUNT_STATUS_DISABLED, request=request
    )


@router.post("/devices/{device_id}/accounts/ex/enable")
def enable_ex_account(
    device_id: str, request: Request, admin: RequireDevices
) -> dict:
    return _set_platform_account_status(
        device_id, platform="ex", status=ACCOUNT_STATUS_ENABLED, request=request
    )


@router.post("/devices/{device_id}/accounts/ex/disable")
def disable_ex_account(
    device_id: str, request: Request, admin: RequireDevices
) -> dict:
    return _set_platform_account_status(
        device_id, platform="ex", status=ACCOUNT_STATUS_DISABLED, request=request
    )


def _set_auto_trade(device_id: str, *, enabled: bool, request: Request) -> dict:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id FROM devices WHERE device_id = ?", (device_id,)
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="设备不存在")
        conn.execute(
            "UPDATE devices SET auto_trade_enabled = ? WHERE device_id = ?",
            (1 if enabled else 0, device_id),
        )
        log_audit(
            conn,
            "enable_auto_trade" if enabled else "disable_auto_trade",
            target_device_id=device_id,
            ip=_client_key(request),
        )
        updated = conn.execute(
            "SELECT * FROM devices WHERE device_id = ?", (device_id,)
        ).fetchone()
    return {"ok": True, "device": enrich_device(updated)}


@router.post("/devices/{device_id}/auto-trade/enable")
def enable_auto_trade(
    device_id: str, request: Request, admin: RequireDevices
) -> dict:
    return _set_auto_trade(device_id, enabled=True, request=request)


@router.post("/devices/{device_id}/auto-trade/disable")
def disable_auto_trade(
    device_id: str, request: Request, admin: RequireDevices
) -> dict:
    return _set_auto_trade(device_id, enabled=False, request=request)


@router.post("/devices/{device_id}/reject")
def reject_device(
    device_id: str,
    body: DeviceActionRequest,
    request: Request,
    admin: RequireDevices,
) -> dict:
    reason = body.reason or "管理员拒绝"
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id FROM devices WHERE device_id = ?", (device_id,)
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="设备不存在")
        conn.execute(
            """
            UPDATE devices SET status = 'rejected', reject_reason = ?
            WHERE device_id = ?
            """,
            (reason, device_id),
        )
        log_audit(
            conn,
            "reject",
            target_device_id=device_id,
            detail=f"原因={reason}",
            ip=_client_key(request),
        )
    return {"ok": True, "status": "rejected"}


@router.post("/devices/{device_id}/disable")
def disable_device(device_id: str, request: Request, admin: RequireDevices) -> dict:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id FROM devices WHERE device_id = ?", (device_id,)
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="设备不存在")
        conn.execute(
            "UPDATE devices SET status = 'disabled' WHERE device_id = ?",
            (device_id,),
        )
        log_audit(
            conn,
            "disable",
            target_device_id=device_id,
            ip=_client_key(request),
        )
    return {"ok": True, "status": "disabled"}


@router.get("/stats")
def admin_stats(admin: RequireDashboard) -> dict:
    now = datetime.now(timezone.utc).replace(microsecond=0)
    soon = (now + timedelta(days=7)).isoformat()
    now_iso = now.isoformat()
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT status, COUNT(*) AS cnt FROM devices GROUP BY status
            """
        ).fetchall()
        trade_count = conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
        total_pnl = conn.execute("SELECT COALESCE(SUM(net_pnl), 0) FROM trades").fetchone()[0]
        online = conn.execute(
            "SELECT COUNT(*) FROM devices WHERE last_seen_at >= ?",
            ((now - timedelta(seconds=900)).isoformat(),),
        ).fetchone()[0]
        expired = conn.execute(
            "SELECT COUNT(*) FROM devices WHERE status='approved' "
            "AND expires_at IS NOT NULL AND expires_at != '' AND expires_at <= ?",
            (now_iso,),
        ).fetchone()[0]
        expiring_soon = conn.execute(
            "SELECT COUNT(*) FROM devices WHERE status='approved' "
            "AND expires_at IS NOT NULL AND expires_at != '' AND expires_at > ? AND expires_at <= ?",
            (now_iso, soon),
        ).fetchone()[0]
    return {
        "devices_by_status": {r["status"]: r["cnt"] for r in rows},
        "trade_count": trade_count,
        "total_net_pnl": round(total_pnl, 2),
        "online": online,
        "expired": expired,
        "expiring_soon": expiring_soon,
    }


@router.get("/audit")
def list_audit(
    page: int = 1,
    page_size: int = 50,
    action: Optional[str] = None,
    *,
    admin: RequireAudit,
) -> dict:
    page = max(1, page)
    page_size = min(200, max(1, page_size))
    offset = (page - 1) * page_size
    where, params = audit_log_where_excluding_superadmin(action=action)
    with get_conn() as conn:
        total = conn.execute(f"SELECT COUNT(*) FROM audit_log{where}", params).fetchone()[0]
        rows = conn.execute(
            f"SELECT * FROM audit_log{where} ORDER BY id DESC LIMIT ? OFFSET ?",
            (*params, page_size, offset),
        ).fetchall()
    pages = max(1, (total + page_size - 1) // page_size)
    return {
        "items": [row_to_dict(r) for r in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": pages,
    }


@router.get("/dashboard")
def admin_dashboard(*, days: int = 30, admin: RequireDashboard) -> dict:
    days = min(180, max(1, days))
    # 序列键必须与 SQL 的「+8 小时」北京日对齐，否则跨 UTC 日界会漏数/错挂日期
    bj_now = datetime.now(timezone.utc) + timedelta(hours=8)
    start = (bj_now - timedelta(days=days - 1)).strftime("%Y-%m-%d")
    with get_conn() as conn:
        # 按北京时区日期聚合：settled_at 存 UTC ISO，+8 小时后取日期
        daily_rows = conn.execute(
            """
            SELECT
                date(datetime(replace(settled_at, 'T', ' '), '+8 hours')) AS day,
                COUNT(*) AS cnt,
                COALESCE(SUM(net_pnl), 0) AS net,
                COUNT(DISTINCT device_id) AS users
            FROM trades
            WHERE action = 'close'
              AND date(datetime(replace(settled_at, 'T', ' '), '+8 hours')) >= ?
            GROUP BY day
            ORDER BY day
            """,
            (start,),
        ).fetchall()
        top_rows = conn.execute(
            """
            SELECT t.device_id,
                   COALESCE(d.display_name, '') AS display_name,
                   COUNT(*) AS cnt,
                   COALESCE(SUM(t.net_pnl), 0) AS net
            FROM trades t
            LEFT JOIN devices d ON d.device_id = t.device_id
            WHERE t.action = 'close'
            GROUP BY t.device_id
            ORDER BY net DESC
            LIMIT 10
            """
        ).fetchall()
    daily = {r["day"]: r for r in daily_rows}
    series = []
    for i in range(days):
        day = (bj_now - timedelta(days=days - 1 - i)).strftime("%Y-%m-%d")
        r = daily.get(day)
        series.append(
            {
                "day": day,
                "count": r["cnt"] if r else 0,
                "net_pnl": round(r["net"], 2) if r else 0.0,
                "users": r["users"] if r else 0,
            }
        )
    top = [
        {
            "device_id": r["device_id"],
            "display_name": r["display_name"] or r["device_id"][:12],
            "count": r["cnt"],
            "net_pnl": round(r["net"], 2),
        }
        for r in top_rows
    ]
    return {"days": days, "daily": series, "top_devices": top}
