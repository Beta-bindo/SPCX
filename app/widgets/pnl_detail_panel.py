from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from app.core.models import AppConfig, Position, Quote
from app.core.hedge_health import (
    HedgeRepair,
    analyze_hedge_health,
    combine_hedge_health,
    format_hedge_banner,
    suggest_hedge_repair,
)
from app.core.position_detail import (
    PlatformDetail,
    build_platform_details,
    build_platform_details_for_preset,
)
from app.core.symbols import find_preset
from app.core.theme import set_flag
from app.core.trading_service import detect_hedge_mode, hedge_strategy_label_for_platform


_COL_WIDTHS = {
    "pnl": 68,
    "qty": 52,
    "side": 44,
    "liq": 58,
    "buf": 44,
    "lev": 36,
}

_DIALOG_COL_WIDTHS = {
    "pnl": 72,
    "qty": 56,
    "side": 44,
    "lev": 40,
}


class PnlDetailPanel(QFrame):
    hedge_repair_requested = Signal(object)

    def __init__(
        self,
        preset_id: str,
        parent=None,
        *,
        show_liq_buf: bool = True,
        compact_margins: bool = False,
    ):
        super().__init__(parent)
        self._preset_id = preset_id
        self._show_liq_buf = show_liq_buf
        self._fields = ("pnl", "qty", "side", "liq", "buf", "lev") if show_liq_buf else (
            "pnl",
            "qty",
            "side",
            "lev",
        )
        col_widths = _COL_WIDTHS if show_liq_buf else _DIALOG_COL_WIDTHS
        self.setObjectName("pnlDetailPanel")
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        root = QVBoxLayout(self)
        if compact_margins:
            root.setContentsMargins(8, 6, 8, 6)
        else:
            root.setContentsMargins(10, 8, 10, 8)
        root.setSpacing(4)

        header = QHBoxLayout()
        title = QLabel("盈利情况")
        title.setObjectName("settingsBlockTitle")
        self.total_label = QLabel("实时净盈亏：$0.00")
        self.total_label.setObjectName("pnlTotal")
        header.addWidget(title)
        header.addStretch()
        header.addWidget(self.total_label)
        root.addLayout(header)

        alert_row = QHBoxLayout()
        alert_row.setContentsMargins(0, 0, 0, 0)
        alert_row.setSpacing(6)
        self.hedge_alert = QLabel("")
        self.hedge_alert.setObjectName("hedgeAlert")
        self.hedge_alert.setWordWrap(True)
        self.hedge_alert.setVisible(False)
        self.hedge_repair_btn = QPushButton("补对冲")
        self.hedge_repair_btn.setObjectName("hedgeRepairButton")
        self.hedge_repair_btn.setProperty("compact", True)
        self.hedge_repair_btn.setFixedHeight(26)
        self.hedge_repair_btn.setVisible(False)
        self.hedge_repair_btn.clicked.connect(self._emit_repair_requested)
        alert_row.addWidget(self.hedge_alert, 1)
        alert_row.addWidget(self.hedge_repair_btn, 0, Qt.AlignmentFlag.AlignTop)
        alert_host = QWidget()
        alert_host.setLayout(alert_row)
        alert_host.setVisible(False)
        self._alert_host = alert_host
        root.addWidget(alert_host)

        self._pending_repair: HedgeRepair | None = None

        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(2)
        grid.setVerticalSpacing(0)
        col_titles = ["", "盈亏", "持仓", "方向"]
        if show_liq_buf:
            col_titles.extend(["爆", "强"])
        col_titles.append("杠")
        for col, text in enumerate(col_titles):
            lbl = QLabel(text)
            lbl.setObjectName("fieldLabel")
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
            grid.addWidget(lbl, 0, col)

        self._rows: dict[str, dict[str, QLabel]] = {}
        self._last_total_text = ""
        self._last_cell_text: dict[str, str] = {}
        self._last_hedge_banner = ""
        self._last_repair_visible = False
        for row, (key, name) in enumerate((("BA", "BA"), ("MT5", "Exness")), start=1):
            plat = QLabel(name)
            plat.setObjectName("platformTag")
            plat.setAlignment(Qt.AlignmentFlag.AlignCenter)
            plat.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
            grid.addWidget(plat, row, 0)
            cells: dict[str, QLabel] = {}
            for col, field in enumerate(self._fields, start=1):
                cell = QLabel("--")
                cell.setObjectName("positionValue")
                cell.setAlignment(Qt.AlignmentFlag.AlignCenter)
                cell.setFixedWidth(col_widths[field])
                cell.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
                grid.addWidget(cell, row, col)
                cells[field] = cell
            self._rows[key] = cells
        for col, field in enumerate(self._fields, start=1):
            grid.setColumnMinimumWidth(col, col_widths[field])
        root.addLayout(grid)

    def _paint_pnl(self, lbl: QLabel, value: float) -> None:
        sign = "+" if value >= 0 else "-"
        text = f"${sign}{abs(value):.2f}" if value != 0 else "$0.00"
        key = f"pnl:{id(lbl)}"
        if self._last_cell_text.get(key) != text:
            lbl.setText(text)
            self._last_cell_text[key] = text
        positive = value > 0
        negative = value < 0
        if lbl.property("positive") != ("true" if positive else "false"):
            set_flag(lbl, "positive", positive)
        if lbl.property("negative") != ("true" if negative else "false"):
            set_flag(lbl, "negative", negative)

    def _set_cell(self, lbl: QLabel, text: str) -> None:
        key = f"cell:{id(lbl)}"
        if self._last_cell_text.get(key) == text:
            return
        lbl.setText(text)
        self._last_cell_text[key] = text

    def _fill_row(
        self,
        cells: dict[str, QLabel],
        detail: PlatformDetail,
        hedge_mode: str | None,
    ) -> None:
        if not detail.has_position:
            self._paint_pnl(cells["pnl"], 0.0)
            for key in self._fields:
                if key == "pnl":
                    continue
                self._set_cell(cells[key], "--")
            return
        self._paint_pnl(cells["pnl"], detail.pnl)
        self._set_cell(cells["qty"], f"{detail.quantity:.2f}")
        self._set_cell(
            cells["side"],
            hedge_strategy_label_for_platform(detail.platform, hedge_mode),
        )
        if "liq" in cells:
            self._set_cell(
                cells["liq"],
                f"{detail.liquidation_price:.3f}" if detail.liquidation_price else "--",
            )
        if "buf" in cells:
            self._set_cell(cells["buf"], f"{detail.liq_buffer:.1f}" if detail.liq_buffer else "--")
        self._set_cell(cells["lev"], f"{detail.leverage}x")

    def _emit_repair_requested(self) -> None:
        if self._pending_repair is not None:
            self.hedge_repair_requested.emit(self._pending_repair)

    def request_hedge_repair(self) -> None:
        self._emit_repair_requested()

    def set_hedge_repair(self, repair: HedgeRepair | None) -> None:
        self._pending_repair = repair
        visible = repair is not None
        if visible != self._last_repair_visible:
            self.hedge_repair_btn.setVisible(visible)
            self._alert_host.setVisible(visible or self.hedge_alert.isVisible())
            self._last_repair_visible = visible
        if repair is not None and repair.tooltip:
            self.hedge_repair_btn.setToolTip(repair.tooltip)
        else:
            self.hedge_repair_btn.setToolTip("")

    def update_hedge_health(self, health, repair: HedgeRepair | None = None) -> None:
        banner = format_hedge_banner(health)
        if banner != self._last_hedge_banner:
            self.hedge_alert.setText(banner)
            self._last_hedge_banner = banner
        visible = bool(banner)
        if self.hedge_alert.isVisible() != visible:
            self.hedge_alert.setVisible(visible)
        if self._alert_host.isVisible() != (visible or repair is not None):
            self._alert_host.setVisible(visible or repair is not None)
        level = health.level if visible and health.level in ("warn", "alert") else ""
        if self.hedge_alert.property("hedgeAlert") != level:
            set_flag(self.hedge_alert, "hedgeAlert", level or False)
        self.set_hedge_repair(repair)

    def update(
        self,
        positions: list[Position],
        ba_quotes: dict[str, Quote],
        mt5_quotes: dict[str, Quote],
        config: AppConfig,
    ) -> None:
        ba, mt5 = (
            build_platform_details(positions, ba_quotes, mt5_quotes, config)
            if self._preset_id == "all"
            else build_platform_details_for_preset(
                self._preset_id, positions, ba_quotes, mt5_quotes, config
            )
        )
        hedge_mode = (
            detect_hedge_mode(self._preset_id, positions)
            if self._preset_id != "all"
            else None
        )
        self._fill_row(self._rows["BA"], ba, hedge_mode)
        self._fill_row(self._rows["MT5"], mt5, hedge_mode)
        if self._preset_id == "all":
            health = combine_hedge_health(
                analyze_hedge_health("xau", positions, config),
                analyze_hedge_health("xag", positions, config),
            )
        else:
            health = analyze_hedge_health(self._preset_id, positions, config)
        repair = (
            suggest_hedge_repair(self._preset_id, positions, health)
            if self._preset_id != "all"
            else None
        )
        self.update_hedge_health(health, repair)
        preset = find_preset(self._preset_id)
        ba_pos = next(
            (p for p in positions if p.platform == "BA" and p.symbol == preset.symbol_ba),
            None,
        )
        mt5_pos = next(
            (p for p in positions if p.platform == "MT5" and p.symbol == preset.symbol_mt5),
            None,
        )
        ba_fee = ba_pos.estimated_fee if ba_pos else 0.0
        mt5_fee = mt5_pos.estimated_fee if mt5_pos else 0.0
        net = round(ba.pnl + mt5.pnl - ba_fee - mt5_fee, 2)
        sign = "+" if net >= 0 else "-"
        total_text = f"实时净盈亏：${sign}{abs(net):.2f}"
        if total_text != self._last_total_text:
            self.total_label.setText(total_text)
            self._last_total_text = total_text
        positive = net > 0
        negative = net < 0
        if self.total_label.property("positive") != ("true" if positive else "false"):
            set_flag(self.total_label, "positive", positive)
        if self.total_label.property("negative") != ("true" if negative else "false"):
            set_flag(self.total_label, "negative", negative)
