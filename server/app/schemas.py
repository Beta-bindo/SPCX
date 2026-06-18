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
    expires_at: Optional[str] = None
    ba_account_status: str = "pending"
    ex_account_status: str = "pending"
    auto_trade_enabled: bool = False


class TradeItem(BaseModel):
    ba_order_no: str = ""
    ex_order_no: str = ""
    product: str = ""
    direction: str = ""
    ba_qty: str = ""
    ex_qty: str = ""
    ba_open_price: str = ""
    ba_close_price: str = ""
    ba_pnl: str = ""
    ex_open_price: str = ""
    ex_close_price: str = ""
    ba_charges: str = ""
    ba_commission: str = ""
    order_time: str = ""
    net_profit: str = ""
    record_key: str = ""


class TradeBatchRequest(BaseModel):
    trades: list[TradeItem]


class AdminLoginRequest(BaseModel):
    username: str = Field(default="admin", min_length=1, max_length=32)
    password: str


class AdminUserCreateRequest(BaseModel):
    username: str = Field(min_length=2, max_length=32)
    password: str = Field(min_length=12, max_length=128)
    display_name: str = Field(default="", max_length=64)
    role_id: int = Field(ge=1)


class AdminUserUpdateRequest(BaseModel):
    display_name: Optional[str] = Field(default=None, max_length=64)
    role_id: Optional[int] = Field(default=None, ge=1)
    status: Optional[str] = Field(default=None, max_length=16)
    password: Optional[str] = Field(default=None, min_length=12, max_length=128)


class AdminRoleCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    description: str = Field(default="", max_length=256)
    modules: list[str] = Field(default_factory=list)


class AdminRoleUpdateRequest(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=64)
    description: Optional[str] = Field(default=None, max_length=256)
    modules: Optional[list[str]] = None


class ChangePasswordRequest(BaseModel):
    old_password: str
    new_password: str = Field(min_length=12, max_length=128)


class DeviceActionRequest(BaseModel):
    reason: str = Field(default="", max_length=256)
    expires_at: Optional[str] = Field(default=None, max_length=32)


class DeviceUpdateRequest(BaseModel):
    display_name: Optional[str] = Field(default=None, min_length=1, max_length=64)
    expires_at: Optional[str] = Field(default=None, max_length=64)
