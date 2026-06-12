"""End-to-end license server smoke test."""
from __future__ import annotations

import uuid
import os

import requests

BASE = "http://127.0.0.1:8787"
DEVICE = f"test-{uuid.uuid4().hex[:24]}"


def main() -> None:
    assert requests.get(f"{BASE}/health", timeout=5).json()["ok"]

    reg = requests.post(
        f"{BASE}/api/v1/register",
        json={
            "device_id": DEVICE,
            "display_name": "测试用户",
            "contact": "wx-test",
            "note": "smoke",
            "app_version": "1.0.0",
        },
        timeout=5,
    )
    reg.raise_for_status()
    assert reg.json()["status"] == "pending"

    login = requests.post(
        f"{BASE}/api/v1/admin/login",
        json={"password": os.environ["TA_ADMIN_TEST_PASSWORD"]},
        timeout=5,
    )
    login.raise_for_status()
    token = login.json()["access_token"]

    approve = requests.post(
        f"{BASE}/api/v1/admin/devices/{DEVICE}/approve",
        headers={"Authorization": f"Bearer {token}"},
        timeout=5,
    )
    approve.raise_for_status()

    hb = requests.post(
        f"{BASE}/api/v1/heartbeat",
        json={"device_id": DEVICE, "app_version": "1.0.0"},
        timeout=5,
    )
    hb.raise_for_status()
    data = hb.json()
    assert data["status"] == "approved"
    assert data["access_token"]

    trades = requests.post(
        f"{BASE}/api/v1/trades/batch",
        json={
            "trades": [
                {
                    "settled_at": "2026-06-09T22:00:00",
                    "preset_id": "xau",
                    "mode": "contraction",
                    "ba_pnl": 10,
                    "mt5_pnl": 8,
                    "ba_fee": 0.5,
                    "mt5_fee": 0.2,
                    "net_pnl": 17.3,
                }
            ]
        },
        headers={"Authorization": f"Bearer {data['access_token']}"},
        timeout=5,
    )
    trades.raise_for_status()
    assert trades.json()["inserted"] == 1
    print("LICENSE SERVER OK")


if __name__ == "__main__":
    main()
