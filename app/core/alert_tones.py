"""Programmatic alert tones: spread (soft) vs liquidation (sharp)."""

from __future__ import annotations

import io
import math
import struct
import wave
from pathlib import Path

from PySide6.QtCore import QUrl
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
    tremolo_hz: float = 0.0,
    tremolo_depth: float = 0.0,
) -> bytes:
    """生成单声道 16bit WAV。

    sharp=True 为短促衰减“叮”声；sharp=False 为无缝循环的持续音。
    tremolo_hz>0 时叠加振幅颤音（保持连贯无空隙，仅做强弱起伏，听感急促但不断续）。
    为保证无限循环时首尾无缝，颤音相位与首尾包络都在零点收敛。
    """
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
            if tremolo_hz > 0.0 and tremolo_depth > 0.0:
                # (1-cos) 形颤音：首尾都落在波谷，循环衔接处无突变
                trem = 1.0 - tremolo_depth * (
                    0.5 - 0.5 * math.cos(2 * math.pi * tremolo_hz * t)
                )
                envelope *= trem
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
    """播放点差/爆仓告警音。

    双通道发声以最大化可闻性：
    1) QMediaPlayer + QAudioOutput 循环播放自定义音色（走系统“媒体”音量）；
    2) 同时叠加 QApplication.beep() 系统提示音（走系统“提示音”音量、NSBeep/MessageBeep，
       绕开整个多媒体栈）。只要两条音量通道任一开着，就能听到告警。
    """

    def __init__(self) -> None:
        self._spread = None  # QMediaPlayer
        self._liq = None     # QMediaPlayer
        self._spread_out = None  # QAudioOutput
        self._liq_out = None     # QAudioOutput

    def _ensure(self) -> None:
        if self._spread is not None and self._liq is not None:
            return
        # 延迟到首次告警才加载 QtMultimedia（FFmpeg 后端），避免启动期就拉起音频后端
        from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer

        spread_path = _tone_cache_path(
            "alert_spread_loud",
            _generate_tone_wav(_SPREAD_HZ, 600, volume=0.6, sharp=False),
        )
        # 爆仓：连贯不断续的持续音 + 急促颤音（12.5Hz×0.48s=6 个整周期，循环无缝），
        # 音量调到接近满幅，听感比点差更响更紧迫。
        liq_path = _tone_cache_path(
            "alert_liq_loud_v2",
            _generate_tone_wav(
                _LIQ_HZ,
                480,
                volume=0.85,
                sharp=False,
                tremolo_hz=12.5,
                tremolo_depth=0.6,
            ),
        )
        self._spread_out = QAudioOutput()
        self._liq_out = QAudioOutput()
        self._spread_out.setVolume(1.0)
        self._liq_out.setVolume(1.0)
        self._spread = QMediaPlayer()
        self._liq = QMediaPlayer()
        self._spread.setAudioOutput(self._spread_out)
        self._liq.setAudioOutput(self._liq_out)
        self._spread.setSource(QUrl.fromLocalFile(str(spread_path)))
        self._liq.setSource(QUrl.fromLocalFile(str(liq_path)))
        self._spread.setLoops(QMediaPlayer.Loops.Infinite)
        self._liq.setLoops(QMediaPlayer.Loops.Infinite)

    @staticmethod
    def _is_playing(player) -> bool:
        from PySide6.QtMultimedia import QMediaPlayer

        return player.playbackState() == QMediaPlayer.PlaybackState.PlayingState

    def play_spread(self) -> None:
        self._ensure()
        if self._is_playing(self._liq):
            self._liq.stop()
        if not self._is_playing(self._spread):
            self._spread.play()
        # 叠加系统提示音兜底，保证可闻
        self._system_beep()

    def play_liq(self) -> None:
        self._ensure()
        if self._is_playing(self._spread):
            self._spread.stop()
        if not self._is_playing(self._liq):
            self._liq.play()
        # 爆仓更紧迫：双响系统提示音
        self._system_beep(double=True)

    def stop(self) -> None:
        if self._spread is not None:
            self._spread.stop()
        if self._liq is not None:
            self._liq.stop()

    @staticmethod
    def _system_beep(*, double: bool = False) -> None:
        app = QApplication.instance()
        if app is None:
            return
        app.beep()
        if double:
            app.beep()
