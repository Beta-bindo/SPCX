from __future__ import annotations

from PySide6.QtCore import QObject, QTimer, Signal

from typing import Callable

from app.core.license.client import LicenseClient, LicenseError
from app.core.trade_ledger import TradeRecord, trade_record_to_payload

HEARTBEAT_MS = 10 * 60 * 1000  # 10 分钟


class LicenseService(QObject):
    status_changed = Signal(str, str)
    revoked = Signal(str)
    auto_trade_changed = Signal(bool)  # 自动下单开通状态变化（运营后台控制）

    def __init__(self, parent=None):
        super().__init__(parent)
        self.client = LicenseClient()
        self._telemetry_provider: Callable[[], dict[str, str]] | None = None
        self._connection_mode_provider: Callable[[], str] | None = None
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._on_timer)
        self._retry_timer = QTimer(self)
        self._retry_timer.timeout.connect(self._retry_pending_uploads)

    def set_telemetry_provider(
        self, provider: Callable[[], dict[str, str]] | None
    ) -> None:
        self._telemetry_provider = provider

    def set_connection_mode_provider(self, provider: Callable[[], str] | None) -> None:
        self._connection_mode_provider = provider

    def _current_connection_mode(self) -> str:
        from app.core.models import ConnectionMode

        if self._connection_mode_provider is None:
            return ConnectionMode.DEMO.value
        try:
            return self._connection_mode_provider() or ConnectionMode.DEMO.value
        except Exception:
            return ConnectionMode.DEMO.value

    @property
    def is_approved(self) -> bool:
        return self.client.is_approved

    @property
    def state(self):
        return self.client.state

    def start_heartbeat(self, *, flush: bool = True, defer_retry_min: int = 0) -> None:
        """启动定时心跳；defer_retry_min>0 时推迟补传重试，避免启动阶段联网。"""
        if flush:
            self.flush_pending()
        self._timer.start(HEARTBEAT_MS)
        if defer_retry_min > 0:
            QTimer.singleShot(
                defer_retry_min * 60 * 1000,
                lambda: self._retry_timer.start(2 * 60 * 1000),
            )
        else:
            self._retry_timer.start(2 * 60 * 1000)

    def stop_heartbeat(self) -> None:
        self._timer.stop()
        self._retry_timer.stop()

    def flush_pending(self) -> int:
        if not self.client.can_upload_trades:
            try:
                self.refresh()
            except LicenseError:
                pass
        if not self.client.can_upload_trades:
            return 0
        return self.client.flush_pending_trades()

    def register(self, display_name: str, contact: str, note: str) -> None:
        state = self.client.register(display_name, contact, note)
        self.status_changed.emit(state.status, state.message)

    def _telemetry_payload(self) -> dict[str, str]:
        if self._telemetry_provider is None:
            return {}
        try:
            return self._telemetry_provider()
        except Exception:
            return {}

    def refresh(self) -> None:
        try:
            prev_device = self.client.state.status
            prev_ba = self.client.state.ba_account_status
            prev_ex = self.client.state.ex_account_status
            prev_auto = self.client.is_auto_trade_enabled
            state = self.client.heartbeat(**self._telemetry_payload())
        except LicenseError as exc:
            self.status_changed.emit(self.client.state.status, str(exc))
            return
        self.status_changed.emit(state.status, state.message)
        if self.client.is_auto_trade_enabled != prev_auto:
            self.auto_trade_changed.emit(self.client.is_auto_trade_enabled)
        if state.status == "approved" or self.client.can_upload_trades:
            self.flush_pending()
        if prev_device == "approved" and state.status != "approved":
            self.revoked.emit(state.message or "授权已失效")
            return
        if state.status == "approved" and self._platform_accounts_blocked():
            ba = self.client.state.ba_account_status
            ex = self.client.state.ex_account_status
            if prev_ba == "enabled" or prev_ex == "enabled":
                msg = state.message or "交易账号已停用或待审核"
                self.revoked.emit(msg)

    def _platform_accounts_blocked(self) -> bool:
        try:
            self.client.require_platform_accounts_enabled(self._current_connection_mode())
        except LicenseError:
            return True
        return False

    def _on_timer(self) -> None:
        self.refresh()

    def _retry_pending_uploads(self) -> None:
        if not self.client.can_upload_trades:
            return
        from app.core.license.pending_trades import load_pending

        if load_pending():
            self.flush_pending()

    def ensure_approved(self) -> None:
        from app.core.build_config import LICENSE_REQUIRED

        if not LICENSE_REQUIRED:
            return
        try:
            self.refresh()
        except LicenseError as exc:
            if self.client.is_approved:
                return
            raise LicenseError(str(exc)) from exc
        self.client.require_approved()

    def ensure_approved_for_trade(self, connection_mode: str | None = None) -> None:
        """交易前强制刷新授权与平台账号状态，避免后台停用后本地缓存仍可下单。"""
        from app.core.build_config import LICENSE_REQUIRED

        if not LICENSE_REQUIRED:
            return
        mode = connection_mode or self._current_connection_mode()
        try:
            self.refresh()
        except LicenseError:
            pass
        if not self.client.is_approved:
            self.ensure_approved()
        self.client.require_platform_accounts_enabled(mode)

    def sync_accounts_now(self) -> None:
        """连接参数变更后立即上报账号（不阻塞 UI 时可后台调用）。"""
        try:
            self.refresh()
        except LicenseError:
            pass

    def _reporting_fresh_enough(self, *, max_age_minutes: int = 30) -> bool:
        """本地已有令牌且近期心跳成功，启动时跳过联网避免系统代理小窗。"""
        if self.client.state.status not in ("approved", "pending"):
            return False
        if not self.client.state.access_token:
            return False
        last = self.client.state.last_check
        if not last:
            return False
        try:
            from datetime import datetime, timedelta

            checked_at = datetime.fromisoformat(last)
            return datetime.now() - checked_at < timedelta(minutes=max_age_minutes)
        except ValueError:
            return False

    def ensure_reporting_ready(self) -> None:
        """免授权版：静默注册/续期上报令牌；尽量单次心跳，无积压则不再额外请求。"""
        if self._reporting_fresh_enough():
            return
        import os

        st = self.client.state
        needs_register = st.status in ("unknown", "") or (
            st.status == "pending" and not st.access_token
        )
        if needs_register:
            name = (st.display_name or os.environ.get("COMPUTERNAME", "免授权用户"))[:32]
            try:
                self.client.register(name, "13000000000", "免授权版自动注册")
            except LicenseError:
                pass
        try:
            state = self.client.heartbeat()
            self.status_changed.emit(state.status, state.message)
        except LicenseError:
            pass
        if not self.client.can_upload_trades:
            return
        from app.core.license.pending_trades import load_pending

        if load_pending():
            try:
                self.client.flush_pending_trades()
            except Exception:
                pass

    def upload_trade(self, record: TradeRecord) -> None:
        if not self.client.can_upload_trades:
            try:
                self.ensure_reporting_ready()
            except LicenseError:
                pass
            if not self.client.can_upload_trades:
                try:
                    self.refresh()
                except LicenseError:
                    pass
        trade = trade_record_to_payload(record)
        if not self.client.can_upload_trades:
            from app.core.license.pending_trades import enqueue_trades

            enqueue_trades([trade])
            return
        if not self.client.upload_trades([trade]):
            return
        self.flush_pending()
