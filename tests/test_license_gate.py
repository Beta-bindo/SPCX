"""License gate must not trust local cache without server approval."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from app.core.license.store import LicenseState, save_license
from app.core.license.service import LicenseService
from app.widgets.license_gate import _verify_with_server, ensure_license_approved


def test_verify_with_server_rejects_stale_local_approval(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "app.core.license.store.license_path",
        lambda: tmp_path / "license.json",
    )
    save_license(
        LicenseState(
            device_id="dev-stale",
            status="approved",
            access_token="stale-token",
            server_url="http://127.0.0.1:8787",
        )
    )
    service = LicenseService()

    with patch.object(
        LicenseService,
        "refresh",
        side_effect=__import__(
            "app.core.license.client", fromlist=["LicenseError"]
        ).LicenseError("授权校验失败"),
    ):
        assert _verify_with_server(service) is False

    print("  ✓ 本地已通过但服务器不可达时不放行")


def test_verify_with_server_requires_approved_status(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "app.core.license.store.license_path",
        lambda: tmp_path / "license.json",
    )
    save_license(
        LicenseState(
            device_id="dev-pending",
            status="pending",
            access_token="",
            server_url="http://127.0.0.1:8787",
        )
    )
    service = LicenseService()

    def _refresh_pending() -> None:
        service.client.state.status = "pending"
        service.client.state.access_token = ""

    with patch.object(LicenseService, "refresh", side_effect=_refresh_pending):
        assert _verify_with_server(service) is False

    print("  ✓ 待审核状态不放行")


def test_verify_with_server_passes_approved_without_platform_accounts(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "app.core.license.store.license_path",
        lambda: tmp_path / "license.json",
    )
    save_license(
        LicenseState(
            device_id="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            status="approved",
            access_token="token",
            ba_account_status="pending",
            ex_account_status="pending",
            server_url="http://127.0.0.1:8787",
        )
    )
    service = LicenseService()

    def _refresh_ok() -> None:
        service.client.state.status = "approved"
        service.client.state.access_token = "token"

    with patch.object(LicenseService, "refresh", side_effect=_refresh_ok):
        assert _verify_with_server(service) is True

    print("  ✓ 设备已通过审核即可进入，未配置 BA/EX 账号时不拦门禁")


def test_ensure_license_approved_returns_none_when_user_exits(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication(sys.argv)
    monkeypatch.setattr("app.core.build_config.LICENSE_REQUIRED", True)
    monkeypatch.setattr(
        "app.core.license.store.license_path",
        lambda: tmp_path / "license.json",
    )
    save_license(
        LicenseState(
            device_id="dev-local",
            status="approved",
            access_token="local-only",
            server_url="http://127.0.0.1:8787",
        )
    )

    class _FakeDialog:
        quit_requested = True

        def __init__(self, service, parent=None):
            self.service = service

        def exec(self):
            return 0

    with patch(
        "app.widgets.license_gate._verify_with_server",
        return_value=False,
    ), patch(
        "app.widgets.license_gate.LicenseGateDialog",
        _FakeDialog,
    ):
        result = ensure_license_approved()
    assert result is None
    print("  ✓ 未通过服务器校验且用户退出时不进入主界面")


def test_ensure_license_approved_skips_gate_when_nolicense(tmp_path, monkeypatch):
    monkeypatch.setattr("app.core.build_config.LICENSE_REQUIRED", False)
    monkeypatch.setattr(
        "app.core.license.store.license_path",
        lambda: tmp_path / "license.json",
    )
    save_license(
        LicenseState(
            device_id="dev-nolicense",
            status="pending",
            access_token="",
            server_url="http://127.0.0.1:8787",
        )
    )

    result = ensure_license_approved()
    assert result is not None
    assert result.client.state.status == "approved"
    assert result.client.state.access_token
    print("  ✓ 无授权版跳过门禁直接进入")


def test_format_license_expires_label():
    from app.core.license.format import format_license_expires_label

    assert format_license_expires_label("") == "授权到：永久"
    assert format_license_expires_label(None) == "授权到：永久"
    text = format_license_expires_label("2026-12-31T16:00:00+00:00")
    assert text.startswith("授权到 2027-01-01")
    assert "：" in text
    print("  ✓ 授权到期时间格式化")
