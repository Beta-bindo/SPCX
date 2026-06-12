"""点差大字展示控件：主数字 + 第三位小数以右上角小字呈现，并按正负着色。"""

from __future__ import annotations

from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QFont, QFontMetrics
from PySide6.QtWidgets import QHBoxLayout, QLabel, QSizePolicy, QWidget

from app.core.theme import polish_widget, ui_mono_font


class SpreadValueLabel(QWidget):
    """点差主数字 + 第三位小数右上角小字（正数热色、负数冷色）。"""

    _MAIN_PX = 36
    _SUP_PX = 14
    _MAX_MAIN_TEXT = "+10.08"

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("spreadStripValueWrap")
        self.setAutoFillBackground(False)
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        self._tone: str | None = None
        self._last_main = ""
        self._last_sup = ""

        self._main = QLabel("--")
        self._main.setObjectName("spreadStripValueMain")
        self._main.setAutoFillBackground(False)
        self._main.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self._main.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)

        self._sup = QLabel("")
        self._sup.setObjectName("spreadStripValueSup")
        self._sup.setAutoFillBackground(False)
        self._sup.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        self._sup.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)

        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        row.addWidget(
            self._main,
            0,
            Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignLeft,
        )
        row.addWidget(
            self._sup,
            0,
            Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft,
        )

        self._apply_fonts()
        self._sync_height()

    def _display_font(self) -> QFont:
        """与 QSS spreadStripValueMain 一致，用于度量。"""
        font = QFont("SimSun")
        if not font.exactMatch():
            font = QFont("宋体")
        if not font.exactMatch():
            font = ui_mono_font(pixel_size=self._MAIN_PX, weight=QFont.Weight.ExtraBold)
        else:
            font.setPixelSize(self._MAIN_PX)
            font.setWeight(QFont.Weight.ExtraBold)
        return font

    def _superscript_font(self) -> QFont:
        font = QFont("SimSun")
        if not font.exactMatch():
            font = QFont("宋体")
        if not font.exactMatch():
            font = ui_mono_font(pixel_size=self._SUP_PX, weight=QFont.Weight.ExtraBold)
        else:
            font.setPixelSize(self._SUP_PX)
            font.setWeight(QFont.Weight.ExtraBold)
        return font

    def _apply_fonts(self) -> None:
        self._main.setFont(self._display_font())
        self._sup.setFont(self._superscript_font())

    def _sync_height(self) -> None:
        main_h = QFontMetrics(self._display_font()).height()
        self.setFixedHeight(main_h)
        self.updateGeometry()

    def reserve_width(self) -> int:
        """预留最大点差宽度，供外层 grid 列宽计算。"""
        main_m = QFontMetrics(self._display_font())
        sup_m = QFontMetrics(self._sup.font())
        return (
            main_m.horizontalAdvance(self._MAX_MAIN_TEXT)
            + sup_m.horizontalAdvance("9")
            + 2
        )

    def width(self) -> int:
        return self.reserve_width()

    def sizeHint(self) -> QSize:
        main_m = QFontMetrics(self._main.font())
        sup_m = QFontMetrics(self._sup.font())
        main_w = main_m.horizontalAdvance(self._main.text() or self._MAX_MAIN_TEXT)
        sup_w = sup_m.horizontalAdvance(self._sup.text()) if self._sup.text() else 0
        return QSize(main_w + sup_w + (1 if sup_w else 0), main_m.height())

    def _set_tone(self, tone: str | None) -> None:
        if tone == self._tone:
            return
        self._tone = tone
        hot = tone == "hot"
        cold = tone == "cold"
        for lbl in (self._main, self._sup):
            lbl.setProperty("spreadHot", "true" if hot else "false")
            lbl.setProperty("spreadCold", "true" if cold else "false")
        polish_widget(self._main)
        polish_widget(self._sup)

    def set_spread(self, spread: float | None) -> None:
        """更新显示的点差值；None 显示"--"。整数/前两位为主字，第三位小数为上标小字。"""
        if spread is None:
            if self._last_main != "--":
                self._main.setText("--")
                self._last_main = "--"
            if self._last_sup:
                self._sup.setText("")
                self._last_sup = ""
            self._set_tone(None)
            self.updateGeometry()
            return

        sign = "+" if spread >= 0 else "-"
        digits = f"{abs(spread):.3f}"
        whole, frac = digits.split(".")
        main_text = f"{sign}{whole}.{frac[:2]}"
        sup_text = frac[2]
        if main_text != self._last_main:
            self._main.setText(main_text)
            self._last_main = main_text
        if sup_text != self._last_sup:
            self._sup.setText(sup_text)
            self._last_sup = sup_text
        self._set_tone("hot" if spread >= 0 else "cold")
        self.updateGeometry()

    def refresh_theme(self) -> None:
        """主题切换后重设字体并刷新着色。"""
        self._apply_fonts()
        self._sync_height()
        tone = self._tone
        self._tone = None
        self._set_tone(tone)
