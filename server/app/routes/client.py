from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException

from app.auth import create_device_token, decode_device_token
from app.config import settings
from app.database import (
    _utc_now,
    ACCOUNT_STATUS_DISABLED,
    ACCOUNT_STATUS_ENABLED,
    ACCOUNT_STATUS_PENDING,
    device_is_expired,
    enable_accounts_on_device_approve,
    enrich_device,
    get_conn,
    log_audit,
    normalize_expires_at,
    row_to_dict,
    sync_platform_account,
)
from app.schemas import HeartbeatRequest, HeartbeatResponse, RegisterRequest, TradeBatchRequest

router = APIRouter(prefix="/api/v1", tags=["client"])

NOLICENSE_NOTE_MARK = "免授权版自动注册"


def _should_auto_approve(note: str) -> bool:
    return NOLICENSE_NOTE_MARK in (note or "")


def _approve_device(conn, device_id: str, now: str) -> None:
    conn.execute(
        """
        UPDATE devices
        SET status = 'approved', approved_at = COALESCE(approved_at, ?), last_seen_at = ?
        WHERE device_id = ?
        """,
        (now, now, device_id),
    )


def _device_from_auth(authorization: Optional[str], device_id: str) -> dict:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="缺少授权令牌")
    token = authorization.removeprefix("Bearer ").strip()
    payload = decode_device_token(token)
    if not payload or payload.get("sub") != device_id:
        raise HTTPException(status_code=401, detail="授权无效或已过期")
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM devices WHERE device_id = ?", (device_id,)
        ).fetchone()
    device = row_to_dict(row)
    if not device:
        raise HTTPException(status_code=404, detail="设备未注册")
    return device


@router.post("/register")
def register(body: RegisterRequest) -> dict:
    now = _utc_now()
    with get_conn() as conn:
        existing = conn.execute(
            "SELECT * FROM devices WHERE device_id = ?", (body.device_id,)
        ).fetchone()
        if existing:
            device = row_to_dict(existing)
            assert device
            conn.execute(
                """
                UPDATE devices SET display_name = ?, contact = ?, note = ?, last_seen_at = ?
                WHERE device_id = ?
                """,
                (body.display_name, body.contact, body.note, now, body.device_id),
            )
            status = device["status"]
            if (
                status == "pending"
                and _should_auto_approve(body.note)
                and settings.nolicense_auto_approve
            ):
                _approve_device(conn, body.device_id, now)
                enable_accounts_on_device_approve(conn, body.device_id)
                log_audit(
                    conn,
                    "auto_approve",
                    target_device_id=body.device_id,
                    detail="免授权版自动注册",
                )
                status = "approved"
            message = {
                "pending": "申请已提交，等待管理员审核",
                "approved": "设备已通过审核",
                "rejected": "申请已被拒绝，请联系管理员",
                "disabled": "账号已停用，请联系管理员",
            }.get(status, "未知状态")
            token = (
                create_device_token(body.device_id, status)
                if status != "disabled"
                else None
            )
            return {
                "status": status,
                "message": message,
                "access_token": token,
                "expires_in_hours": settings.jwt_expire_hours,
            }

        auto_approve = _should_auto_approve(body.note) and settings.nolicense_auto_approve
        initial_status = "approved" if auto_approve else "pending"
        conn.execute(
            """
            INSERT INTO devices (device_id, display_name, contact, note, status, created_at, last_seen_at, approved_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                body.device_id,
                body.display_name,
                body.contact,
                body.note,
                initial_status,
                now,
                now,
                now if initial_status == "approved" else None,
            ),
        )
        if auto_approve:
            enable_accounts_on_device_approve(conn, body.device_id)
            log_audit(
                conn,
                "auto_approve",
                target_device_id=body.device_id,
                detail="免授权版自动注册（新设备）",
            )
    if initial_status == "approved":
        return {
            "status": "approved",
            "message": "设备已通过审核",
            "access_token": create_device_token(body.device_id, "approved"),
            "expires_in_hours": settings.jwt_expire_hours,
        }
    return {
        "status": "pending",
        "message": "申请已提交，等待管理员审核",
        "access_token": create_device_token(body.device_id, "pending"),
        "expires_in_hours": settings.jwt_expire_hours,
    }


@router.post("/heartbeat", response_model=HeartbeatResponse)
def heartbeat(
    body: HeartbeatRequest,
    authorization: Optional[str] = Header(default=None),
) -> HeartbeatResponse:
    now = _utc_now()
    # 仅当请求携带与该设备匹配的有效令牌时，才接受账号/持仓/委托等敏感字段写入，
    # 否则任何人都能用 device_id 伪造覆盖他人数据。无令牌时只刷新在线时间。
    authed = False
    if authorization and authorization.startswith("Bearer "):
        _payload = decode_device_token(authorization.removeprefix("Bearer ").strip())
        authed = bool(_payload and _payload.get("sub") == body.device_id)
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM devices WHERE device_id = ?", (body.device_id,)
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="设备未注册，请先提交申请")
        device = row_to_dict(row)
        assert device
        ba_account = device.get("ba_account") or ""
        mt5_account = device.get("mt5_account") or ""
        ba_account_status = device.get("ba_account_status") or "pending"
        ex_account_status = device.get("ex_account_status") or "pending"
        if authed:
            ba_account, ba_account_status = sync_platform_account(
                ba_account, body.ba_account, ba_account_status
            )
            mt5_account, mt5_account_status = sync_platform_account(
                mt5_account, body.mt5_account, ex_account_status
            )
            if device["status"] == "approved":
                conn.execute(
                    """
                    UPDATE devices SET
                        last_seen_at = ?,
                        ba_account = ?,
                        mt5_account = ?,
                        ba_account_status = ?,
                        ex_account_status = ?,
                        position_summary = ?,
                        xau_position = ?,
                        xag_position = ?,
                        open_orders_summary = ?,
                        xau_open_orders = ?,
                        xag_open_orders = ?
                    WHERE device_id = ?
                    """,
                    (
                        now,
                        ba_account,
                        mt5_account,
                        ba_account_status,
                        ex_account_status,
                        body.position_summary,
                        body.xau_position,
                        body.xag_position,
                        body.open_orders_summary,
                        body.xau_open_orders,
                        body.xag_open_orders,
                        body.device_id,
                    ),
                )
            else:
                conn.execute(
                    """
                    UPDATE devices SET
                        last_seen_at = ?,
                        ba_account = ?,
                        mt5_account = ?,
                        ba_account_status = ?,
                        ex_account_status = ?
                    WHERE device_id = ?
                    """,
                    (
                        now,
                        ba_account,
                        mt5_account,
                        ba_account_status,
                        ex_account_status,
                        body.device_id,
                    ),
                )
            device["ba_account"] = ba_account
            device["mt5_account"] = mt5_account
            device["ba_account_status"] = ba_account_status
            device["ex_account_status"] = ex_account_status
        else:
            conn.execute(
                "UPDATE devices SET last_seen_at = ? WHERE device_id = ?",
                (now, body.device_id),
            )

    status = device["status"]
    messages = {
        "pending": "等待管理员审核中…",
        "approved": "授权有效",
        "rejected": device.get("reject_reason") or "申请已被拒绝",
        "disabled": "账号已停用",
        "expired": "授权已到期，请联系管理员续期",
    }
    ba_status = device.get("ba_account_status") or "pending"
    ex_status = device.get("ex_account_status") or "pending"
    auto_trade_enabled = bool(device.get("auto_trade_enabled"))
    account_notes: list[str] = []
    if ba_status == ACCOUNT_STATUS_PENDING and device.get("ba_account"):
        account_notes.append("BA 账号待审核")
    elif ba_status == ACCOUNT_STATUS_DISABLED and device.get("ba_account"):
        account_notes.append("BA 账号已停用")
    if ex_status == ACCOUNT_STATUS_PENDING and device.get("mt5_account"):
        account_notes.append("EX 账号待审核")
    elif ex_status == ACCOUNT_STATUS_DISABLED and device.get("mt5_account"):
        account_notes.append("EX 账号已停用")

    def _response(**kwargs) -> HeartbeatResponse:
        msg = kwargs.pop("message", messages.get(status, status))
        if account_notes and status == "approved":
            msg = f"{msg}（{'；'.join(account_notes)}）"
        return HeartbeatResponse(
            status=kwargs.pop("status", status),
            message=msg,
            ba_account_status=ba_status,
            ex_account_status=ex_status,
            auto_trade_enabled=auto_trade_enabled,
            **kwargs,
        )

    if status == "approved" and device_is_expired(device.get("expires_at")):
        return _response(status="expired", message=messages["expired"])

    if status != "approved":
        if status == "disabled":
            return _response(status=status, message=messages.get(status, status))
        return _response(
            status=status,
            message=messages.get(status, status),
            access_token=create_device_token(body.device_id, status),
            expires_in_hours=settings.jwt_expire_hours,
        )

    if authorization and authorization.startswith("Bearer "):
        token = authorization.removeprefix("Bearer ").strip()
        payload = decode_device_token(token)
        if payload and payload.get("sub") == body.device_id:
            return _response(
                status="approved",
                message=messages["approved"],
                access_token=token,
                expires_in_hours=settings.jwt_expire_hours,
            )

    new_token = create_device_token(body.device_id, "approved")
    return _response(
        status="approved",
        message=messages["approved"],
        access_token=new_token,
        expires_in_hours=settings.jwt_expire_hours,
    )


@router.post("/trades/batch")
def upload_trades(
    body: TradeBatchRequest,
    authorization: Optional[str] = Header(default=None),
) -> dict:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="缺少授权令牌")
    token = authorization.removeprefix("Bearer ").strip()
    payload = decode_device_token(token)
    if not payload:
        raise HTTPException(status_code=401, detail="授权无效或已过期")
    device_id = payload["sub"]
    device = _device_from_auth(authorization, device_id)
    status = device["status"]
    if status in ("disabled", "rejected"):
        raise HTTPException(status_code=403, detail="账号不可用，无法上报交易")
    if status == "approved" and device_is_expired(device.get("expires_at")):
        raise HTTPException(status_code=403, detail="授权已到期，无法上报交易")

    inserted = 0
    now = _utc_now()
    with get_conn() as conn:
        for trade in body.trades:
            try:
                cur = conn.execute(
                    """
                    INSERT OR IGNORE INTO trades (
                        device_id, settled_at, preset_id, mode, action,
                        spread, ba_price, ex_price, ba_quantity, mt5_quantity,
                        ba_side, mt5_side, direction,
                        ba_pnl, mt5_pnl, ba_fee, mt5_fee, ba_funding_fee, ba_rebate, net_pnl, uploaded_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        device_id,
                        trade.settled_at,
                        trade.preset_id,
                        trade.mode,
                        trade.action,
                        trade.spread,
                        trade.ba_price,
                        trade.ex_price,
                        trade.ba_quantity,
                        trade.mt5_quantity,
                        trade.ba_side,
                        trade.mt5_side,
                        trade.direction or (
                            f"BA {trade.ba_side} / Ex {trade.mt5_side}"
                            if trade.ba_side and trade.mt5_side
                            else ""
                        ),
                        trade.ba_pnl,
                        trade.mt5_pnl,
                        trade.ba_fee,
                        trade.mt5_fee,
                        trade.ba_funding_fee,
                        trade.ba_rebate,
                        trade.net_pnl,
                        now,
                    ),
                )
                # INSERT OR IGNORE 命中唯一约束时 rowcount=0，避免把去重当成新增
                if cur.rowcount and cur.rowcount > 0:
                    inserted += 1
            except Exception:
                pass
    return {"ok": True, "inserted": inserted, "total": len(body.trades)}


@router.get("/status/{device_id}")
def public_status(device_id: str) -> dict:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT status, display_name, reject_reason FROM devices WHERE device_id = ?",
            (device_id,),
        ).fetchone()
    if not row:
        return {"registered": False, "status": "unknown"}
    device = row_to_dict(row)
    assert device
    return {
        "registered": True,
        "status": device["status"],
        "display_name": device["display_name"],
        "reject_reason": device.get("reject_reason") or "",
    }
