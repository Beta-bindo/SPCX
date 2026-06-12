from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
)

from app.core.models import AppConfig
from app.widgets.symbol_alert_settings import ClickToEditDoubleSpinBox


class SymbolRatioFields(QFrame):
    """单品种数量配比：BA / Exness / 开仓手数。"""

    def __init__(
        self,
        preset_id: str,
        config: AppConfig,
        *,
        parent=None,
    ):
        super().__init__(parent)
        self.preset_id = preset_id
        self.setObjectName("symbolRatioFields")

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(4)

        title = QLabel("数量配比")
        title.setObjectName("fieldLabel")
        root.addWidget(title)

        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(2)
        grid.setVerticalSpacing(2)
        for col, text in enumerate(["", "BA", "Exness", "开仓"]):
            hdr = QLabel(text)
            hdr.setObjectName("fieldLabel")
            if col:
                hdr.setAlignment(Qt.AlignmentFlag.AlignCenter)
            grid.addWidget(hdr, 0, col)

        sym = QLabel("黄金" if preset_id == "xau" else "白银")
        sym.setObjectName("fieldLabel")
        sym.setProperty("symbolTag", "true")
        sym.style().unpolish(sym)
        sym.style().polish(sym)
        grid.addWidget(sym, 1, 0)

        if preset_id == "xau":
            ba_default, mt5_default, lots_default = 500.0, 1.0, 1.0
            ba_val = config.xau_ba_qty_map
            mt5_val = config.xau_mt5_lot_map
            lots_val = config.xau_trade_lots
        else:
            ba_default, mt5_default, lots_default = 5000.0, 1.0, 1.0
            ba_val = config.xag_ba_qty_map
            mt5_val = config.xag_mt5_lot_map
            lots_val = config.xag_trade_lots

        self.ba_map = _ratio_spin(ba_val or ba_default, minimum=0.001)
        self.mt5_map = _ratio_spin(
            mt5_val or mt5_default, minimum=0.001, maximum=9999
        )
        self.trade_lots = _ratio_spin(
            lots_val or lots_default,
            decimals=2,
            minimum=0.01,
            maximum=100,
            step=0.01,
        )
        for col, spin in enumerate((self.ba_map, self.mt5_map, self.trade_lots), start=1):
            spin.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
            grid.addWidget(spin, 1, col)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.addLayout(grid)
        row.addStretch()
        root.addLayout(row)

        info_row = QHBoxLayout()
        info_row.setContentsMargins(0, 0, 0, 0)
        info_row.setSpacing(12)
        self.preview = QLabel("")
        self.preview.setObjectName("fieldHint")
        self.leverage_label = QLabel(
            f"BA {config.ba_leverage}x · Ex {config.mt5_leverage}x"
        )
        self.leverage_label.setObjectName("fieldHint")
        self.leverage_label.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        info_row.addWidget(self.preview, stretch=1)
        info_row.addWidget(self.leverage_label, stretch=0)
        root.addLayout(info_row)

        for spin in (self.ba_map, self.mt5_map, self.trade_lots):
            spin.valueChanged.connect(self._update_preview)
        self._update_preview()
        self.setMouseTracking(True)

    def lock_all_spins(self) -> None:
        for spin in (self.ba_map, self.mt5_map, self.trade_lots):
            spin.lock()

    def mousePressEvent(self, event) -> None:
        target = self.childAt(event.pos())
        while target is not None and target is not self:
            if isinstance(target, ClickToEditDoubleSpinBox):
                break
            target = target.parentWidget()
        else:
            self.lock_all_spins()
        super().mousePressEvent(event)

    def leaveEvent(self, event) -> None:
        self.lock_all_spins()
        super().leaveEvent(event)

    def _update_preview(self) -> None:
        cfg = AppConfig()
        self.apply_to(cfg)
        ba_q = cfg.ba_quantity_for(self.preset_id)
        mt5_l = cfg.mt5_lot_for(self.preset_id)
        self.preview.setText(f"本次：BA 数量 {ba_q:.4g} · Exness 手数 {mt5_l:.2f}")

    def apply_to(self, config: AppConfig) -> None:
        if self.preset_id == "xau":
            config.xau_ba_qty_map = self.ba_map.value()
            config.xau_mt5_lot_map = self.mt5_map.value()
            config.xau_trade_lots = self.trade_lots.value()
        else:
            config.xag_ba_qty_map = self.ba_map.value()
            config.xag_mt5_lot_map = self.mt5_map.value()
            config.xag_trade_lots = self.trade_lots.value()
        config.xau_ba_quantity = config.ba_quantity_for("xau")
        config.xag_ba_quantity = config.ba_quantity_for("xag")
        config.xau_mt5_lot_size = config.mt5_lot_for("xau")
        config.xag_mt5_lot_size = config.mt5_lot_for("xag")


def _ratio_spin(
    value: float,
    *,
    decimals: int = 3,
    minimum: float = -9999,
    maximum: float = 999999,
    step: float = 0.1,
) -> ClickToEditDoubleSpinBox:
    spin = ClickToEditDoubleSpinBox()
    spin.setRange(minimum, maximum)
    spin.setDecimals(decimals)
    spin.setSingleStep(step)
    spin.setValue(value)
    spin.setButtonSymbols(ClickToEditDoubleSpinBox.ButtonSymbols.NoButtons)
    spin.setAlignment(Qt.AlignmentFlag.AlignRight)
    spin.setFixedSize(52, 18)
    spin.setObjectName("settingsSpin")
    spin.setProperty("inline", True)
    spin.setProperty("readOnlyMode", True)
    return spin
