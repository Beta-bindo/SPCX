"""Programmatic alert tones: spread (soft) vs liquidation (sharp)."""

from __future__ import annotations

import io
import math
import struct
import wave
from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtMultimedia import QSoundEffect
from PySide6.QtWidgets import QApplication

from app.core.paths import user_data_dir

_SPREAD_HZ = 880.0
_LIQ_HZ = 2800.0


def _generate_tone_wav(
    frequency: float,
    duration_ms: int,
    *,
    volume: float = 0.5,
    sharp: bool = False,
) -> bytes:
    sample_rate = 44100
    sample_count = max(1, int(sample_rate * duration_ms / 1000))
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        frames = bytearray()
        for i in range(sample_count):
            t = i / sample_rate
            remaining = (sample_count - 1 - i) / sample_rate
            if sharp:
                envelope = min(1.0, t * 120.0) * math.exp(-t * 32.0)
                wave_val = math.sin(2 * math.pi * frequency * t)
                wave_val += 0.4 * math.sin(2 * math.pi * frequency * 2 * t)
                wave_val += 0.2 * math.sin(2 * math.pi * frequency * 3 * t)
            else:
                # 起音/收音各约 12ms，中间保持满音量，循环播放时听感连续不“滴答”
                attack = min(1.0, t * 80.0)
                release = min(1.0, remaining * 80.0)
                envelope = min(attack, release)
                wave_val = math.sin(2 * math.pi * frequency * t)
                wave_val += 0.3 * math.sin(2 * math.pi * frequency * 2 * t)
            sample = int(max(-32767, min(32767, volume * 32767 * wave_val * envelope)))
            frames.extend(struct.pack("<h", sample))
        wf.writeframes(bytes(frames))
    return buf.getvalue()


def _tone_cache_path(name: str, payload: bytes) -> Path:
    cache_dir = user_data_dir() / "tones"
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / f"{name}.wav"
    if not path.exists() or path.read_bytes() != payload:
        path.write_bytes(payload)
    return path


class AlertTonePlayer:
    """Play spread / liquidation tones via cached WAV files (lazy init)."""

    def __init__(self) -> None:
        self._spread: QSoundEffect | None = None
        self._liq: QSoundEffect | None = None

    def _ensure(self) -> None:
        if self._spread is not None and self._liq is not None:
            return
        spread_path = _tone_cache_path(
            "alert_spread_loud",
            _generate_tone_wav(_SPREAD_HZ, 600, volume=0.7, sharp=False),
        )
        liq_path = _tone_cache_path(
            "alert_liq_loud",
            _generate_tone_wav(_LIQ_HZ, 130, volume=0.7, sharp=True),
        )
        self._spread = QSoundEffect()
        self._liq = QSoundEffect()
        self._spread.setSource(QUrl.fromLocalFile(str(spread_path)))
        self._liq.setSource(QUrl.fromLocalFile(str(liq_path)))
        self._spread.setLoopCount(QSoundEffect.Loop.Infinite.value)
        self._liq.setLoopCount(QSoundEffect.Loop.Infinite.value)
        self._spread.setVolume(1.0)
        self._liq.setVolume(1.0)

    def play_spread(self) -> None:
        self._ensure()
        assert self._spread is not None and self._liq is not None
        if self._spread.status() == QSoundEffect.Status.Error:
            self._fallback_beep()
            return
        if self._liq.isPlaying():
            self._liq.stop()
        if not self._spread.isPlaying():
            self._spread.play()

    def play_liq(self) -> None:
        self._ensure()
        assert self._spread is not None and self._liq is not None
        if self._liq.status() == QSoundEffect.Status.Error:
            self._fallback_beep(double=True)
            return
        if self._spread.isPlaying():
            self._spread.stop()
        if not self._liq.isPlaying():
            self._liq.play()

    def stop(self) -> None:
        if self._spread is not None:
            self._spread.stop()
        if self._liq is not None:
            self._liq.stop()

    @staticmethod
    def _fallback_beep(*, double: bool = False) -> None:
        app = QApplication.instance()
        if app is None:
            return
        app.beep()
        if double:
            app.beep()
