from __future__ import annotations

import re
from dataclasses import asdict
from datetime import datetime
from typing import Any

import requests

from app.core.license.device_id import get_device_id
from app.core.license.store import LicenseState, effective_server_url, load_license, save_license
from app.core.ssl_certs import ensure_ca_bundle

APP_VERSION = "1.0.0"
REQUEST_TIMEOUT = 15


class LicenseError(Exception):
    pass


class LicenseClient:
    def __init__(self, server_url: str | None = None) -> None:
        ensure_ca_bundle()
        self.state = load_license()
        real_id = get_device_id()
        stored = (self.state.device_id or "").strip().lower()
        if re.fullmatch(r"[0-9a-f]{32}", stored):
            self.state.device_id = stored
        else:
            if self.state.device_id:
                # 丢弃测试/手工写入的假机器码及失效令牌，避免心跳 404
                self.state.access_token = ""
                self.state.status = "unknown"
                self.state.message = ""
            self.state.device_id = real_id
        # 所有版本（正式授权 / 免授权上报）统一指向运营服务器
        self.state.server_url = (
            server_url.rstrip("/") if server_url else effective_server_url()
        )
        self._session = requests.Session()
        # 不走 Windows 系统代理，避免启动/上报时弹出代理认证小窗
        self._session.trust_env = False
        self._session.proxies = {"http": None, "https": None}
        self._session.verify = ensure_ca_bundle()

    def _post(self, path: str, *, json: dict, headers: dict | None = None) -> requests.Response:
        return self._session.post(
            self._url(path),
            json=json,
            headers=headers or self._headers(),
            timeout=REQUEST_TIMEOUT,
        )

    @property
    def is_approved(self) -> bool:
        return self.state.status == "approved" and bool(self.state.access_token)

    @property
    def reporting_only(self) -> bool:
        try:
            from app.core.build_config import LICENSE_REQUIRED
        except ImportError:
            return False
        return not LICENSE_REQUIRED

    @property
    def is_auto_trade_enabled(self) -> bool:
        """自动下单是否由运营后台开通。无授权版默认放开，正式版以服务端为准。"""
        if self.reporting_only:
            return True
        return bool(self.state.auto_trade_enabled)

    @property
    def is_ba_account_enabled(self) -> bool:
        return self.state.ba_account_status == "enabled"

    @property
    def is_ex_account_enabled(self) -> bool:
        return self.state.ex_account_status == "enabled"

    @property
    def can_upload_trades(self) -> bool:
        if not self.state.access_token:
            return False
        if self.state.status in ("disabled", "rejected"):
            return False
        if self.reporting_only:
            return self.state.status in ("approved", "pending")
        return self.is_approved

    def _url(self, path: str) -> str:
        return f"{self.state.server_url}{path}"

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.state.access_token:
            headers["Authorization"] = f"Bearer {self.state.access_token}"
        return headers

    def _save_check(self, **kwargs: Any) -> LicenseState:
        self.state.last_check = datetime.now().isoformat(timespec="seconds")
        for key, val in kwargs.items():
            if hasattr(self.state, key):
                setattr(self.state, key, val)
        save_license(self.state)
        return self.state

    def _raise_http_error(self, exc: requests.RequestException, prefix: str) -> LicenseError:
        if isinstance(exc, requests.HTTPError) and exc.response is not None:
            try:
                body = exc.response.json()
                detail = body.get("detail")
                if isinstance(detail, list):
                    parts = []
                    for item in detail:
                        loc = ".".join(str(x) for x in item.get("loc", ()))
                        msg = item.get("msg", "")
                        parts.append(f"{loc}: {msg}" if loc else msg)
                    detail = "; ".join(parts)
                if detail:
                    return LicenseError(f"{prefix}：{detail}")
            except (ValueError, AttributeError):
                pass
        return LicenseError(f"{prefix}：{exc}")

    def register(self, display_name: str, contact: str, note: str) -> LicenseState:
        payload = {
            "device_id": self.state.device_id,
            "display_name": display_name.strip(),
            "contact": contact.strip(),
            "note": note.strip(),
            "app_version": APP_VERSION,
        }
        try:
            res = self._post("/api/v1/register", json=payload, headers={"Content-Type": "application/json"})
            res.raise_for_status()
        except requests.RequestException as exc:
            raise self._raise_http_error(exc, "无法连接授权服务器") from exc
        data = res.json()
        return self._save_check(
            display_name=display_name.strip(),
            contact=contact.strip(),
            note=note.strip(),
            status=data.get("status", "pending"),
            message=data.get("message", ""),
            access_token=data.get("access_token") or "",
            expires_at=data.get("expires_at") or "",
        )

    def heartbeat(
        self,
        *,
        ba_account: str = "",
        mt5_account: str = "",
        position_summary: str = "",
        xau_position: str = "",
        xag_position: str = "",
        spcx_position: str = "",
        open_orders_summary: str = "",
        xau_open_orders: str = "",
        xag_open_orders: str = "",
        spcx_open_orders: str = "",
    ) -> LicenseState:
        spcx_position = spcx_position or xag_position
        spcx_open_orders = spcx_open_orders or xag_open_orders
        payload = {
            "device_id": self.state.device_id,
            "app_version": APP_VERSION,
            "ba_account": ba_account,
            "mt5_account": mt5_account,
            "position_summary": position_summary,
            "xau_position": xau_position,
            "spcx_position": spcx_position,
            "xag_position": xag_position,
            "open_orders_summary": open_orders_summary,
            "xau_open_orders": xau_open_orders,
            "spcx_open_orders": spcx_open_orders,
            "xag_open_orders": xag_open_orders,
        }
        try:
            res = self._post(
                "/api/v1/heartbeat",
                json=payload,
                headers={**self._headers()},
            )
            if res.status_code == 404:
                return self._save_check(status="unknown", message="设备未注册，请重新申请")
            res.raise_for_status()
        except requests.RequestException as exc:
            raise self._raise_http_error(exc, "授权校验失败") from exc
        data = res.json()
        status = data.get("status", self.state.status)
        token = data.get("access_token") or self.state.access_token
        if status == "approved":
            saved_token = token
        elif self.reporting_only and status == "pending" and token:
            saved_token = token
        else:
            saved_token = ""
        return self._save_check(
            status=status,
            message=data.get("message", ""),
            access_token=saved_token,
            ba_account_status=data.get("ba_account_status", self.state.ba_account_status),
            ex_account_status=data.get("ex_account_status", self.state.ex_account_status),
            auto_trade_enabled=bool(data.get("auto_trade_enabled", self.state.auto_trade_enabled)),
            expires_at=data.get("expires_at") or self.state.expires_at or "",
        )

    def upload_trades(self, trades: list[dict]) -> bool:
        if not trades:
            return True
        if not self.state.access_token:
            return False
        try:
            res = self._post(
                "/api/v1/trades/batch",
                json={"trades": trades},
            )
            res.raise_for_status()
            return True
        except requests.RequestException:
            from app.core.license.pending_trades import enqueue_trades

            enqueue_trades(trades)
            return False

    def flush_pending_trades(self) -> int:
        from app.core.license.pending_trades import load_pending, remove_trades

        pending = load_pending()
        if not pending or not self.state.access_token:
            return 0
        try:
            res = self._post(
                "/api/v1/trades/batch",
                json={"trades": pending},
            )
            res.raise_for_status()
        except requests.RequestException:
            return 0
        uploaded = len(pending)
        remove_trades(pending)
        return uploaded

    def require_approved(self) -> None:
        if not self.is_approved:
            raise LicenseError(self.state.message or "未授权，无法使用此功能")

    def require_platform_accounts_enabled(
        self, connection_mode: str | None = None
    ) -> None:
        """按连接模式校验运营后台已开通的平台账号。

        - 演示模式：不校验
        - 实盘双端：BA + EX 均需 enabled
        - 仅 BA / 仅 MT5：只校验对应一端
        """
        from app.core.models import ConnectionMode

        mode = connection_mode or ConnectionMode.DEMO.value
        if mode == ConnectionMode.DEMO.value:
            return
        if mode in (ConnectionMode.LIVE_BOTH.value, ConnectionMode.LIVE_BA.value):
            self._require_account_enabled(self.state.ba_account_status, "BA")
        if mode in (ConnectionMode.LIVE_BOTH.value, ConnectionMode.LIVE_MT5.value):
            self._require_account_enabled(self.state.ex_account_status, "EX")

    def _require_account_enabled(self, status: str, label: str) -> None:
        if status == "enabled":
            return
        if status == "disabled":
            raise LicenseError(f"{label} 账号已停用，请联系管理员")
        if status == "pending":
            raise LicenseError(f"{label} 账号待审核，请联系管理员启用后再交易")
        raise LicenseError(f"{label} 账号未开通，请联系管理员启用后再交易")

    def to_dict(self) -> dict:
        return asdict(self.state)
