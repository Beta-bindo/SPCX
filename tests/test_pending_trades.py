"""Tests for offline trade upload queue."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

TEST_DEVICE_ID = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"

from app.core.license.pending_trades import (
    clear_pending,
    enqueue_trades,
    load_pending,
    remove_trades,
)


@pytest.fixture(autouse=True)
def _clean_pending(tmp_path, monkeypatch):
    path = tmp_path / "pending_trades.json"
    monkeypatch.setattr("app.core.license.pending_trades._path", lambda: path)
    clear_pending()
    yield
    clear_pending()


def test_enqueue_dedupes_by_settlement_key():
    trade = {
        "settled_at": "2026-06-10T12:00:00",
        "preset_id": "xau",
        "mode": "contraction",
        "ba_pnl": 1.0,
        "mt5_pnl": 2.0,
        "ba_fee": 0.1,
        "mt5_fee": 0.2,
        "net_pnl": 2.7,
    }
    enqueue_trades([trade, trade])
    assert len(load_pending()) == 1


def test_remove_trades():
    trade = {
        "settled_at": "2026-06-10T12:00:00",
        "preset_id": "xau",
        "mode": "contraction",
        "ba_pnl": 1.0,
        "mt5_pnl": 2.0,
        "ba_fee": 0.1,
        "mt5_fee": 0.2,
        "net_pnl": 2.7,
    }
    enqueue_trades([trade])
    remove_trades([trade])
    assert load_pending() == []


def test_upload_works_with_pending_token(tmp_path, monkeypatch):
    from app.core.license.client import LicenseClient
    from app.core.license.store import LicenseState, save_license

    license_file = tmp_path / "license.json"
    pending_path = tmp_path / "pending_trades.json"
    monkeypatch.setattr("app.core.license.store.license_path", lambda: license_file)
    monkeypatch.setattr("app.core.license.pending_trades._path", lambda: pending_path)

    state = LicenseState(
        device_id=TEST_DEVICE_ID,
        status="pending",
        access_token="pending-token",
        server_url="http://127.0.0.1:8787",
    )
    save_license(state)

    with patch("app.core.license.client.get_device_id", return_value=TEST_DEVICE_ID):
        client = LicenseClient()
    trade = {
        "settled_at": "2026-06-10T12:00:00",
        "preset_id": "xau",
        "mode": "contraction",
        "ba_pnl": 1.0,
        "mt5_pnl": 2.0,
        "ba_fee": 0.1,
        "mt5_fee": 0.2,
        "net_pnl": 2.7,
    }

    class _Resp:
        def raise_for_status(self):
            return None

    with patch.object(client._session, "post", return_value=_Resp()):
        ok = client.upload_trades([trade])

    assert ok is True
    assert load_pending() == []


def test_upload_failure_enqueues(tmp_path, monkeypatch):
    from app.core.license.client import LicenseClient
    from app.core.license.store import LicenseState, save_license

    license_file = tmp_path / "license.json"
    pending_path = tmp_path / "pending_trades.json"
    monkeypatch.setattr("app.core.license.store.license_path", lambda: license_file)
    monkeypatch.setattr("app.core.license.pending_trades._path", lambda: pending_path)

    state = LicenseState(
        device_id=TEST_DEVICE_ID,
        status="approved",
        access_token="token",
        server_url="http://127.0.0.1:8787",
    )
    save_license(state)

    with patch("app.core.license.client.get_device_id", return_value=TEST_DEVICE_ID):
        client = LicenseClient()
    trade = {
        "settled_at": "2026-06-10T12:00:00",
        "preset_id": "xau",
        "mode": "contraction",
        "ba_pnl": 1.0,
        "mt5_pnl": 2.0,
        "ba_fee": 0.1,
        "mt5_fee": 0.2,
        "net_pnl": 2.7,
    }

    import requests

    with patch.object(
        client._session,
        "post",
        side_effect=requests.ConnectionError("offline"),
    ):
        ok = client.upload_trades([trade])

    assert ok is False
    saved = json.loads(pending_path.read_text(encoding="utf-8"))
    assert len(saved) == 1
