from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from typing import Any

import requests

from app.core.license.device_id import get_device_id
from app.core.license.store import LicenseState, load_license, save_license
from app.core.ssl_certs import ensure_ca_bundle

APP_VERSION = "1.0.0"
REQUEST_TIMEOUT = 15


class LicenseError(Exception):
    pass


class LicenseClient:
    def __init__(self, server_url: str | None = None) -> None:
        ensure_ca_bundle()
        self.state = load_license()
        if not self.state.device_id:
            self.state.device_id = get_device_id()
        if server_url:
            self.state.server_url = server_url.rstrip("/")
        elif not self.state.server_url:
            from app.core.license.store import DEFAULT_SERVER_URL

            self.state.server_url = DEFAULT_SERVER_URL

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
            res = requests.post(
                self._url("/api/v1/register"),
                json=payload,
                timeout=REQUEST_TIMEOUT,
            )
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
        )

    def heartbeat(
        self,
        *,
        ba_account: str = "",
        mt5_account: str = "",
        position_summary: str = "",
        xau_position: str = "",
        xag_position: str = "",
    ) -> LicenseState:
        payload = {
            "device_id": self.state.device_id,
            "app_version": APP_VERSION,
            "ba_account": ba_account,
            "mt5_account": mt5_account,
            "position_summary": position_summary,
            "xau_position": xau_position,
            "xag_position": xag_position,
        }
        try:
            res = requests.post(
                self._url("/api/v1/heartbeat"),
                json=payload,
                headers=self._headers(),
                timeout=REQUEST_TIMEOUT,
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
        )

    def upload_trades(self, trades: list[dict]) -> bool:
        if not trades:
            return True
        if not self.state.access_token:
            return False
        try:
            res = requests.post(
                self._url("/api/v1/trades/batch"),
                json={"trades": trades},
                headers=self._headers(),
                timeout=REQUEST_TIMEOUT,
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
            res = requests.post(
                self._url("/api/v1/trades/batch"),
                json={"trades": pending},
                headers=self._headers(),
                timeout=REQUEST_TIMEOUT,
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

    def to_dict(self) -> dict:
        return asdict(self.state)
