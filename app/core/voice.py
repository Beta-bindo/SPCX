"""语音播报：基于 Qt Text-To-Speech，跨平台（macOS darwin / Windows SAPI）。

无可用语音引擎时静默降级，不影响主流程。需在 QApplication 创建后实例化。
"""

from __future__ import annotations

from typing import Callable


class VoiceAnnouncer:
    """简单的中文语音播报器；调用 say() 朗读一句话（异步、不阻塞 UI）。"""

    def __init__(self) -> None:
        self._tts = None
        self._on_done: Callable[[], None] | None = None
        self._armed = False
        try:
            from PySide6.QtCore import QLocale
            from PySide6.QtTextToSpeech import QTextToSpeech

            usable = [e for e in QTextToSpeech.availableEngines() if e != "mock"]
            if not usable:
                return
            self._tts = QTextToSpeech()
            self._tts.setVolume(1.0)  # 音量拉满（受系统总音量约束）
            # 尽量切到中文嗓音，保证中文播报清晰
            try:
                for loc in self._tts.availableLocales():
                    if loc.language() == QLocale.Language.Chinese:
                        self._tts.setLocale(loc)
                        break
            except Exception:
                pass
            self._tts.stateChanged.connect(self._on_state)
        except Exception:
            self._tts = None

    def _on_state(self, state) -> None:
        from PySide6.QtTextToSpeech import QTextToSpeech

        if state == QTextToSpeech.State.Speaking:
            self._armed = True
            return
        # 真正朗读过（Speaking）后才认定为“播放结束”，避免 stop() 的瞬时 Ready 误触发
        if self._armed and state in (
            QTextToSpeech.State.Ready,
            QTextToSpeech.State.Paused,
            QTextToSpeech.State.Error,
        ):
            self._armed = False
            cb = self._on_done
            self._on_done = None
            if cb:
                cb()

    def say(self, text: str, on_finished: Callable[[], None] | None = None) -> None:
        """异步朗读 text；on_finished 在播放结束（或无引擎/异常）后回调一次。"""
        if self._tts is None or not text:
            if on_finished:
                on_finished()
            return
        try:
            self._on_done = on_finished
            self._armed = False
            self._tts.stop()  # 停掉上一句，避免叠加重读
            self._tts.say(text)
        except Exception:
            cb = self._on_done
            self._on_done = None
            if cb:
                cb()
