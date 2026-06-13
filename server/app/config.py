from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _load_env_file() -> None:
    """Load server/.env so password matches run.bat, not stale system env."""
    env_path = Path(__file__).resolve().parents[1] / ".env"
    if not env_path.is_file():
        return
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            os.environ[key] = value


_load_env_file()


def _env_bool(key: str, default: bool) -> bool:
    raw = os.environ.get(key)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Settings:
    admin_password: str
    admin_password_hash: str
    jwt_secret: str
    jwt_expire_hours: int
    db_path: str
    host: str
    port: int
    # 免授权版「暗号备注自动通过」开关：付费服务器建议设 TA_NOLICENSE_AUTO_APPROVE=0 关闭
    nolicense_auto_approve: bool
    # 是否信任反向代理传来的 X-Forwarded-For（仅在确有可信反代时开启，防止伪造限流键）
    trust_forwarded: bool
    # CSV 导出最大行数上限，防止超大导出拖垮内存
    export_max_rows: int

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            admin_password=os.environ.get("TA_ADMIN_PASSWORD", ""),
            admin_password_hash=os.environ.get("TA_ADMIN_PASSWORD_HASH", ""),
            jwt_secret=os.environ.get("TA_JWT_SECRET", ""),
            jwt_expire_hours=int(os.environ.get("TA_JWT_EXPIRE_HOURS", "24")),
            db_path=os.environ.get("TA_DB_PATH", "data/license.db"),
            host=os.environ.get("TA_HOST", "127.0.0.1"),
            port=int(os.environ.get("TA_PORT", "8787")),
            nolicense_auto_approve=_env_bool("TA_NOLICENSE_AUTO_APPROVE", True),
            trust_forwarded=_env_bool("TA_TRUST_FORWARDED", False),
            export_max_rows=int(os.environ.get("TA_EXPORT_MAX_ROWS", "100000")),
        )


def admin_token_version() -> int:
    """管理员令牌版本号：改密/退出时自增，使旧令牌立即失效。"""
    try:
        return int(os.environ.get("TA_ADMIN_TOKEN_VERSION", "0"))
    except ValueError:
        return 0


def bump_admin_token_version() -> int:
    new_version = admin_token_version() + 1
    update_env_value("TA_ADMIN_TOKEN_VERSION", str(new_version))
    return new_version


settings = Settings.from_env()

ENV_PATH = Path(__file__).resolve().parents[1] / ".env"


def update_env_value(key: str, value: str) -> None:
    lines: list[str] = []
    found = False
    if ENV_PATH.is_file():
        for raw in ENV_PATH.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                lines.append(raw)
                continue
            k, _, _ = line.partition("=")
            if k.strip() == key:
                lines.append(f'{key}="{value}"')
                found = True
            else:
                lines.append(raw)
    if not found:
        lines.append(f'{key}="{value}"')
    ENV_PATH.parent.mkdir(parents=True, exist_ok=True)
    ENV_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.environ[key] = value


def remove_env_key(key: str) -> None:
    if not ENV_PATH.is_file():
        os.environ.pop(key, None)
        return
    kept: list[str] = []
    for raw in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, _ = line.partition("=")
            if k.strip() == key:
                continue
        kept.append(raw)
    ENV_PATH.write_text("\n".join(kept) + ("\n" if kept else ""), encoding="utf-8")
    os.environ.pop(key, None)


def validate_production_settings() -> None:
    if os.environ.get("TA_ALLOW_INSECURE_SERVER") == "1":
        return
    problems: list[str] = []
    if not settings.admin_password_hash and not settings.admin_password:
        problems.append("TA_ADMIN_PASSWORD_HASH 未设置，且 TA_ADMIN_PASSWORD 仍是默认值")
    if not settings.jwt_secret or len(settings.jwt_secret) < 32:
        problems.append("TA_JWT_SECRET 未设置或长度不足 32 字符")
    if problems:
        joined = "；".join(problems)
        raise RuntimeError(f"授权服务器拒绝以不安全配置启动：{joined}")
