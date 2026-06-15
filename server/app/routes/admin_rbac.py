from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from app.auth import (
    hash_admin_password,
)
from app.database import _utc_now, get_conn, log_audit, row_to_dict
from app.rbac import (
    ADMIN_MODULES,
    HIDDEN_ADMIN_USERNAME,
    SUPERADMIN_ROLE_NAME,
    AdminUser,
    get_admin_user,
    modules_to_json,
    normalize_modules,
    parse_modules,
    require_module,
)
from app.schemas import (
    AdminRoleCreateRequest,
    AdminRoleUpdateRequest,
    AdminUserCreateRequest,
    AdminUserUpdateRequest,
)

router = APIRouter(prefix="/api/v1/admin", tags=["admin-rbac"])


def _role_payload(row: dict) -> dict:
    modules = parse_modules(row.get("modules"))
    return {
        "id": row["id"],
        "name": row["name"],
        "description": row.get("description") or "",
        "modules": modules,
        "module_labels": [ADMIN_MODULES.get(m, m) for m in modules if m != "*"],
        "is_builtin": bool(row.get("is_builtin")),
        "created_at": row.get("created_at"),
        "user_count": row.get("user_count", 0),
    }


def _user_payload(row: dict) -> dict:
    return {
        "id": row["id"],
        "username": row["username"],
        "display_name": row.get("display_name") or row["username"],
        "role_id": row["role_id"],
        "role_name": row.get("role_name") or "",
        "status": row.get("status") or "active",
        "created_at": row.get("created_at"),
        "last_login_at": row.get("last_login_at"),
    }


def _me_payload(admin: AdminUser) -> dict:
    modules = admin.modules
    nav = []
    for key, label in ADMIN_MODULES.items():
        if admin.can_access(key):
            nav.append({"key": key, "label": label})
    return {
        "id": admin.user_id,
        "username": admin.username,
        "display_name": admin.display_name,
        "role_id": admin.role_id,
        "role_name": admin.role_name,
        "modules": modules,
        "nav": nav,
    }


@router.get("/me")
def admin_me(admin: AdminUser = Depends(get_admin_user)) -> dict:
    return _me_payload(admin)


@router.get("/modules")
def list_modules(_: AdminUser = Depends(get_admin_user)) -> dict:
    return {
        "modules": [
            {"key": key, "label": label} for key, label in ADMIN_MODULES.items()
        ]
    }


@router.get("/roles")
def list_roles(admin: AdminUser = Depends(require_module("roles"))) -> dict:
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT r.*, (
                SELECT COUNT(*) FROM admin_users u
                WHERE u.role_id = r.id AND u.username != ?
            ) AS user_count
            FROM admin_roles r
            ORDER BY r.is_builtin DESC, r.id ASC
            """,
            (HIDDEN_ADMIN_USERNAME,),
        ).fetchall()
    return {"roles": [_role_payload(row_to_dict(r) or {}) for r in rows]}


@router.post("/roles")
def create_role(
    body: AdminRoleCreateRequest,
    request: Request,
    admin: AdminUser = Depends(require_module("roles")),
) -> dict:
    modules = normalize_modules(body.modules)
    if not modules:
        raise HTTPException(status_code=400, detail="请至少选择一个模块权限")
    now = _utc_now()
    with get_conn() as conn:
        exists = conn.execute(
            "SELECT id FROM admin_roles WHERE name = ?", (body.name.strip(),)
        ).fetchone()
        if exists:
            raise HTTPException(status_code=400, detail="角色名称已存在")
        cur = conn.execute(
            """
            INSERT INTO admin_roles (name, description, modules, is_builtin, created_at)
            VALUES (?, ?, ?, 0, ?)
            """,
            (
                body.name.strip(),
                body.description.strip(),
                modules_to_json(modules),
                now,
            ),
        )
        role_id = cur.lastrowid
        row = conn.execute(
            "SELECT * FROM admin_roles WHERE id = ?", (role_id,)
        ).fetchone()
        log_audit(
            conn,
            "create_role",
            detail=body.name.strip(),
            ip=request.client.host if request.client else "",
            actor=admin.username,
        )
    return {"ok": True, "role": _role_payload(row_to_dict(row) or {})}


@router.patch("/roles/{role_id}")
def update_role(
    role_id: int,
    body: AdminRoleUpdateRequest,
    request: Request,
    admin: AdminUser = Depends(require_module("roles")),
) -> dict:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM admin_roles WHERE id = ?", (role_id,)
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="角色不存在")
        role = row_to_dict(row) or {}
        if role.get("is_builtin") and body.name and body.name.strip() != role["name"]:
            raise HTTPException(status_code=400, detail="内置角色不可改名")
        name = body.name.strip() if body.name else role["name"]
        description = (
            body.description.strip() if body.description is not None else role.get("description") or ""
        )
        modules = (
            modules_to_json(normalize_modules(body.modules))
            if body.modules is not None
            else role.get("modules") or "[]"
        )
        if body.modules is not None and not normalize_modules(body.modules):
            raise HTTPException(status_code=400, detail="请至少选择一个模块权限")
        dup = conn.execute(
            "SELECT id FROM admin_roles WHERE name = ? AND id != ?",
            (name, role_id),
        ).fetchone()
        if dup:
            raise HTTPException(status_code=400, detail="角色名称已存在")
        conn.execute(
            """
            UPDATE admin_roles SET name = ?, description = ?, modules = ?
            WHERE id = ?
            """,
            (name, description, modules, role_id),
        )
        updated = conn.execute(
            "SELECT * FROM admin_roles WHERE id = ?", (role_id,)
        ).fetchone()
        log_audit(
            conn,
            "update_role",
            detail=name,
            ip=request.client.host if request.client else "",
            actor=admin.username,
        )
    return {"ok": True, "role": _role_payload(row_to_dict(updated) or {})}


@router.delete("/roles/{role_id}")
def delete_role(
    role_id: int,
    request: Request,
    admin: AdminUser = Depends(require_module("roles")),
) -> dict:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM admin_roles WHERE id = ?", (role_id,)
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="角色不存在")
        role = row_to_dict(row) or {}
        if role.get("is_builtin"):
            raise HTTPException(status_code=400, detail="内置角色不可删除")
        used = conn.execute(
            "SELECT COUNT(*) FROM admin_users WHERE role_id = ?", (role_id,)
        ).fetchone()[0]
        if used:
            raise HTTPException(status_code=400, detail="仍有用户使用该角色，无法删除")
        conn.execute("DELETE FROM admin_roles WHERE id = ?", (role_id,))
        log_audit(
            conn,
            "delete_role",
            detail=role.get("name") or str(role_id),
            ip=request.client.host if request.client else "",
            actor=admin.username,
        )
    return {"ok": True}


@router.get("/users")
def list_users(admin: AdminUser = Depends(require_module("users"))) -> dict:
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT u.id, u.username, u.display_name, u.role_id, u.status,
                   u.created_at, u.last_login_at, r.name AS role_name
            FROM admin_users u
            JOIN admin_roles r ON r.id = u.role_id
            WHERE u.username != ?
            ORDER BY u.id ASC
            """,
            (HIDDEN_ADMIN_USERNAME,),
        ).fetchall()
    return {"users": [_user_payload(row_to_dict(r) or {}) for r in rows]}


@router.post("/users")
def create_user(
    body: AdminUserCreateRequest,
    request: Request,
    admin: AdminUser = Depends(require_module("users")),
) -> dict:
    username = body.username.strip()
    if username == HIDDEN_ADMIN_USERNAME:
        raise HTTPException(status_code=400, detail="该用户名为系统保留，不可创建")
    now = _utc_now()
    with get_conn() as conn:
        role = conn.execute(
            "SELECT id FROM admin_roles WHERE id = ?", (body.role_id,)
        ).fetchone()
        if not role:
            raise HTTPException(status_code=400, detail="角色不存在")
        exists = conn.execute(
            "SELECT id FROM admin_users WHERE username = ?", (username,)
        ).fetchone()
        if exists:
            raise HTTPException(status_code=400, detail="用户名已存在")
        cur = conn.execute(
            """
            INSERT INTO admin_users (
                username, password_hash, display_name, role_id, status, created_at
            ) VALUES (?, ?, ?, ?, 'active', ?)
            """,
            (
                username,
                hash_admin_password(body.password),
                body.display_name.strip() or username,
                body.role_id,
                now,
            ),
        )
        user_id = cur.lastrowid
        row = conn.execute(
            """
            SELECT u.id, u.username, u.display_name, u.role_id, u.status,
                   u.created_at, u.last_login_at, r.name AS role_name
            FROM admin_users u
            JOIN admin_roles r ON r.id = u.role_id
            WHERE u.id = ?
            """,
            (user_id,),
        ).fetchone()
        log_audit(
            conn,
            "create_admin_user",
            detail=username,
            ip=request.client.host if request.client else "",
            actor=admin.username,
        )
    return {"ok": True, "user": _user_payload(row_to_dict(row) or {})}


@router.patch("/users/{user_id}")
def update_user(
    user_id: int,
    body: AdminUserUpdateRequest,
    request: Request,
    admin: AdminUser = Depends(require_module("users")),
) -> dict:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM admin_users WHERE id = ?", (user_id,)
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="用户不存在")
        user = row_to_dict(row) or {}
        if user.get("username") == HIDDEN_ADMIN_USERNAME:
            raise HTTPException(status_code=400, detail="系统内置账号不可修改")
        if body.role_id is not None:
            role = conn.execute(
                "SELECT id FROM admin_roles WHERE id = ?", (body.role_id,)
            ).fetchone()
            if not role:
                raise HTTPException(status_code=400, detail="角色不存在")
        if body.status == "disabled" and user_id == admin.user_id:
            raise HTTPException(status_code=400, detail="不能停用自己的账号")
        display_name = (
            body.display_name.strip()
            if body.display_name is not None
            else user.get("display_name") or user["username"]
        )
        role_id = body.role_id if body.role_id is not None else user["role_id"]
        status = body.status if body.status is not None else user.get("status") or "active"
        if status not in ("active", "disabled"):
            raise HTTPException(status_code=400, detail="无效的用户状态")
        conn.execute(
            """
            UPDATE admin_users
            SET display_name = ?, role_id = ?, status = ?
            WHERE id = ?
            """,
            (display_name, role_id, status, user_id),
        )
        if body.password:
            conn.execute(
                "UPDATE admin_users SET password_hash = ? WHERE id = ?",
                (hash_admin_password(body.password), user_id),
            )
        updated = conn.execute(
            """
            SELECT u.id, u.username, u.display_name, u.role_id, u.status,
                   u.created_at, u.last_login_at, r.name AS role_name
            FROM admin_users u
            JOIN admin_roles r ON r.id = u.role_id
            WHERE u.id = ?
            """,
            (user_id,),
        ).fetchone()
        log_audit(
            conn,
            "update_admin_user",
            detail=user.get("username") or str(user_id),
            ip=request.client.host if request.client else "",
            actor=admin.username,
        )
    return {"ok": True, "user": _user_payload(row_to_dict(updated) or {})}


@router.delete("/users/{user_id}")
def delete_user(
    user_id: int,
    request: Request,
    admin: AdminUser = Depends(require_module("users")),
) -> dict:
    if user_id == admin.user_id:
        raise HTTPException(status_code=400, detail="不能删除自己的账号")
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM admin_users WHERE id = ?", (user_id,)
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="用户不存在")
        user = row_to_dict(row) or {}
        if user.get("username") == HIDDEN_ADMIN_USERNAME:
            raise HTTPException(status_code=400, detail="系统内置账号不可删除")
        total = conn.execute(
            "SELECT COUNT(*) FROM admin_users WHERE username != ?",
            (HIDDEN_ADMIN_USERNAME,),
        ).fetchone()[0]
        if total <= 1:
            raise HTTPException(status_code=400, detail="至少保留一个管理员账号")
        conn.execute("DELETE FROM admin_users WHERE id = ?", (user_id,))
        log_audit(
            conn,
            "delete_admin_user",
            detail=user.get("username") or str(user_id),
            ip=request.client.host if request.client else "",
            actor=admin.username,
        )
    return {"ok": True}
