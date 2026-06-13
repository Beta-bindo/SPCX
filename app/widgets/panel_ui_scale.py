"""中间栏板块 UI 缩放：字体与勾选框尺寸。"""

from __future__ import annotations

DEFAULT_PANEL_FONT_PT = 10
DEFAULT_PANEL_CHECK_PX = 18
MIN_PANEL_FONT_PT = 8
MAX_PANEL_FONT_PT = 24
MIN_PANEL_CHECK_PX = 14
MAX_PANEL_CHECK_PX = 36


def build_panel_section_qss(font_pt: int, check_px: int) -> str:
    """生成作用于单个板块容器及其子控件的样式表。"""
    touch_h = max(22, check_px + 6)
    spacing = max(4, check_px // 3)
    spin_h = max(18, check_px + 2)
    return f"""
QLabel#fieldLabel,
QLabel#fieldHint,
QLabel#rangeSep,
QLabel#settingsBlockTitle,
QLabel#platformTag,
QLabel#positionStatus,
QLabel#pendingHint,
QLabel#riskHint,
QLabel#mt5PlatformTag {{
    font-size: {font_pt}pt;
}}
QLabel#fieldLabel[pendingActive="true"] {{
    color: #e67e22;
}}
QCheckBox#settingsCheck {{
    font-size: {font_pt}pt;
    min-height: {touch_h}px;
    spacing: {spacing}px;
}}
QCheckBox#settingsCheck::indicator {{
    width: {check_px}px;
    height: {check_px}px;
}}
QDoubleSpinBox#settingsSpin[inline="true"],
QSpinBox#settingsSpin[inline="true"] {{
    font-size: {font_pt}pt;
    min-height: {spin_h}px;
    max-height: {spin_h}px;
}}
"""


def clamp_font_pt(value: int) -> int:
    return max(MIN_PANEL_FONT_PT, min(MAX_PANEL_FONT_PT, int(value)))


def clamp_check_px(value: int) -> int:
    return max(MIN_PANEL_CHECK_PX, min(MAX_PANEL_CHECK_PX, int(value)))
