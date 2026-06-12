from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path

from app.core.paths import user_data_dir

DEFAULT_SERVER_URL = os.environ.get(
    "TA_LICENSE_SERVER", "http://127.0.0.1:8787"
).rstrip("/")


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


def license_path() -> Path:
    return user_data_dir() / "license.json"


def load_license() -> LicenseState:
    path = license_path()
    if not path.exists():
        state = LicenseState()
        return state
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return LicenseState(**{k: data.get(k, v) for k, v in asdict(LicenseState()).items()})
    except (json.JSONDecodeError, TypeError):
        return LicenseState()


def save_license(state: LicenseState) -> None:
    path = license_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(asdict(state), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
