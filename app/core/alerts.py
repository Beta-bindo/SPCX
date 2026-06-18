"""声音告警：点差越界与各品种爆仓缓冲阈值。

AlertService 周期性评估行情与风险快照，触发文字提示（信号）并驱动持续蜂鸣，
直到条件解除或用户关闭告警。爆仓告警优先级高于点差告警。
"""

from __future__ import annotations

import time
from enum import Enum

from PySide6.QtCore import QObject, QTimer, Signal

from app.core.alert_tones import AlertTonePlayer
from app.core.models import AppConfig, RiskSnapshot, SpreadSnapshot


class AlertSoundKind(str, Enum):
    """告警声音类型：点差 / 爆仓。"""

    SPREAD = "spread"
    LIQ = "liq"


def _spread_at_warning_edge(spread: float, lo: float, hi: float) -> bool:
    """点差触及任一预警边界（≤下界 或 ≥上界）即告警。"""
    return spread <= lo or spread >= hi


def _liq_distance_alert(distance: float, threshold: float) -> bool:
    """爆仓缓冲低于阈值即告警；distance>90000 视为无有效持仓，不告警。"""
    if distance > 90000:
        return False
    return distance <= threshold


class AlertService(QObject):
    """告警服务：评估条件、按冷却节流发文字提示、维持周期蜂鸣。"""

    alert_triggered = Signal(str)

    SPREAD_INTERVAL_MS = 300   # 点差告警蜂鸣间隔
    LIQ_INTERVAL_MS = 110      # 爆仓告警蜂鸣间隔（更急促）
    COOLDOWN_SEC = 12.0        # 同一条文字提示的最短重发间隔

    def __init__(self, parent=None):
        super().__init__(parent)
        self._last_fire: dict[str, float] = {}  # 各告警键上次发文字提示的时间
        self._ringing = False
        self._active_kind: AlertSoundKind | None = None
        self._voice_active = False  # 语音播报占用中：期间静音点差（语音优先于点差，但低于爆仓）
        self._tones = AlertTonePlayer()
        self._beep_timer = QTimer(self)
        self._beep_timer.timeout.connect(self._tick_beep)

    def evaluate(
        self,
        config: AppConfig,
        spreads: dict[str, SpreadSnapshot],
        risk: RiskSnapshot,
    ) -> None:
        """评估当前点差/风险，触发或停止告警。总开关关闭时直接停。"""
        if not config.any_alert_sound_enabled():
            self.stop()
            return

        spread_active = False
        liq_active = False
        pending_messages: list[tuple[str, str, AlertSoundKind]] = []

        # 逐品种检查点差是否越界
        for preset_id, label in (("xau", "黄金"), ("xag", "SPCXUSDT")):
            if not config.spread_alerts_on(preset_id):
                continue
            snap = spreads.get(preset_id)
            if not snap:
                continue
            # 按界面字面取值：「<=」框→低于即报警，「>=」框→高于即报警（不再排序归一化）
            lo = config.spread_alert_min(preset_id)
            hi = config.spread_alert_max(preset_id)
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

        # 逐品种、逐平台检查爆仓缓冲是否低于阈值
        liq_map = [
            ("xau", "xau_ba_liq", risk.xau_ba_liq, config.xau_ba_liq_alert, "黄金 BA 爆仓缓冲"),
            ("xau", "xau_mt5_liq", risk.xau_mt5_liq, config.xau_mt5_liq_alert, "黄金 Exness 爆仓缓冲"),
            ("xag", "xag_ba_liq", risk.xag_ba_liq, config.xag_ba_liq_alert, "SPCXUSDT BA 爆仓缓冲"),
            ("xag", "xag_mt5_liq", risk.xag_mt5_liq, config.xag_mt5_liq_alert, "SPCXUSDT Exness 爆仓缓冲"),
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

        # 文字提示按冷却节流，避免刷屏；蜂鸣则持续到条件解除
        for key, message, kind in pending_messages:
            if self._should_fire(key):
                self.alert_triggered.emit(message)

        active_kind = AlertSoundKind.LIQ if liq_active else AlertSoundKind.SPREAD
        self._ensure_beep(active_kind)

    def _should_fire(self, key: str) -> bool:
        """该告警键距上次发文字提示是否已超过冷却时间。"""
        now = time.time()
        last = self._last_fire.get(key, 0.0)
        if now - last < self.COOLDOWN_SEC:
            return False
        self._last_fire[key] = now
        return True

    def _ensure_beep(self, kind: AlertSoundKind) -> None:
        """启动/维持周期蜂鸣，直到 stop()。

        kind 已由 evaluate 做过优先级裁决（两端同时告警时恒为 LIQ），这里只负责
        忠实切换到该音色：切换时停掉另一路再播新的一路，保证两种声音不会同时响、
        也不会出现“爆仓解除后仍卡在爆仓音”的串音。
        """
        kind_changed = self._active_kind != kind
        self._active_kind = kind
        interval = (
            self.LIQ_INTERVAL_MS
            if kind == AlertSoundKind.LIQ
            else self.SPREAD_INTERVAL_MS
        )
        if self._beep_timer.interval() != interval:
            self._beep_timer.setInterval(interval)
        was_ringing = self._ringing
        self._ringing = True
        if not self._beep_timer.isActive():
            self._beep_timer.start(interval)
        if not was_ringing or kind_changed:
            self._play_active_tone()

    def _start_continuous_beep(self, kind: AlertSoundKind) -> None:
        self._ensure_beep(kind)

    def _play_active_tone(self) -> None:
        if self._active_kind == AlertSoundKind.LIQ:
            self._tones.play_liq()  # 爆仓优先级最高，不受语音占用影响
        elif not self._voice_active:
            self._tones.play_spread()
        # 语音播报占用中且为点差告警：静音让位给语音（语音优先于点差）

    def is_liq_ringing(self) -> bool:
        """当前是否正在响爆仓告警（优先级高于语音播报）。"""
        return self._ringing and self._active_kind == AlertSoundKind.LIQ

    def begin_voice(self) -> None:
        """进入语音播报：立即静音正在响的点差告警；爆仓不受影响。"""
        self._voice_active = True
        if self._ringing and self._active_kind == AlertSoundKind.SPREAD:
            self._tones.stop()

    def end_voice(self) -> None:
        """语音播报结束：解除占用，点差告警将在下一拍蜂鸣中自然恢复。"""
        self._voice_active = False

    def _tick_beep(self) -> None:
        """定时器回调：仍在响铃则播放一次，否则停表。"""
        if not self._ringing:
            self._beep_timer.stop()
            return
        self._play_active_tone()

    def stop(self) -> None:
        """停止一切告警声音并复位状态。"""
        self._ringing = False
        self._beep_timer.stop()
        self._active_kind = None
        self._tones.stop()
