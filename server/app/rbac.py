from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Optional

from fastapi import Header, HTTPException

from .auth import decode_admin_token

# 后台可授权模块（与导航/页面一一对应）
ADMIN_MODULES: dict[str, str] = {
    "devices": "授权管理",
    "dashboard": "数据看板",
    "trades": "交易明细",
    "positions": "持仓列表",
    "audit": "操作日志",
    "roles": "角色管理",
    "users": "用户管理",
}

ALL_MODULE_KEYS = list(ADMIN_MODULES.keys())
SUPERADMIN_ROLE_NAME = "超级管理员"
# 用户管理列表中隐藏的系统内置账号（不可通过用户管理增删改）
HIDDEN_ADMIN_USERNAME = "admin"


@dataclass
class AdminUser:
    user_id: int
    username: str
    display_name: str
    role_id: int
    role_name: str
    modules: list[str]

    def can_access(self, module: str) -> bool:
        if "*" in self.modules:
            return True
        return module in self.modules


def parse_modules(raw: str | None) -> list[str]:
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return []
    if not isinstance(data, list):
        return []
    out: list[str] = []
    for item in data:
        key = str(item).strip()
        if key == "*" or key in ADMIN_MODULES:
            out.append(key)
    return out


def modules_to_json(modules: list[str]) -> str:
    cleaned: list[str] = []
    for item in modules:
        key = str(item).strip()
        if not key:
            continue
        if key == "*" or key in ADMIN_MODULES:
            cleaned.append(key)
    return json.dumps(cleaned, ensure_ascii=False)


def normalize_modules(modules: list[str]) -> list[str]:
    if "*" in modules:
        return ["*"]
    return [m for m in ALL_MODULE_KEYS if m in modules]


def get_admin_user(authorization: Optional[str] = Header(default=None)) -> AdminUser:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="需要管理员登录")
    token = authorization.removeprefix("Bearer ").strip()
    payload = decode_admin_token(token)
    if not payload:
        raise HTTPException(status_code=401, detail="管理员会话已过期")
    modules = payload.get("modules") or []
    if isinstance(modules, str):
        modules = parse_modules(modules)
    if payload.get("legacy"):
        modules = ["*"]
    return AdminUser(
        user_id=int(payload.get("uid") or 0),
        username=str(payload.get("sub") or "admin"),
        display_name=str(payload.get("name") or payload.get("sub") or "admin"),
        role_id=int(payload.get("role_id") or 0),
        role_name=str(payload.get("role_name") or ""),
        modules=list(modules),
    )


def require_module(module: str):
    def _dep(authorization: Optional[str] = Header(default=None)) -> AdminUser:
        user = get_admin_user(authorization)
        if not user.can_access(module):
            label = ADMIN_MODULES.get(module, module)
            raise HTTPException(status_code=403, detail=f"无权限访问：{label}")
        return user

    return _dep
