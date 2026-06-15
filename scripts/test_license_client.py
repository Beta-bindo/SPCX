"""Simulate client register -> admin approve -> heartbeat (no GUI)."""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import requests

from app.core.license.client import LicenseClient
from app.core.license import store


def main() -> None:
    base = os.environ.get("TA_LICENSE_SERVER", "http://8.148.30.99:8787").rstrip("/")
    assert requests.get(f"{base}/health", timeout=5).json()["ok"]

    with tempfile.TemporaryDirectory() as tmp:
        fake_home = Path(tmp)
        store.user_data_dir = lambda: fake_home  # type: ignore[method-assign]

        client = LicenseClient(server_url=base)
        state = client.register("联调用户", "wx-test", "auto test")
        assert state.status == "pending", state.status
        print("register ok:", state.message)

        login = requests.post(
            f"{base}/api/v1/admin/login",
            json={"password": os.environ["TA_ADMIN_TEST_PASSWORD"]},
            timeout=5,
        )
        login.raise_for_status()
        admin_token = login.json()["access_token"]

        approve = requests.post(
            f"{base}/api/v1/admin/devices/{client.state.device_id}/approve",
            headers={"Authorization": f"Bearer {admin_token}"},
            timeout=5,
        )
        approve.raise_for_status()
        print("admin approve ok")

        state = client.heartbeat()
        assert state.status == "approved", state.status
        assert state.access_token
        print("heartbeat ok:", state.message)
        print("LICENSE CLIENT E2E OK")


if __name__ == "__main__":
    main()
