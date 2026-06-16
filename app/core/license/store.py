from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path

from app.core.paths import user_data_dir

# 所有版本（正式授权版 / 免授权版）统一使用的运营服务器地址
LICENSE_SERVER_URL = "http://8.148.30.99:8787"


def effective_server_url() -> str:
    """返回当前应使用的授权/上报服务器地址。

    生产环境固定为 LICENSE_SERVER_URL；本地开发可通过环境变量 TA_LICENSE_SERVER 覆盖。
    """
    return os.environ.get("TA_LICENSE_SERVER", LICENSE_SERVER_URL).rstrip("/")


DEFAULT_SERVER_URL = effective_server_url()


@dataclass
class LicenseState:
    device_id: str = ""
    display_name: str = ""
    contact: str = ""
    note: str = ""
    status: str = "unknown"  # pending | approved | rejected | disabled | unknown
    access_token: str = ""
    server_url: str = DEFAULT_SERVER_URL
    message: str = ""
    last_check: str = ""
    ba_account_status: str = "unknown"
    ex_account_status: str = "unknown"
    auto_trade_enabled: bool = False
    expires_at: str = ""


def license_path() -> Path:
    return user_data_dir() / "license.json"


def load_license() -> LicenseState:
    path = license_path()
    if not path.exists():
        return LicenseState(server_url=effective_server_url())
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        state = LicenseState(**{k: data.get(k, v) for k, v in asdict(LicenseState()).items()})
    except (json.JSONDecodeError, TypeError):
        state = LicenseState()
    # 始终使用统一运营服务器，忽略本地缓存的旧地址（如 127.0.0.1）
    state.server_url = effective_server_url()
    return state


def save_license(state: LicenseState) -> None:
    path = license_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    state.server_url = effective_server_url()
    path.write_text(
        json.dumps(asdict(state), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
