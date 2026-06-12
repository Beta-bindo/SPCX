"""盈利·告警面板：双品种模式下展示合并盈亏明细与爆仓缓冲、行情来源徽标。"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from app.core.models import AppConfig, MarketUpdate, Position, Quote
from app.widgets.pnl_detail_panel import PnlDetailPanel
from app.widgets.symbol_trade_panel import SymbolActionStrip


class SpreadPanel(QFrame):
    """右侧汇总卡片：合并盈亏面板 + 风险缓冲行 + 真实/模拟行情徽标。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("card")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._root = QVBoxLayout(self)
        self._root.setContentsMargins(12, 10, 12, 10)
        self._root.setSpacing(8)

        toolbar = QHBoxLayout()
        toolbar.setSpacing(6)
        title = QLabel("盈利 · 告警")
        title.setObjectName("cardTitle")
        toolbar.addWidget(title)
        toolbar.addStretch()
        self._root.addLayout(toolbar)

        self._combined_section = QWidget()
        self._combined_section.setObjectName("spreadCombinedProfit")
        combined_layout = QVBoxLayout(self._combined_section)
        combined_layout.setContentsMargins(0, 0, 0, 0)
        combined_layout.setSpacing(6)
        self.pnl_detail = PnlDetailPanel("all")
        self.pnl_detail.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )
        combined_layout.addWidget(self.pnl_detail)
        self.risk_label = QLabel("爆仓缓冲 —")
        self.risk_label.setObjectName("riskHint")
        self.risk_label.setWordWrap(False)
        combined_layout.addWidget(self.risk_label)
        combined_layout.addStretch()
        self._combined_section.setVisible(False)
        self._root.addWidget(self._combined_section, stretch=1)

        self._gold_actions: SymbolActionStrip | None = None
        self._silver_actions: SymbolActionStrip | None = None
        self._source_badge: QLabel | None = None
        self._source_simulated: bool | None = None
        self._last_risk_text = ""

    def set_source_badge(self, badge: QLabel) -> None:
        self._source_badge = badge

    def set_action_strips(
        self, gold: SymbolActionStrip, silver: SymbolActionStrip
    ) -> None:
        self._gold_actions = gold
        self._silver_actions = silver

    def apply_layout_mode(self, single: bool) -> None:
        """单品种模式下隐藏合并盈亏区（各品种自有面板）。"""
        self._combined_section.setVisible(not single)
        self._root.setStretchFactor(self._combined_section, 0 if single else 1)

    def refresh_theme(self) -> None:
        """主题切换后刷新徽标与动作条样式。"""
        badge = self._source_badge
        if badge is not None:
            badge.style().unpolish(badge)
            badge.style().polish(badge)
        if self._gold_actions:
            self._gold_actions.refresh_theme()
        if self._silver_actions:
            self._silver_actions.refresh_theme()

    def update_pnl(
        self,
        positions: list[Position],
        ba_quotes: dict[str, Quote],
        mt5_quotes: dict[str, Quote],
        config: AppConfig,
    ) -> None:
        """刷新合并盈亏明细（仅在合并区可见时）。"""
        if self._combined_section.isVisible():
            self.pnl_detail.update(positions, ba_quotes, mt5_quotes, config)

    def update_risk(
        self,
        xau_ba: float,
        xau_mt5: float,
        xag_ba: float,
        xag_mt5: float,
    ) -> None:
        """刷新两品种两端的爆仓缓冲展示行（∞ 表示无风险）。"""
        if not self._combined_section.isVisible():
            return

        def fmt(v: float) -> str:
            return "∞" if v > 90000 else f"{v:.1f}"

        text = (
            f"爆仓缓冲 · 黄金 BA {fmt(xau_ba)} / Ex {fmt(xau_mt5)} · "
            f"白银 BA {fmt(xag_ba)} / Ex {fmt(xag_mt5)}"
        )
        if text != self._last_risk_text:
            self.risk_label.setText(text)
            self._last_risk_text = text

    def update_market(self, update: MarketUpdate) -> None:
        """根据是否含模拟报价切换"真实行情/模拟数据"徽标。"""
        badge = self._source_badge
        if badge is None:
            return
        simulated = any(
            q.is_simulated for q in list(update.ba_quotes.values()) + list(update.mt5_quotes.values())
        )
        if simulated == self._source_simulated:
            return
        self._source_simulated = simulated
        if simulated:
            badge.setText("模拟数据")
            badge.setObjectName("demoBadge")
        else:
            badge.setText("真实行情")
            badge.setObjectName("liveBadge")
        badge.style().unpolish(badge)
        badge.style().polish(badge)
