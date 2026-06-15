from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, Field, field_validator

# 机器码为 sha256 截断（纯十六进制）；限制字符集防止注入类内容混入
_DEVICE_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{8,128}$")


def _validate_device_id(value: str) -> str:
    v = value.strip()
    if not _DEVICE_ID_RE.fullmatch(v):
        raise ValueError("device_id 含非法字符")
    return v


class RegisterRequest(BaseModel):
    device_id: str = Field(min_length=8, max_length=128)
    display_name: str = Field(min_length=1, max_length=64)
    contact: str = Field(min_length=11, max_length=11)
    note: str = Field(default="", max_length=512)
    app_version: str = Field(default="", max_length=32)

    @field_validator("device_id")
    @classmethod
    def _check_device_id(cls, value: str) -> str:
        return _validate_device_id(value)

    @field_validator("contact")
    @classmethod
    def validate_mainland_mobile(cls, value: str) -> str:
        phone = value.strip()
        if not phone.isdigit() or len(phone) != 11 or phone[0] != "1" or phone[1] not in "3456789":
            raise ValueError("联系方式必须是大陆 11 位手机号")
        return phone


class HeartbeatRequest(BaseModel):
    device_id: str = Field(min_length=8, max_length=128)

    @field_validator("device_id")
    @classmethod
    def _check_device_id(cls, value: str) -> str:
        return _validate_device_id(value)
    app_version: str = Field(default="", max_length=32)
    ba_account: str = Field(default="", max_length=128)
    mt5_account: str = Field(default="", max_length=128)
    position_summary: str = Field(default="", max_length=1024)
    xau_position: str = Field(default="", max_length=256)
    xag_position: str = Field(default="", max_length=256)
    open_orders_summary: str = Field(default="", max_length=1024)
    xau_open_orders: str = Field(default="", max_length=256)
    xag_open_orders: str = Field(default="", max_length=256)


class HeartbeatResponse(BaseModel):
    status: str
    message: str
    access_token: Optional[str] = None
    expires_in_hours: int = 24
    ba_account_status: str = "pending"
    ex_account_status: str = "pending"
    auto_trade_enabled: bool = False


class TradeItem(BaseModel):
    settled_at: str
    preset_id: str
    mode: str
    action: str = "close"
    spread: float = 0.0
    ba_price: float = 0.0
    ex_price: float = 0.0
    ba_quantity: float = 0.0
    mt5_quantity: float = 0.0
    ba_side: str = ""
    mt5_side: str = ""
    direction: str = ""
    ba_pnl: float = 0.0
    mt5_pnl: float = 0.0
    ba_fee: float = 0.0
    mt5_fee: float = 0.0
    ba_funding_fee: float = 0.0
    ba_rebate: float = 0.0
    net_pnl: float = 0.0


class TradeBatchRequest(BaseModel):
    trades: list[TradeItem]


class AdminLoginRequest(BaseModel):
    password: str


class ChangePasswordRequest(BaseModel):
    old_password: str
    new_password: str = Field(min_length=12, max_length=128)


class DeviceActionRequest(BaseModel):
    reason: str = Field(default="", max_length=256)
    expires_at: Optional[str] = Field(default=None, max_length=32)


class DeviceUpdateRequest(BaseModel):
    display_name: Optional[str] = Field(default=None, min_length=1, max_length=64)
    expires_at: Optional[str] = Field(default=None, max_length=64)
