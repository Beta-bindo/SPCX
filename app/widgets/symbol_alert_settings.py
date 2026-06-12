"""单品种告警设置组件，并定义"点击进入编辑、失焦自动锁定"的只读数字输入框。"""

from __future__ import annotations

from PySide6.QtCore import Qt, QEvent
from PySide6.QtGui import QFocusEvent, QMouseEvent
from PySide6.QtWidgets import (
    QCheckBox,
    QDoubleSpinBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
)

from app.core.models import AppConfig


def format_decimal_text(value: float, decimals: int) -> str:
    """按实际精度显示：3 -> 3，3.55 -> 3.55，不补无意义的尾随 0。"""
    if decimals <= 0:
        return str(int(round(value)))
    text = f"{value:.{decimals}f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


class ClickToEditDoubleSpinBox(QDoubleSpinBox):
    """默认只读；双击进入编辑，点击其他区域失焦后锁定。"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        editor = self.lineEdit()
        if editor is not None:
            editor.installEventFilter(self)
        self._apply_lock(True)

    def is_locked(self) -> bool:
        editor = self.lineEdit()
        return editor is None or editor.isReadOnly()

    def _unlock_for_edit(self) -> None:
        self._apply_lock(False)
        editor = self.lineEdit()
        if editor is not None:
            editor.setFocus(Qt.FocusReason.MouseFocusReason)
            editor.selectAll()
        else:
            self.setFocus(Qt.FocusReason.MouseFocusReason)

    def eventFilter(self, watched, event) -> bool:
        editor = self.lineEdit()
        if watched is editor:
            if event.type() == QEvent.Type.MouseButtonDblClick:
                if event.button() == Qt.MouseButton.LeftButton:
                    self._unlock_for_edit()
                    return True
            if event.type() == QEvent.Type.MouseButtonPress and self.is_locked():
                event.accept()
                return True
        return super().eventFilter(watched, event)

    def _apply_lock(self, locked: bool) -> None:
        editor = self.lineEdit()
        if editor is not None:
            editor.setReadOnly(locked)
        self.setFocusPolicy(
            Qt.FocusPolicy.NoFocus if locked else Qt.FocusPolicy.StrongFocus
        )
        if self.property("readOnlyMode") != locked:
            self.setProperty("readOnlyMode", locked)
            self.style().unpolish(self)
            self.style().polish(self)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if self.is_locked():
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self.is_locked():
            self._unlock_for_edit()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)
        if not self.is_locked():
            self.selectAll()

    def focusOutEvent(self, event: QFocusEvent) -> None:
        super().focusOutEvent(event)
        self._apply_lock(True)

    def wheelEvent(self, event) -> None:
        if self.is_locked():
            event.ignore()
            return
        super().wheelEvent(event)

    def keyPressEvent(self, event) -> None:
        if self.is_locked():
            event.ignore()
            return
        super().keyPressEvent(event)

    def stepBy(self, steps: int) -> None:
        if self.is_locked():
            return
        super().stepBy(steps)

    def lock(self) -> None:
        self._apply_lock(True)
        if self.hasFocus():
            self.clearFocus()

    def textFromValue(self, value: float) -> str:
        return format_decimal_text(value, self.decimals())


class ClickToEditSpinBox(QSpinBox):
    """默认只读；双击进入编辑，失焦后锁定。"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        editor = self.lineEdit()
        if editor is not None:
            editor.installEventFilter(self)
        self._apply_lock(True)

    def is_locked(self) -> bool:
        editor = self.lineEdit()
        return editor is None or editor.isReadOnly()

    def _unlock_for_edit(self) -> None:
        self._apply_lock(False)
        editor = self.lineEdit()
        if editor is not None:
            editor.setFocus(Qt.FocusReason.MouseFocusReason)
            editor.selectAll()
        else:
            self.setFocus(Qt.FocusReason.MouseFocusReason)

    def eventFilter(self, watched, event) -> bool:
        editor = self.lineEdit()
        if watched is editor:
            if event.type() == QEvent.Type.MouseButtonDblClick:
                if event.button() == Qt.MouseButton.LeftButton:
                    self._unlock_for_edit()
                    return True
            if event.type() == QEvent.Type.MouseButtonPress and self.is_locked():
                event.accept()
                return True
        return super().eventFilter(watched, event)

    def _apply_lock(self, locked: bool) -> None:
        editor = self.lineEdit()
        if editor is not None:
            editor.setReadOnly(locked)
        self.setFocusPolicy(
            Qt.FocusPolicy.NoFocus if locked else Qt.FocusPolicy.StrongFocus
        )
        if self.property("readOnlyMode") != locked:
            self.setProperty("readOnlyMode", locked)
            self.style().unpolish(self)
            self.style().polish(self)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if self.is_locked():
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self.is_locked():
            self._unlock_for_edit()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)
        if not self.is_locked():
            self.selectAll()

    def focusOutEvent(self, event: QFocusEvent) -> None:
        super().focusOutEvent(event)
        self._apply_lock(True)

    def wheelEvent(self, event) -> None:
        if self.is_locked():
            event.ignore()
            return
        super().wheelEvent(event)

    def keyPressEvent(self, event) -> None:
        if self.is_locked():
            event.ignore()
            return
        super().keyPressEvent(event)

    def stepBy(self, steps: int) -> None:
        if self.is_locked():
            return
        super().stepBy(steps)

    def lock(self) -> None:
        self._apply_lock(True)
        if self.hasFocus():
            self.clearFocus()


def _settings_spin(
    value: float,
    *,
    decimals: int = 3,
    minimum: float = -9999,
    maximum: float = 999999,
    step: float = 0.1,
) -> ClickToEditDoubleSpinBox:
    """构造设置区使用的紧凑小数输入框（无按钮、右对齐、点击编辑）。"""
    spin = ClickToEditDoubleSpinBox()
    spin.setRange(minimum, maximum)
    spin.setDecimals(decimals)
    spin.setSingleStep(step)
    spin.setValue(value)
    spin.setButtonSymbols(QDoubleSpinBox.ButtonSymbols.NoButtons)
    spin.setAlignment(Qt.AlignmentFlag.AlignRight)
    spin.setFixedSize(52, 18)
    spin.setObjectName("settingsSpin")
    spin.setProperty("inline", True)
    return spin


def _settings_int_spin(
    value: int,
    *,
    minimum: int = -9999,
    maximum: int = 999999,
    step: int = 1,
    fixed_width: int = 52,
) -> ClickToEditSpinBox:
    """构造设置区使用的紧凑整数输入框。"""
    spin = ClickToEditSpinBox()
    spin.setRange(minimum, maximum)
    spin.setSingleStep(step)
    spin.setValue(value)
    spin.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)
    spin.setAlignment(Qt.AlignmentFlag.AlignRight)
    spin.setFixedSize(fixed_width, 18)
    spin.setObjectName("settingsSpin")
    spin.setProperty("inline", True)
    return spin


class SymbolAlertSettings(QFrame):
    """单品种告警设置：点差区间 + 爆仓阈值，置于点差值下方。"""

    def __init__(self, preset_id: str, parent=None):
        super().__init__(parent)
        self.preset_id = preset_id
        self.setObjectName("symbolAlertSettings")

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(2)

        title = QLabel("告警设置")
        title.setObjectName("settingsBlockTitle")
        root.addWidget(title)

        self.spread_enabled = QCheckBox("声音")
        self.spread_enabled.setObjectName("settingsCheck")
        self.spread_enabled.setProperty("inline", True)
        self.liq_enabled = QCheckBox("声音")
        self.liq_enabled.setObjectName("settingsCheck")
        self.liq_enabled.setProperty("inline", True)

        spread_row = QHBoxLayout()
        spread_row.setContentsMargins(0, 0, 0, 0)
        spread_row.setSpacing(2)
        spread_row.addWidget(self.spread_enabled)
        spread_row.addWidget(self._label("点差值 <="))
        self.spread_min = _settings_spin(1.0)
        self.spread_max = _settings_spin(3.0)
        spread_row.addWidget(self.spread_min)
        sep = QLabel("或者 >=")
        sep.setObjectName("rangeSep")
        sep.setAlignment(Qt.AlignmentFlag.AlignCenter)
        spread_row.addWidget(sep)
        spread_row.addWidget(self.spread_max)
        spread_row.addWidget(self._label("报警"))
        spread_row.addStretch()
        root.addLayout(spread_row)

        liq_row = QHBoxLayout()
        liq_row.setContentsMargins(0, 0, 0, 0)
        liq_row.setSpacing(2)
        liq_row.addWidget(self.liq_enabled)
        liq_row.addWidget(self._label("BA爆仓 <="))
        self.ba_liq = _settings_spin(100, decimals=1, step=1)
        liq_row.addWidget(self.ba_liq)
        liq_row.addWidget(self._label("Ex爆仓 <="))
        self.mt5_liq = _settings_spin(100, decimals=1, step=1)
        liq_row.addWidget(self.mt5_liq)
        liq_row.addWidget(self._label("报警"))
        liq_row.addStretch()
        root.addLayout(liq_row)

        hint = QLabel("勾选声音后，触发对应条件就持续响；取消勾选或条件恢复立即停声")
        hint.setObjectName("fieldHint")
        root.addWidget(hint)

    def _label(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setObjectName("fieldLabel")
        lbl.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        return lbl

    def iter_watch_widgets(self):
        """逐个产出需要监听变更的控件（用于自动保存/联动）。"""
        yield self.spread_enabled
        yield self.liq_enabled
        yield self.spread_min
        yield self.spread_max
        yield self.ba_liq
        yield self.mt5_liq

    def lock_all_spins(self) -> None:
        for spin in (self.spread_min, self.spread_max, self.ba_liq, self.mt5_liq):
            spin.lock()

    def load_config(self, config: AppConfig) -> None:
        """按品种把告警配置回填到控件。"""
        if self.preset_id == "xau":
            self.spread_enabled.setChecked(config.xau_spread_alert_enabled)
            self.liq_enabled.setChecked(config.xau_liq_alert_enabled)
            self.spread_min.setValue(config.xau_spread_alert_min)
            self.spread_max.setValue(config.xau_spread_alert_max)
            self.ba_liq.setValue(config.xau_ba_liq_alert)
            self.mt5_liq.setValue(config.xau_mt5_liq_alert)
        else:
            self.spread_enabled.setChecked(config.xag_spread_alert_enabled)
            self.liq_enabled.setChecked(config.xag_liq_alert_enabled)
            self.spread_min.setValue(config.xag_spread_alert_min)
            self.spread_max.setValue(config.xag_spread_alert_max)
            self.ba_liq.setValue(config.xag_ba_liq_alert)
            self.mt5_liq.setValue(config.xag_mt5_liq_alert)

    def apply_to(self, config: AppConfig) -> None:
        """把控件值写回配置（同时同步派生的声音开关字段）。"""
        if self.preset_id == "xau":
            config.xau_spread_alert_enabled = self.spread_enabled.isChecked()
            config.xau_liq_alert_enabled = self.liq_enabled.isChecked()
            config.xau_spread_alert_min = self.spread_min.value()
            config.xau_spread_alert_max = self.spread_max.value()
            config.xau_ba_liq_alert = self.ba_liq.value()
            config.xau_mt5_liq_alert = self.mt5_liq.value()
            config.xau_spread_sound_enabled = config.xau_spread_alert_enabled
            config.xau_liq_sound_enabled = config.xau_liq_alert_enabled
            config.xau_alert_sound_enabled = (
                config.xau_spread_alert_enabled or config.xau_liq_alert_enabled
            )
        else:
            config.xag_spread_alert_enabled = self.spread_enabled.isChecked()
            config.xag_liq_alert_enabled = self.liq_enabled.isChecked()
            config.xag_spread_alert_min = self.spread_min.value()
            config.xag_spread_alert_max = self.spread_max.value()
            config.xag_ba_liq_alert = self.ba_liq.value()
            config.xag_mt5_liq_alert = self.mt5_liq.value()
            config.xag_spread_sound_enabled = config.xag_spread_alert_enabled
            config.xag_liq_sound_enabled = config.xag_liq_alert_enabled
            config.xag_alert_sound_enabled = (
                config.xag_spread_alert_enabled or config.xag_liq_alert_enabled
            )

