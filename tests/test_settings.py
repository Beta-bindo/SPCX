"""Settings panel, config persistence, and alert logic tests."""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QCheckBox, QDialogButtonBox, QLabel, QScrollArea

from app.core.alerts import AlertService, AlertSoundKind
from app.core.config import load_config, save_config
from app.core.models import AppConfig, ConnectionMode, normalize_ba_refresh_interval, RiskSnapshot, SpreadSnapshot
from app.main_window import MainWindow
from app.widgets.config_panel import ConfigPanel
from app.widgets.connection_settings_dialog import ConnectionSettingsDialog
from app.widgets.symbol_ratio_fields import SymbolRatioFields
from app.widgets.trade_confirm_dialog import TradeConfirmDialog


def test_ba_refresh_interval_normalize():
    assert normalize_ba_refresh_interval(0.5) == 0.5
    assert normalize_ba_refresh_interval("0.8") == 0.8
    assert normalize_ba_refresh_interval(0.77) == 0.8
    assert normalize_ba_refresh_interval(None) == 0.8
    print("  ✓ BA 刷新间隔规范化")


def test_ba_refresh_interval_settings_roundtrip(tmp_path, monkeypatch):
    from app.core import config as config_module

    monkeypatch.setattr(config_module, "CONFIG_FILE", tmp_path / "config.json")
    cfg = AppConfig(ba_refresh_interval_sec=0.5)
    save_config(cfg)
    loaded = load_config()
    assert loaded.ba_refresh_interval_sec == 0.5

    app = QApplication.instance() or QApplication(sys.argv)
    panel = ConfigPanel(embedded=False)
    panel.load_config(loaded)
    assert panel.ba_refresh_interval.currentData() == 0.5
    panel.ba_refresh_interval.setCurrentIndex(panel.ba_refresh_interval.findData(0.3))
    out = panel.to_config()
    assert out.ba_refresh_interval_sec == 0.3
    print("  ✓ BA 刷新间隔设置面板往返")


def test_ratio_apply_and_preview():
    app = QApplication.instance() or QApplication(sys.argv)
    fields = SymbolRatioFields("xau", AppConfig())
    fields.ba_map.setValue(500)
    fields.mt5_map.setValue(1)
    fields.trade_lots.setValue(2)
    cfg = AppConfig()
    fields.apply_to(cfg)
    assert cfg.ba_quantity_for("xau") == 1000.0
    assert cfg.mt5_lot_for("xau") == 2.0
    assert "1000" in fields.preview.text()
    print("  ✓ 数量配比计算与预览")


def test_ratio_double_click_unlock_in_trade_dialog():
    app = QApplication.instance() or QApplication(sys.argv)
    dlg = TradeConfirmDialog("xau", AppConfig())
    dlg.show()
    app.processEvents()
    spin = dlg._ratio_fields.ba_map
    editor = spin.lineEdit()
    assert spin.is_locked()
    QTest.mouseDClick(editor, Qt.MouseButton.LeftButton, pos=editor.rect().center())
    app.processEvents()
    assert not spin.is_locked()
    assert editor.isReadOnly() is False
    QTest.keyClicks(editor, "650")
    app.processEvents()
    assert spin.value() == 650.0
    QTest.mouseClick(dlg, Qt.MouseButton.LeftButton, pos=dlg.rect().bottomLeft())
    app.processEvents()
    assert spin.is_locked()
    print("  ✓ 对冲弹窗数量双击编辑")


def test_alert_settings_double_click_edit():
    app = QApplication.instance() or QApplication(sys.argv)
    window = MainWindow()
    strip = window.gold_actions
    spin = strip.alert_settings.spread_min
    editor = spin.lineEdit()
    assert spin.is_locked()
    QTest.mouseDClick(editor, Qt.MouseButton.LeftButton, pos=editor.rect().center())
    app.processEvents()
    assert not spin.is_locked()
    QTest.keyClicks(editor, "2.5")
    app.processEvents()
    assert spin.value() == 2.5
    QTest.mouseClick(
        strip.spread_frame,
        Qt.MouseButton.LeftButton,
        pos=strip.spread_frame.rect().center(),
    )
    app.processEvents()
    assert spin.is_locked()
    window.close()
    print("  ✓ 告警设置数值框双击编辑")


def test_settings_load_apply_roundtrip():
    app = QApplication.instance() or QApplication(sys.argv)
    window = MainWindow()
    src = AppConfig(
        xag_spread_alert_min=-5,
        xag_spread_alert_max=0.5,
        xau_ba_liq_alert=80,
        xau_spread_alert_enabled=False,
        xau_liq_alert_enabled=False,
        xag_spread_alert_enabled=False,
        xag_liq_alert_enabled=False,
        xau_auto_contraction_enabled=True,
        xau_auto_contraction_threshold=2.5,
    )
    window.gold_actions.load_settings_from(src)
    window.silver_actions.load_settings_from(src)
    dst = AppConfig()
    window.gold_actions.apply_settings_to(dst)
    window.silver_actions.apply_settings_to(dst)
    assert dst.xag_spread_alert_min == -5
    assert dst.xag_spread_alert_max == 0.5
    assert dst.xau_ba_liq_alert == 80
    assert dst.xau_spread_alert_enabled is False
    assert dst.xag_spread_alert_enabled is False
    assert dst.xau_auto_contraction_enabled is True
    assert dst.xau_auto_contraction_threshold == 2.5
    assert dst.xau_auto_trade_hold_sec == 0.0  # hold 时间已移除，恒为 0（即时触发）
    window.close()
    print("  ✓ 设置面板 load/apply 往返")


def test_settings_save_via_main_window():
    app = QApplication.instance() or QApplication(sys.argv)
    window = MainWindow()
    window.config.ba_api_key = "persist-key"
    window.gold_actions.alert_settings.spread_max.setValue(42)
    window.gold_actions.alert_settings.spread_enabled.setChecked(True)
    window.silver_actions.alert_settings.spread_enabled.setChecked(True)
    window.gold_actions.alert_settings.spread_enabled.setChecked(False)
    window.gold_actions.alert_settings.liq_enabled.setChecked(False)
    window.save_btn.click()
    loaded = load_config()
    assert loaded.xau_spread_alert_max == 42
    assert loaded.ba_api_key == "persist-key"
    assert loaded.xau_spread_alert_enabled is False
    assert loaded.xag_spread_alert_enabled is True
    save_config(AppConfig(connection_mode=ConnectionMode.DEMO.value))
    window.close()
    print("  ✓ 主窗口保存合并设置字段")


def test_ratio_save_via_trade_dialog():
    app = QApplication.instance() or QApplication(sys.argv)
    window = MainWindow()
    dlg = TradeConfirmDialog("xau", window.config, None, window)
    dlg._ratio_fields.ba_map.setValue(650)
    dlg._ratio_fields.trade_lots.setValue(2)
    dlg.apply_ratio_to(window.config)
    window.save_btn.click()
    loaded = load_config()
    assert loaded.xau_ba_qty_map == 650
    assert loaded.xau_trade_lots == 2
    save_config(AppConfig(connection_mode=ConnectionMode.DEMO.value))
    window.close()
    print("  ✓ 数量配比在对冲弹窗保存")


def test_spread_alert_between_edges_is_silent():
    app = QApplication.instance() or QApplication(sys.argv)
    alerts = AlertService()
    fired: list[str] = []
    alerts.alert_triggered.connect(fired.append)

    cfg = AppConfig(
        xau_spread_alert_min=1.0,
        xau_spread_alert_max=3.0,
        xau_spread_alert_enabled=True,
        xag_spread_alert_enabled=False,
    )
    spreads = {
        "xau": SpreadSnapshot(preset_id="xau", mid_spread=2.0),
        "xag": SpreadSnapshot(preset_id="xag", mid_spread=99.0),
    }
    alerts.evaluate(cfg, spreads, RiskSnapshot())
    assert not fired
    assert not alerts._beep_timer.isActive()
    print("  ✓ 点差在上下限之间不响铃")


def test_spread_alert_at_or_beyond_edges():
    app = QApplication.instance() or QApplication(sys.argv)
    alerts = AlertService()
    fired: list[str] = []
    alerts.alert_triggered.connect(fired.append)

    cfg = AppConfig(
        xau_spread_alert_min=2.0,
        xau_spread_alert_max=4.0,
        xau_spread_alert_enabled=True,
    )
    spreads = {"xau": SpreadSnapshot(preset_id="xau", mid_spread=1.5)}
    alerts.evaluate(cfg, spreads, RiskSnapshot())
    assert fired
    assert "黄金" in fired[0]
    assert alerts._beep_timer.isActive()

    spreads_normal = {"xau": SpreadSnapshot(preset_id="xau", mid_spread=3.0)}
    alerts.evaluate(cfg, spreads_normal, RiskSnapshot())
    assert not alerts._beep_timer.isActive()

    spreads_upper_edge = {"xau": SpreadSnapshot(preset_id="xau", mid_spread=4.0)}
    alerts.evaluate(cfg, spreads_upper_edge, RiskSnapshot())
    assert alerts._beep_timer.isActive()

    alerts.evaluate(cfg, spreads_normal, RiskSnapshot())
    assert not alerts._beep_timer.isActive()

    spreads_lower_edge = {"xau": SpreadSnapshot(preset_id="xau", mid_spread=2.0)}
    alerts.evaluate(cfg, spreads_lower_edge, RiskSnapshot())
    assert alerts._beep_timer.isActive()
    print("  ✓ 点差达到或越过上下限告警，回到中间立即停声")


def test_liq_alert_skips_no_position():
    app = QApplication.instance() or QApplication(sys.argv)
    alerts = AlertService()
    fired: list[str] = []
    alerts.alert_triggered.connect(fired.append)

    cfg = AppConfig(xau_ba_liq_alert=100, xau_liq_alert_enabled=True)
    risk = RiskSnapshot(xau_ba_liq=99999.0)
    alerts.evaluate(cfg, {}, risk)
    assert not fired
    print("  ✓ 无持仓时不触发爆仓告警")


def test_liq_alert_clears_above_threshold():
    app = QApplication.instance() or QApplication(sys.argv)
    alerts = AlertService()
    cfg = AppConfig(xau_ba_liq_alert=100, xau_liq_alert_enabled=True)
    alerts.evaluate(cfg, {}, RiskSnapshot(xau_ba_liq=50.0))
    assert alerts._beep_timer.isActive()
    alerts.evaluate(cfg, {}, RiskSnapshot(xau_ba_liq=150.0))
    assert not alerts._beep_timer.isActive()


def test_liq_alert_reenters_threshold():
    app = QApplication.instance() or QApplication(sys.argv)
    alerts = AlertService()
    cfg = AppConfig(xau_ba_liq_alert=100, xau_liq_alert_enabled=True)
    alerts.evaluate(cfg, {}, RiskSnapshot(xau_ba_liq=50.0))
    assert alerts._beep_timer.isActive()
    alerts.evaluate(cfg, {}, RiskSnapshot(xau_ba_liq=150.0))
    assert not alerts._beep_timer.isActive()
    alerts.evaluate(cfg, {}, RiskSnapshot(xau_ba_liq=100.0))
    assert alerts._beep_timer.isActive()


def test_liq_alert_reacts_to_threshold_change():
    app = QApplication.instance() or QApplication(sys.argv)
    alerts = AlertService()
    risk = RiskSnapshot(xau_ba_liq=120.0)
    cfg_normal = AppConfig(xau_ba_liq_alert=100, xau_liq_alert_enabled=True)
    alerts.evaluate(cfg_normal, {}, risk)
    assert not alerts._beep_timer.isActive()

    cfg_warning = AppConfig(xau_ba_liq_alert=150, xau_liq_alert_enabled=True)
    alerts.evaluate(cfg_warning, {}, risk)
    assert alerts._beep_timer.isActive()


def test_contraction_direction_labels_per_platform():
    from app.core.trading_service import hedge_strategy_label_for_platform
    from app.core.models import HedgeMode

    assert hedge_strategy_label_for_platform("BA", HedgeMode.CONTRACTION.value) == "收缩"
    assert hedge_strategy_label_for_platform("MT5", HedgeMode.CONTRACTION.value) == "扩张"
    assert hedge_strategy_label_for_platform("BA", HedgeMode.EXPANSION.value) == "扩张"
    assert hedge_strategy_label_for_platform("MT5", HedgeMode.EXPANSION.value) == "收缩"


def test_liq_alert_when_below_threshold():
    app = QApplication.instance() or QApplication(sys.argv)
    alerts = AlertService()
    fired: list[str] = []
    alerts.alert_triggered.connect(fired.append)

    cfg = AppConfig(xau_ba_liq_alert=100, xau_liq_alert_enabled=True)
    risk = RiskSnapshot(xau_ba_liq=50.0)
    alerts.evaluate(cfg, {}, risk)
    assert fired
    assert "黄金 BA" in fired[0]
    print("  ✓ 爆仓缓冲低于阈值时告警")


def test_spread_alert_disabled_when_unchecked():
    app = QApplication.instance() or QApplication(sys.argv)
    alerts = AlertService()
    fired: list[str] = []
    alerts.alert_triggered.connect(fired.append)

    cfg = AppConfig(
        xau_spread_alert_min=1.0,
        xau_spread_alert_max=3.0,
        xau_spread_alert_enabled=False,
    )
    spreads = {"xau": SpreadSnapshot(preset_id="xau", mid_spread=2.0)}
    alerts.evaluate(cfg, spreads, RiskSnapshot())
    assert not fired
    print("  ✓ 未勾选点差时不告警")


def test_negative_spread_range():
    app = QApplication.instance() or QApplication(sys.argv)
    alerts = AlertService()
    fired: list[str] = []
    alerts.alert_triggered.connect(fired.append)

    cfg = AppConfig(
        xag_spread_alert_min=-2.0,
        xag_spread_alert_max=1.0,
        xag_spread_alert_enabled=True,
    )
    spreads = {"xag": SpreadSnapshot(preset_id="xag", mid_spread=-3.5)}
    alerts.evaluate(cfg, spreads, RiskSnapshot())
    assert fired
    print("  ✓ 负数点差触及预警边界告警")


def test_per_symbol_alert_independent():
    app = QApplication.instance() or QApplication(sys.argv)
    alerts = AlertService()
    fired: list[str] = []
    alerts.alert_triggered.connect(fired.append)

    cfg = AppConfig(
        xau_spread_alert_min=1.0,
        xau_spread_alert_max=3.0,
        xag_spread_alert_min=-2.0,
        xag_spread_alert_max=1.0,
        xau_spread_alert_enabled=True,
        xag_spread_alert_enabled=False,
    )
    spreads = {
        "xau": SpreadSnapshot(preset_id="xau", mid_spread=5.0),
        "xag": SpreadSnapshot(preset_id="xag", mid_spread=-1.0),
    }
    alerts.evaluate(cfg, spreads, RiskSnapshot())
    assert len(fired) == 1
    assert "黄金" in fired[0]
    assert "白银" not in fired[0]
    print("  ✓ 各品种点差告警独立（仅黄金开启时白银不响）")


def test_disabling_alert_stops_active_beep():
    app = QApplication.instance() or QApplication(sys.argv)
    alerts = AlertService()
    cfg_on = AppConfig(
        xau_spread_alert_min=1.0,
        xau_spread_alert_max=3.0,
        xau_spread_alert_enabled=True,
    )
    spreads = {"xau": SpreadSnapshot(preset_id="xau", mid_spread=5.0)}
    alerts.evaluate(cfg_on, spreads, RiskSnapshot())
    assert alerts._beep_timer.isActive()

    cfg_off = AppConfig(
        xau_spread_alert_enabled=False,
        xau_liq_alert_enabled=False,
        xag_spread_alert_enabled=False,
        xag_liq_alert_enabled=False,
    )
    alerts.evaluate(cfg_off, spreads, RiskSnapshot())
    assert not alerts._beep_timer.isActive()
    print("  ✓ 关闭告警立即停止当前响铃")


def test_main_window_alert_checkbox_stops_beep():
    app = QApplication.instance() or QApplication(sys.argv)
    window = MainWindow()
    window.engine.alerts._start_continuous_beep(AlertSoundKind.SPREAD)
    assert window.engine.alerts._beep_timer.isActive()

    window.gold_actions.alert_settings.spread_enabled.setChecked(False)
    window.gold_actions.alert_settings.liq_enabled.setChecked(False)
    assert not window.engine.alerts._beep_timer.isActive()
    window.close()
    print("  ✓ UI 关闭告警立即停声")


def test_main_window_alert_checkboxes_do_not_sync():
    app = QApplication.instance() or QApplication(sys.argv)
    window = MainWindow()
    gold = window.gold_actions.alert_settings.spread_enabled
    silver = window.silver_actions.alert_settings.spread_enabled

    gold.setChecked(True)
    silver.setChecked(False)
    assert gold.isChecked()
    assert not silver.isChecked()

    silver.setChecked(True)
    assert gold.isChecked()
    assert silver.isChecked()
    gold.setChecked(False)
    assert not gold.isChecked()
    assert silver.isChecked()
    window.close()
    print("  ✓ 黄金/白银点差勾选互不同步")


def test_spread_alert_reenters_warning_edge():
    app = QApplication.instance() or QApplication(sys.argv)
    alerts = AlertService()
    cfg = AppConfig(
        xau_spread_alert_min=1.0,
        xau_spread_alert_max=2.5,
        xau_spread_alert_enabled=True,
    )
    warning = {"xau": SpreadSnapshot(preset_id="xau", mid_spread=3.0)}
    normal = {"xau": SpreadSnapshot(preset_id="xau", mid_spread=1.5)}
    alerts.evaluate(cfg, warning, RiskSnapshot())
    assert alerts._beep_timer.isActive()
    alerts.evaluate(cfg, normal, RiskSnapshot())
    assert not alerts._beep_timer.isActive()
    alerts.evaluate(cfg, warning, RiskSnapshot())
    assert alerts._beep_timer.isActive()
    print("  ✓ 回到中间停声，再次触及边界重新响铃")


def test_spread_alert_reacts_to_threshold_change():
    app = QApplication.instance() or QApplication(sys.argv)
    alerts = AlertService()
    spreads = {"xau": SpreadSnapshot(preset_id="xau", mid_spread=2.0)}
    cfg_normal = AppConfig(
        xau_spread_alert_min=1.0,
        xau_spread_alert_max=3.0,
        xau_spread_alert_enabled=True,
    )
    alerts.evaluate(cfg_normal, spreads, RiskSnapshot())
    assert not alerts._beep_timer.isActive()

    cfg_warning = AppConfig(
        xau_spread_alert_min=2.5,
        xau_spread_alert_max=3.0,
        xau_spread_alert_enabled=True,
    )
    alerts.evaluate(cfg_warning, spreads, RiskSnapshot())
    assert alerts._beep_timer.isActive()
    print("  ✓ 调整阈值后当前点差触及边界会立即响铃")


def test_spread_alert_rings_without_time_limit():
    app = QApplication.instance() or QApplication(sys.argv)
    alerts = AlertService()
    cfg = AppConfig(
        xau_spread_alert_min=1.0,
        xau_spread_alert_max=2.5,
        xau_spread_alert_enabled=True,
    )
    spreads = {"xau": SpreadSnapshot(preset_id="xau", mid_spread=3.0)}
    alerts.evaluate(cfg, spreads, RiskSnapshot())
    assert alerts._ringing
    for _ in range(40):
        alerts._tick_beep()
    assert alerts._ringing
    assert alerts._beep_timer.isActive()
    print("  ✓ 触及预警边界后持续响铃不会自动超时停止")


def test_spread_alert_user_range_1_to_2_5():
    """User scenario: alert when spread <= 1 or >= 2.5; silent between edges."""
    app = QApplication.instance() or QApplication(sys.argv)
    alerts = AlertService()
    fired: list[str] = []
    alerts.alert_triggered.connect(fired.append)

    cfg = AppConfig(
        xau_spread_alert_min=1.0,
        xau_spread_alert_max=2.5,
        xau_spread_alert_enabled=True,
    )
    risk = RiskSnapshot()

    for spread, should_alert in ((0.5, True), (1.0, True), (1.5, False), (2.5, True), (3.2, True)):
        fired.clear()
        alerts.evaluate(
            cfg,
            {"xau": SpreadSnapshot(preset_id="xau", mid_spread=spread)},
            risk,
        )
        if should_alert:
            assert alerts._beep_timer.isActive(), f"spread={spread} should alert"
        else:
            assert not alerts._beep_timer.isActive(), f"spread={spread} should be silent"
    print("  ✓ 点差 <=1 或 >=2.5 告警，中间不响")


def test_trade_dialog_buttons_and_gold_order_mode():
    app = QApplication.instance() or QApplication(sys.argv)

    gold = TradeConfirmDialog("xau", AppConfig(connection_mode=ConnectionMode.DEMO.value))
    gold._apply_action("开仓", "contraction")
    assert gold.gold_order_mode() == "maker"
    assert gold._order_mode_market is not None
    gold._order_mode_market.setChecked(True)
    assert gold.gold_order_mode() == "market"
    action_btns = [btn.text() for btn in gold._action_buttons]
    assert any("开仓收缩" in t for t in action_btns)

    silver = TradeConfirmDialog("xag", AppConfig(connection_mode=ConnectionMode.DEMO.value))
    assert silver.gold_order_mode() == "market"
    assert silver._order_mode_maker is not None
    silver._order_mode_maker.setChecked(True)
    assert silver.gold_order_mode() == "maker"
    silver_actions = [btn.text() for btn in silver._action_buttons]
    assert any("开仓" in t for t in silver_actions)
    print("  ✓ 交易确认弹窗操作按钮与下单模式")


def test_connection_dialog_buttons_are_chinese():
    app = QApplication.instance() or QApplication(sys.argv)
    dlg = ConnectionSettingsDialog(AppConfig())
    buttons = dlg.findChild(QDialogButtonBox)
    assert buttons.button(QDialogButtonBox.StandardButton.Save).text() == "保存并关闭"
    assert buttons.button(QDialogButtonBox.StandardButton.Cancel).text() == "取消"
    print("  ✓ 连接设置弹窗按钮中文")


def test_connection_dialog_demo_mode_allows_credentials():
    app = QApplication.instance() or QApplication(sys.argv)
    dlg = ConnectionSettingsDialog(AppConfig(connection_mode=ConnectionMode.DEMO.value))
    panel = dlg._panel
    assert panel.ba_api_key.isEnabled()
    assert panel.ba_api_secret.isEnabled()
    assert panel.mt5_login.isEnabled()
    panel.ba_api_key.setText("demo-fill-key")
    assert panel.ba_api_key.text() == "demo-fill-key"
    scroll = panel.findChild(QScrollArea, "connectionSettingsScroll")
    assert scroll is not None
    assert panel.ba_card.isEnabled()
    assert panel.mt5_card.isEnabled()
    print("  ✓ 演示模式仍可填写凭证且纵向滚动布局")


def test_ui_flat_sections_exist():
    app = QApplication.instance() or QApplication(sys.argv)
    window = MainWindow()
    assert window.gold_actions.alert_settings.spread_min is not None
    assert window.gold_actions.alert_settings.ba_liq is not None
    assert window.gold_actions.auto_trade_settings.contraction_enabled is not None
    assert window.settings_btn is not None
    window.close()
    print("  ✓ 平铺设置区与设置按钮")


def main() -> int:
    errors: list[str] = []
    tests = [
        test_ratio_apply_and_preview,
        test_ratio_double_click_unlock_in_trade_dialog,
        test_alert_settings_double_click_edit,
        test_settings_load_apply_roundtrip,
        test_settings_save_via_main_window,
        test_ratio_save_via_trade_dialog,
        test_spread_alert_between_edges_is_silent,
        test_spread_alert_at_or_beyond_edges,
        test_liq_alert_skips_no_position,
        test_liq_alert_when_below_threshold,
        test_liq_alert_clears_above_threshold,
        test_liq_alert_reenters_threshold,
        test_liq_alert_reacts_to_threshold_change,
        test_spread_alert_disabled_when_unchecked,
        test_negative_spread_range,
        test_per_symbol_alert_independent,
        test_disabling_alert_stops_active_beep,
        test_main_window_alert_checkbox_stops_beep,
        test_main_window_alert_checkboxes_do_not_sync,
        test_spread_alert_reenters_warning_edge,
        test_spread_alert_reacts_to_threshold_change,
        test_spread_alert_rings_without_time_limit,
        test_spread_alert_user_range_1_to_2_5,
        test_trade_dialog_buttons_and_gold_order_mode,
        test_connection_dialog_buttons_are_chinese,
        test_connection_dialog_demo_mode_allows_credentials,
        test_ui_flat_sections_exist,
    ]
    for fn in tests:
        try:
            fn()
        except Exception as exc:
            errors.append(f"{fn.__name__}: {exc}")

    if errors:
        print("SETTINGS TEST FAILED:")
        for err in errors:
            print(f"  - {err}")
        return 1
    print("ALL SETTINGS TESTS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
