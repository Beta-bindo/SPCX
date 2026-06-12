"""单品种自动交易设置：按收缩/扩张配置自动开/平仓的点差阈值与触发条件。

黄金支持 Maker 与市价两条"通道"（lane），白银仅市价。每条通道含开仓/平仓各两个方向。
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
)

from app.core.models import AppConfig
from app.widgets.symbol_alert_settings import _settings_int_spin, _settings_spin


def _hold_spin(value: float):
    """构造"持续秒数"用的整数输入框（1~120）。"""
    return _settings_int_spin(max(1, int(round(value))), minimum=1, maximum=120)


class SymbolAutoTradeSettings(QFrame):
    """收缩/扩张自动开平仓：黄金 Maker+市价；白银仅市价。"""

    def __init__(self, preset_id: str, parent=None):
        super().__init__(parent)
        self.preset_id = preset_id
        self.setObjectName("symbolAutoTradeSettings")

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(2)

        # 全局触发条件放最上面：条件连续满足 N 秒后才执行
        root.addLayout(self._build_hold_row())

        if preset_id == "xau":
            root.addLayout(self._build_trade_block("Maker自动开仓", "Maker自动平仓", "maker"))
            # Maker 专属：委托等待超时撤单，紧跟 Maker 区块
            root.addLayout(self._build_maker_wait_row())
            root.addLayout(self._build_trade_block("市价自动开仓", "市价自动平仓", "market"))
        else:
            root.addLayout(self._build_trade_block("市价自动开仓", "市价自动平仓", "market"))

        hint = QLabel(
            "Maker：先 BA 挂单(GTX)，成交后立即 Ex 对冲；超时自动撤单"
            if preset_id == "xau"
            else "自动下单固定市价；有持仓时仅禁反向开/平仓"
        )
        hint.setObjectName("fieldHint")
        root.addWidget(hint)

        self.status_label = QLabel("")
        self.status_label.setObjectName("fieldHint")
        self.status_label.setWordWrap(True)
        root.addWidget(self.status_label)

    def _build_trade_block(self, open_title: str, close_title: str, lane: str) -> QHBoxLayout:
        """构建一条通道的开仓列 + 平仓列（各含收缩/扩张两行），并按通道保存控件引用。"""
        blocks_row = QHBoxLayout()
        blocks_row.setContentsMargins(0, 0, 0, 0)
        blocks_row.setSpacing(12)
        blocks_row.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)

        open_col = QVBoxLayout()
        open_col.setContentsMargins(0, 0, 0, 0)
        open_col.setSpacing(2)
        open_col.addWidget(self._block_title(open_title))

        contraction_row, contraction_enabled, contraction_threshold = self._condition_row(
            "收缩", "点差 >=", 3.0, "开仓"
        )
        open_col.addLayout(contraction_row)

        expansion_row, expansion_enabled, expansion_threshold = self._condition_row(
            "扩张", "点差 <=", -3.0, "开仓"
        )
        open_col.addLayout(expansion_row)

        close_col = QVBoxLayout()
        close_col.setContentsMargins(0, 0, 0, 0)
        close_col.setSpacing(2)
        close_col.addWidget(self._block_title(close_title))

        close_contraction_row, close_contraction_enabled, close_contraction_threshold = (
            self._condition_row("收缩", "点差 <=", 0.5, "平仓")
        )
        close_col.addLayout(close_contraction_row)

        close_expansion_row, close_expansion_enabled, close_expansion_threshold = (
            self._condition_row("扩张", "点差 >=", -0.5, "平仓")
        )
        close_col.addLayout(close_expansion_row)

        if lane == "maker":
            self.contraction_enabled = contraction_enabled
            self.expansion_enabled = expansion_enabled
            self.contraction_threshold = contraction_threshold
            self.expansion_threshold = expansion_threshold
            self.close_contraction_enabled = close_contraction_enabled
            self.close_expansion_enabled = close_expansion_enabled
            self.close_contraction_threshold = close_contraction_threshold
            self.close_expansion_threshold = close_expansion_threshold
        elif self.preset_id == "xau":
            self.market_contraction_enabled = contraction_enabled
            self.market_expansion_enabled = expansion_enabled
            self.market_contraction_threshold = contraction_threshold
            self.market_expansion_threshold = expansion_threshold
            self.market_close_contraction_enabled = close_contraction_enabled
            self.market_close_expansion_enabled = close_expansion_enabled
            self.market_close_contraction_threshold = close_contraction_threshold
            self.market_close_expansion_threshold = close_expansion_threshold
        else:
            self.contraction_enabled = contraction_enabled
            self.expansion_enabled = expansion_enabled
            self.contraction_threshold = contraction_threshold
            self.expansion_threshold = expansion_threshold
            self.close_contraction_enabled = close_contraction_enabled
            self.close_expansion_enabled = close_expansion_enabled
            self.close_contraction_threshold = close_contraction_threshold
            self.close_expansion_threshold = close_expansion_threshold

        blocks_row.addLayout(open_col)
        blocks_row.addLayout(close_col)
        blocks_row.addStretch()
        return blocks_row

    def _block_title(self, text: str) -> QLabel:
        title = QLabel(text)
        title.setObjectName("settingsBlockTitle")
        return title

    def _field_label(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setObjectName("fieldLabel")
        lbl.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        return lbl

    def _build_hold_row(self) -> QHBoxLayout:
        """构建"条件连续满足 N 秒后执行"的全局触发行。"""
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(2)
        self.hold_sec = _hold_spin(3)
        row.addWidget(self._field_label("条件连续满足"))
        row.addWidget(self.hold_sec)
        row.addWidget(self._field_label("秒后执行"))
        row.addStretch()
        return row

    def _build_maker_wait_row(self) -> QHBoxLayout:
        """构建"Maker 委托等待 N 秒未成交撤单"行（仅黄金 Maker 通道）。"""
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(2)
        self.maker_timeout_sec = _hold_spin(5)
        self.maker_timeout_sec.setRange(1, 120)
        row.addWidget(self._field_label("Maker 委托等待"))
        row.addWidget(self.maker_timeout_sec)
        row.addWidget(self._field_label("秒未成交撤单"))
        row.addStretch()
        return row

    def _condition_row(
        self, check_text: str, operator_text: str, threshold_value: float, action_text: str
    ) -> tuple:
        """与点差告警一致的一行式：勾选框 + 条件 + 输入框 + 动作。"""
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(2)
        enabled = QCheckBox(check_text)
        enabled.setObjectName("settingsCheck")
        enabled.setProperty("inline", True)
        threshold = _settings_spin(threshold_value)
        row.addWidget(enabled)
        row.addWidget(self._field_label(operator_text))
        row.addWidget(threshold)
        row.addWidget(self._field_label(action_text))
        row.addStretch()
        return row, enabled, threshold

    def _lane_widgets(self, lane: str) -> tuple:
        """返回某通道的 8 个控件，顺序固定：
        (开收缩启用, 开扩张启用, 开收缩阈值, 开扩张阈值,
         平收缩启用, 平扩张启用, 平收缩阈值, 平扩张阈值)。白银无 maker 通道返回空元组。
        """
        if lane == "maker":
            if self.preset_id != "xau":
                return ()
            return (
                self.contraction_enabled,
                self.expansion_enabled,
                self.contraction_threshold,
                self.expansion_threshold,
                self.close_contraction_enabled,
                self.close_expansion_enabled,
                self.close_contraction_threshold,
                self.close_expansion_threshold,
            )
        if self.preset_id == "xau":
            return (
                self.market_contraction_enabled,
                self.market_expansion_enabled,
                self.market_contraction_threshold,
                self.market_expansion_threshold,
                self.market_close_contraction_enabled,
                self.market_close_expansion_enabled,
                self.market_close_contraction_threshold,
                self.market_close_expansion_threshold,
            )
        return (
            self.contraction_enabled,
            self.expansion_enabled,
            self.contraction_threshold,
            self.expansion_threshold,
            self.close_contraction_enabled,
            self.close_expansion_enabled,
            self.close_contraction_threshold,
            self.close_expansion_threshold,
        )

    def iter_watch_widgets(self):
        for lane in ("maker", "market"):
            for widget in self._lane_widgets(lane):
                yield widget
        yield self.hold_sec
        if self.preset_id == "xau":
            yield self.maker_timeout_sec

    def iter_spin_widgets(self):
        for widget in self.iter_watch_widgets():
            if hasattr(widget, "lock"):
                yield widget

    def lock_all_spins(self) -> None:
        """收起所有数字框的编辑态。"""
        for spin in self.iter_spin_widgets():
            spin.lock()

    def load_config(self, config: AppConfig) -> None:
        """按品种把自动交易配置回填到各控件。"""
        if self.preset_id == "xau":
            self.contraction_enabled.setChecked(config.xau_auto_contraction_enabled)
            self.expansion_enabled.setChecked(config.xau_auto_expansion_enabled)
            self.contraction_threshold.setValue(config.xau_auto_contraction_threshold)
            self.expansion_threshold.setValue(config.xau_auto_expansion_threshold)
            self.close_contraction_enabled.setChecked(config.xau_auto_close_contraction_enabled)
            self.close_expansion_enabled.setChecked(config.xau_auto_close_expansion_enabled)
            self.close_contraction_threshold.setValue(config.xau_auto_close_contraction_threshold)
            self.close_expansion_threshold.setValue(config.xau_auto_close_expansion_threshold)
            self.hold_sec.setValue(max(1, int(round(config.xau_auto_trade_hold_sec))))
            self.market_contraction_enabled.setChecked(config.xau_auto_market_contraction_enabled)
            self.market_expansion_enabled.setChecked(config.xau_auto_market_expansion_enabled)
            self.market_contraction_threshold.setValue(config.xau_auto_market_contraction_threshold)
            self.market_expansion_threshold.setValue(config.xau_auto_market_expansion_threshold)
            self.market_close_contraction_enabled.setChecked(
                config.xau_auto_market_close_contraction_enabled
            )
            self.market_close_expansion_enabled.setChecked(
                config.xau_auto_market_close_expansion_enabled
            )
            self.market_close_contraction_threshold.setValue(
                config.xau_auto_market_close_contraction_threshold
            )
            self.market_close_expansion_threshold.setValue(
                config.xau_auto_market_close_expansion_threshold
            )
            self.maker_timeout_sec.setValue(
                max(1, int(round(config.ba_maker_timeout_sec)))
            )
        else:
            self.contraction_enabled.setChecked(config.xag_auto_contraction_enabled)
            self.expansion_enabled.setChecked(config.xag_auto_expansion_enabled)
            self.contraction_threshold.setValue(config.xag_auto_contraction_threshold)
            self.expansion_threshold.setValue(config.xag_auto_expansion_threshold)
            self.close_contraction_enabled.setChecked(config.xag_auto_close_contraction_enabled)
            self.close_expansion_enabled.setChecked(config.xag_auto_close_expansion_enabled)
            self.close_contraction_threshold.setValue(config.xag_auto_close_contraction_threshold)
            self.close_expansion_threshold.setValue(config.xag_auto_close_expansion_threshold)
            self.hold_sec.setValue(max(1, int(round(config.xag_auto_trade_hold_sec))))

    def apply_to(self, config: AppConfig) -> None:
        """把控件值写回配置。"""
        if self.preset_id == "xau":
            config.xau_auto_contraction_enabled = self.contraction_enabled.isChecked()
            config.xau_auto_expansion_enabled = self.expansion_enabled.isChecked()
            config.xau_auto_contraction_threshold = self.contraction_threshold.value()
            config.xau_auto_expansion_threshold = self.expansion_threshold.value()
            config.xau_auto_close_contraction_enabled = self.close_contraction_enabled.isChecked()
            config.xau_auto_close_expansion_enabled = self.close_expansion_enabled.isChecked()
            config.xau_auto_close_contraction_threshold = self.close_contraction_threshold.value()
            config.xau_auto_close_expansion_threshold = self.close_expansion_threshold.value()
            config.xau_auto_trade_hold_sec = float(self.hold_sec.value())
            config.xau_auto_market_contraction_enabled = self.market_contraction_enabled.isChecked()
            config.xau_auto_market_expansion_enabled = self.market_expansion_enabled.isChecked()
            config.xau_auto_market_contraction_threshold = self.market_contraction_threshold.value()
            config.xau_auto_market_expansion_threshold = self.market_expansion_threshold.value()
            config.xau_auto_market_close_contraction_enabled = (
                self.market_close_contraction_enabled.isChecked()
            )
            config.xau_auto_market_close_expansion_enabled = (
                self.market_close_expansion_enabled.isChecked()
            )
            config.xau_auto_market_close_contraction_threshold = (
                self.market_close_contraction_threshold.value()
            )
            config.xau_auto_market_close_expansion_threshold = (
                self.market_close_expansion_threshold.value()
            )
            config.ba_maker_timeout_sec = float(self.maker_timeout_sec.value())
        else:
            config.xag_auto_contraction_enabled = self.contraction_enabled.isChecked()
            config.xag_auto_expansion_enabled = self.expansion_enabled.isChecked()
            config.xag_auto_contraction_threshold = self.contraction_threshold.value()
            config.xag_auto_expansion_threshold = self.expansion_threshold.value()
            config.xag_auto_close_contraction_enabled = self.close_contraction_enabled.isChecked()
            config.xag_auto_close_expansion_enabled = self.close_expansion_enabled.isChecked()
            config.xag_auto_close_contraction_threshold = self.close_contraction_threshold.value()
            config.xag_auto_close_expansion_threshold = self.close_expansion_threshold.value()
            config.xag_auto_trade_hold_sec = float(self.hold_sec.value())

    def any_enabled(self) -> bool:
        """是否存在任一已启用的自动开/平仓条件。"""
        enabled = (
            self.contraction_enabled.isChecked()
            or self.expansion_enabled.isChecked()
            or self.close_contraction_enabled.isChecked()
            or self.close_expansion_enabled.isChecked()
        )
        if self.preset_id == "xau":
            enabled = enabled or (
                self.market_contraction_enabled.isChecked()
                or self.market_expansion_enabled.isChecked()
                or self.market_close_contraction_enabled.isChecked()
                or self.market_close_expansion_enabled.isChecked()
            )
        return enabled

    def snapshot_lock_state(self) -> tuple[bool, ...]:
        """采集各勾选框的勾选/可用状态指纹，用于检测持仓联动后是否需刷新。"""
        flags: list[bool] = []
        for lane in ("maker", "market"):
            widgets = self._lane_widgets(lane)
            if not widgets:
                continue
            for widget in widgets:
                if hasattr(widget, "isChecked"):
                    flags.append(widget.isChecked())
                    flags.append(widget.isEnabled())
                else:
                    flags.append(widget.isEnabled())
        return tuple(flags)

    def set_status(self, text: str) -> None:
        """更新底部状态提示行（空文本则隐藏）。"""
        self.status_label.setText(text)
        self.status_label.setVisible(bool(text))

    def apply_position_lock(self, active_mode: str | None) -> None:
        """有持仓时禁用并取消反向（扩张/收缩）开/平仓；同方向 Maker/市价互不影响。"""
        for lane in ("maker", "market"):
            widgets = self._lane_widgets(lane)
            if not widgets:
                continue
            pairs = [
                (widgets[0], widgets[2]),
                (widgets[1], widgets[3]),
                (widgets[4], widgets[6]),
                (widgets[5], widgets[7]),
            ]
            for enabled, threshold in pairs:
                enabled.setEnabled(True)
                threshold.setEnabled(True)

            if active_mode is None:
                continue

            lock_pairs = (
                (widgets[1], widgets[3]),
                (widgets[5], widgets[7]),
            ) if active_mode == "contraction" else (
                (widgets[0], widgets[2]),
                (widgets[4], widgets[6]),
            )
            for enabled, threshold in lock_pairs:
                enabled.blockSignals(True)
                enabled.setChecked(False)
                enabled.blockSignals(False)
                enabled.setEnabled(False)
                threshold.setEnabled(False)
