from __future__ import annotations

from PySide6.QtCore import QObject, QTimer, Signal

from typing import Callable

from app.core.license.client import LicenseClient, LicenseError
from app.core.trade_ledger import TradeRecord, trade_record_to_payload

HEARTBEAT_MS = 10 * 60 * 1000  # 10 分钟


class LicenseService(QObject):
    status_changed = Signal(str, str)
    revoked = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.client = LicenseClient()
        self._telemetry_provider: Callable[[], dict[str, str]] | None = None
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._on_timer)
        self._retry_timer = QTimer(self)
        self._retry_timer.timeout.connect(self._retry_pending_uploads)

    def set_telemetry_provider(
        self, provider: Callable[[], dict[str, str]] | None
    ) -> None:
        self._telemetry_provider = provider

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
            state = self.client.heartbeat(**self._telemetry_payload())
        except LicenseError as exc:
            self.status_changed.emit(self.client.state.status, str(exc))
            return
        prev = self.client.state.status
        self.status_changed.emit(state.status, state.message)
        if state.status == "approved" or self.client.can_upload_trades:
            self.flush_pending()
        if prev == "approved" and state.status != "approved":
            self.revoked.emit(state.message or "授权已失效")

    def _on_timer(self) -> None:
        self.refresh()

    def _retry_pending_uploads(self) -> None:
        if not self.client.can_upload_trades:
            return
        from app.core.license.pending_trades import load_pending

        if load_pending():
            self.flush_pending()

    def ensure_approved(self) -> None:
        import os
        import sys

        if os.environ.get("TA_LICENSE_SKIP") == "1" and not getattr(sys, "frozen", False):
            return
        try:
            self.refresh()
        except LicenseError as exc:
            if self.client.is_approved:
                return
            raise LicenseError(str(exc)) from exc
        self.client.require_approved()

    def ensure_approved_for_trade(self) -> None:
        """交易热路径：本地已授权则跳过网络心跳，避免点击下单卡顿。"""
        import os
        import sys

        if os.environ.get("TA_LICENSE_SKIP") == "1" and not getattr(sys, "frozen", False):
            return
        if self.client.is_approved:
            return
        self.ensure_approved()

    def _reporting_fresh_enough(self, *, max_age_minutes: int = 30) -> bool:
        """本地已有令牌且近期心跳成功，启动时跳过联网避免系统代理小窗。"""
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
