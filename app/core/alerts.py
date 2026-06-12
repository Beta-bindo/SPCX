"""Sound alerts: spread range and per-symbol liquidation thresholds."""

from __future__ import annotations

import time
from enum import Enum

from PySide6.QtCore import QObject, QTimer, Signal

from app.core.alert_tones import AlertTonePlayer
from app.core.models import AppConfig, RiskSnapshot, SpreadSnapshot


class AlertSoundKind(str, Enum):
    SPREAD = "spread"
    LIQ = "liq"


def _spread_at_warning_edge(spread: float, lo: float, hi: float) -> bool:
    """Alert when spread reaches either configured warning edge."""
    return spread <= lo or spread >= hi


def _liq_distance_alert(distance: float, threshold: float) -> bool:
    """Alert when buffer is at or below threshold; skip when no meaningful position."""
    if distance > 90000:
        return False
    return distance <= threshold


class AlertService(QObject):
    alert_triggered = Signal(str)

    SPREAD_INTERVAL_MS = 300
    LIQ_INTERVAL_MS = 110
    COOLDOWN_SEC = 12.0

    def __init__(self, parent=None):
        super().__init__(parent)
        self._last_fire: dict[str, float] = {}
        self._ringing = False
        self._active_kind: AlertSoundKind | None = None
        self._tones = AlertTonePlayer()
        self._beep_timer = QTimer(self)
        self._beep_timer.timeout.connect(self._tick_beep)

    def evaluate(
        self,
        config: AppConfig,
        spreads: dict[str, SpreadSnapshot],
        risk: RiskSnapshot,
    ) -> None:
        if not config.any_alert_sound_enabled():
            self.stop()
            return

        spread_active = False
        liq_active = False
        pending_messages: list[tuple[str, str, AlertSoundKind]] = []

        for preset_id, label in (("xau", "黄金"), ("xag", "白银")):
            if not config.spread_alerts_on(preset_id):
                continue
            snap = spreads.get(preset_id)
            if not snap:
                continue
            lo = min(config.spread_alert_min(preset_id), config.spread_alert_max(preset_id))
            hi = max(config.spread_alert_min(preset_id), config.spread_alert_max(preset_id))
            if _spread_at_warning_edge(snap.mid_spread, lo, hi):
                spread_active = True
                key = f"spread_{preset_id}"
                pending_messages.append(
                    (
                        key,
                        f"{label}点差 {snap.mid_spread:+.3f} 触发预警边界 ≤ {lo:.3f} 或 ≥ {hi:.3f}",
                        AlertSoundKind.SPREAD,
                    )
                )

        liq_map = [
            ("xau", "xau_ba_liq", risk.xau_ba_liq, config.xau_ba_liq_alert, "黄金 BA 爆仓缓冲"),
            ("xau", "xau_mt5_liq", risk.xau_mt5_liq, config.xau_mt5_liq_alert, "黄金 Exness 爆仓缓冲"),
            ("xag", "xag_ba_liq", risk.xag_ba_liq, config.xag_ba_liq_alert, "白银 BA 爆仓缓冲"),
            ("xag", "xag_mt5_liq", risk.xag_mt5_liq, config.xag_mt5_liq_alert, "白银 Exness 爆仓缓冲"),
        ]
        for preset_id, key, distance, threshold, name in liq_map:
            if not config.liq_alerts_on(preset_id):
                continue
            if _liq_distance_alert(distance, threshold):
                liq_active = True
                val = "∞" if distance > 90000 else f"{distance:.1f}"
                pending_messages.append(
                    (
                        key,
                        f"{name} ≤ {threshold:.1f}（当前 {val}）",
                        AlertSoundKind.LIQ,
                    )
                )

        if not spread_active and not liq_active:
            self.stop()
            return

        for key, message, kind in pending_messages:
            if self._should_fire(key):
                self.alert_triggered.emit(message)

        active_kind = AlertSoundKind.LIQ if liq_active else AlertSoundKind.SPREAD
        self._ensure_beep(active_kind)

    def _should_fire(self, key: str) -> bool:
        now = time.time()
        last = self._last_fire.get(key, 0.0)
        if now - last < self.COOLDOWN_SEC:
            return False
        self._last_fire[key] = now
        return True

    def _ensure_beep(self, kind: AlertSoundKind) -> None:
        """Keep ringing until stop() — condition cleared or user disables alert."""
        kind_changed = self._active_kind != kind
        if kind == AlertSoundKind.LIQ or self._active_kind != AlertSoundKind.LIQ:
            self._active_kind = kind
            interval = (
                self.LIQ_INTERVAL_MS
                if kind == AlertSoundKind.LIQ
                else self.SPREAD_INTERVAL_MS
            )
            self._beep_timer.setInterval(interval)
        was_ringing = self._ringing
        self._ringing = True
        if not self._beep_timer.isActive():
            self._beep_timer.start(self._beep_timer.interval())
        if not was_ringing or kind_changed:
            self._play_active_tone()

    def _start_continuous_beep(self, kind: AlertSoundKind) -> None:
        self._ensure_beep(kind)

    def _play_active_tone(self) -> None:
        if self._active_kind == AlertSoundKind.LIQ:
            self._tones.play_liq()
        else:
            self._tones.play_spread()

    def _tick_beep(self) -> None:
        if not self._ringing:
            self._beep_timer.stop()
            return
        self._play_active_tone()

    def stop(self) -> None:
        self._ringing = False
        self._beep_timer.stop()
        self._active_kind = None
        self._tones.stop()
