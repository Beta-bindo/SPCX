from __future__ import annotations

import base64
import hashlib
import hmac
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

from jose import JWTError, jwt

from app.config import settings

ALGORITHM = "HS256"
PBKDF2_ITERATIONS = 260_000


def hash_admin_password(password: str, *, salt: bytes | None = None) -> str:
    salt = salt or os.urandom(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS
    )
    return "pbkdf2_sha256${}${}${}".format(
        PBKDF2_ITERATIONS,
        base64.b64encode(salt).decode("ascii"),
        base64.b64encode(digest).decode("ascii"),
    )


def _verify_password_hash(password: str, stored: str) -> bool:
    try:
        algo, iterations, salt_b64, digest_b64 = stored.split("$", 3)
        if algo != "pbkdf2_sha256":
            return False
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(digest_b64)
        actual = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), salt, int(iterations)
        )
        return hmac.compare_digest(actual, expected)
    except Exception:
        return False


def verify_admin_password(password: str) -> bool:
    candidate = password.strip()
    stored_hash = os.environ.get("TA_ADMIN_PASSWORD_HASH") or settings.admin_password_hash
    if stored_hash:
        return _verify_password_hash(candidate, stored_hash)
    plain = os.environ.get("TA_ADMIN_PASSWORD") or settings.admin_password
    return hmac.compare_digest(candidate, plain)


def change_admin_password(old_password: str, new_password: str) -> None:
    if not verify_admin_password(old_password):
        raise ValueError("当前密码错误")
    new_password = new_password.strip()
    if len(new_password) < 12:
        raise ValueError("新密码至少 12 位")
    from app.config import remove_env_key, update_env_value

    new_hash = hash_admin_password(new_password)
    update_env_value("TA_ADMIN_PASSWORD_HASH", new_hash)
    remove_env_key("TA_ADMIN_PASSWORD")


def create_device_token(device_id: str, status: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(hours=settings.jwt_expire_hours)
    payload = {
        "sub": device_id,
        "status": status,
        "exp": expire,
        "typ": "device",
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=ALGORITHM)


def decode_device_token(token: str) -> Optional[dict]:
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[ALGORITHM])
        if payload.get("typ") != "device":
            return None
        return payload
    except JWTError:
        return None


def create_admin_token() -> str:
    expire = datetime.now(timezone.utc) + timedelta(hours=8)
    payload = {"sub": "admin", "exp": expire, "typ": "admin"}
    return jwt.encode(payload, settings.jwt_secret, algorithm=ALGORITHM)


def decode_admin_token(token: str) -> bool:
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[ALGORITHM])
        return payload.get("typ") == "admin" and payload.get("sub") == "admin"
    except JWTError:
        return False
