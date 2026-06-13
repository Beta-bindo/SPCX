from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException

from app.auth import create_device_token, decode_device_token
from app.config import settings
from app.database import (
    _utc_now,
    device_is_expired,
    enrich_device,
    get_conn,
    normalize_expires_at,
    row_to_dict,
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
            if _should_auto_approve(body.note) and status == "pending":
                _approve_device(conn, body.device_id, now)
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

        initial_status = "approved" if _should_auto_approve(body.note) else "pending"
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
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM devices WHERE device_id = ?", (body.device_id,)
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="设备未注册，请先提交申请")
        device = row_to_dict(row)
        assert device
        conn.execute(
            """
            UPDATE devices SET
                last_seen_at = ?,
                ba_account = COALESCE(NULLIF(?, ''), ba_account),
                mt5_account = COALESCE(NULLIF(?, ''), mt5_account),
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
                body.ba_account,
                body.mt5_account,
                body.position_summary,
                body.xau_position,
                body.xag_position,
                body.open_orders_summary,
                body.xau_open_orders,
                body.xag_open_orders,
                body.device_id,
            ),
        )

    status = device["status"]
    messages = {
        "pending": "等待管理员审核中…",
        "approved": "授权有效",
        "rejected": device.get("reject_reason") or "申请已被拒绝",
        "disabled": "账号已停用",
        "expired": "授权已到期，请联系管理员续期",
    }

    if status == "approved" and device_is_expired(device.get("expires_at")):
        return HeartbeatResponse(status="expired", message=messages["expired"])

    if status != "approved":
        if status == "disabled":
            return HeartbeatResponse(status=status, message=messages.get(status, status))
        return HeartbeatResponse(
            status=status,
            message=messages.get(status, status),
            access_token=create_device_token(body.device_id, status),
            expires_in_hours=settings.jwt_expire_hours,
        )

    if authorization and authorization.startswith("Bearer "):
        token = authorization.removeprefix("Bearer ").strip()
        payload = decode_device_token(token)
        if payload and payload.get("sub") == body.device_id:
            return HeartbeatResponse(
                status="approved",
                message=messages["approved"],
                access_token=token,
                expires_in_hours=settings.jwt_expire_hours,
            )

    new_token = create_device_token(body.device_id, "approved")
    return HeartbeatResponse(
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
    if device["status"] == "disabled":
        raise HTTPException(status_code=403, detail="账号已停用，无法上报交易")

    inserted = 0
    now = _utc_now()
    with get_conn() as conn:
        for trade in body.trades:
            try:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO trades (
                        device_id, settled_at, preset_id, mode, action,
                        spread, ba_price, ex_price, ba_quantity, mt5_quantity,
                        ba_side, mt5_side, direction,
                        ba_pnl, mt5_pnl, ba_fee, mt5_fee, net_pnl, uploaded_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                        trade.net_pnl,
                        now,
                    ),
                )
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
