from __future__ import annotations

from dataclasses import dataclass

from app.core.models import AppConfig, ConnectionState

HIGH_LATENCY_MS = 200.0


@dataclass
class NetworkStatus:
    running: bool
    ba_ms: float | None
    mt5_ms: float | None
    ba_state: str
    mt5_state: str
    ba_live: bool
    mt5_live: bool

    @classmethod
    def from_engine(cls, engine, running: bool) -> "NetworkStatus":
        cfg: AppConfig = engine.config
        return cls(
            running=running,
            ba_ms=engine.binance.latency_ms,
            mt5_ms=engine.mt5.latency_ms,
            ba_state=engine.binance.state.value,
            mt5_state=engine.mt5.state.value,
            ba_live=cfg.use_live_ba,
            mt5_live=cfg.use_live_mt5,
        )

    def _endpoint_offline(self, live: bool, state: str) -> bool:
        if not live:
            return False
        return state in {
            ConnectionState.DISCONNECTED.value,
            ConnectionState.ERROR.value,
        }

    def _endpoint_slow(self, live: bool, state: str, ms: float | None) -> bool:
        if not live:
            return False
        if state == ConnectionState.CONNECTING.value:
            return True
        return ms is not None and ms >= HIGH_LATENCY_MS

    @property
    def level(self) -> str:
        """ok | slow | offline"""
        if not self.running:
            return "offline"
        if self._endpoint_offline(self.ba_live, self.ba_state) or self._endpoint_offline(
            self.mt5_live, self.mt5_state
        ):
            return "offline"
        if self._endpoint_slow(self.ba_live, self.ba_state, self.ba_ms) or self._endpoint_slow(
            self.mt5_live, self.mt5_state, self.mt5_ms
        ):
            return "slow"
        return "ok"

    def _fmt_ms(self, ms: float | None) -> str:
        if ms is None:
            return "--"
        return f"{ms:.0f}ms"

    def _fmt_ms_fixed(self, ms: float | None) -> str:
        """固定 4 位数字宽度，避免顶栏延迟刷新时撑开布局。"""
        if ms is None:
            return "----"
        value = min(max(int(round(ms)), 0), 9999)
        return f"{value:4d}"

    def _show_ba_latency(self) -> bool:
        return self.ba_live or self.ba_state == ConnectionState.SIMULATED.value

    def _show_ex_latency(self) -> bool:
        return self.mt5_live or self.mt5_state == ConnectionState.SIMULATED.value

    def ba_latency_line(self) -> str:
        if not self._show_ba_latency():
            return "BA ----ms"
        return f"BA {self._fmt_ms_fixed(self.ba_ms)}ms"

    def ex_latency_line(self) -> str:
        if not self._show_ex_latency():
            return "Ex ----ms"
        return f"Ex {self._fmt_ms_fixed(self.mt5_ms)}ms"

    def ba_ms_text(self) -> str:
        if not self._show_ba_latency():
            return "--"
        return self._fmt_ms_capped(self.ba_ms)

    def ex_ms_text(self) -> str:
        if not self._show_ex_latency():
            return "--"
        return self._fmt_ms_capped(self.mt5_ms)

    def _fmt_ms_capped(self, ms: float | None) -> str:
        if ms is None:
            return "--"
        value = min(max(int(round(ms)), 0), 9999)
        return f"{value}ms"

    @property
    def compact_text(self) -> str:
        if not self.running:
            return "未启动"
        if self.level == "offline":
            return "断网"
        return ""

    @property
    def label_text(self) -> str:
        if not self.running:
            return "网络 · 未启动"
        if self.level == "offline":
            return "网络 · 断网"
        parts: list[str] = []
        if self.ba_live or self.ba_state == ConnectionState.SIMULATED.value:
            parts.append(f"BA {self._fmt_ms(self.ba_ms)}")
        if self.mt5_live or self.mt5_state == ConnectionState.SIMULATED.value:
            parts.append(f"Ex {self._fmt_ms(self.mt5_ms)}")
        if not parts:
            return "网络 · --"
        return "网络 · " + " / ".join(parts)
