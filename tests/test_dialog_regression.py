"""Regression: settings dialog must not break header save after close."""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from app.main_window import MainWindow
from app.widgets.connection_settings_dialog import ConnectionSettingsDialog


def main() -> int:
    app = QApplication(sys.argv)
    window = MainWindow()
    dlg = ConnectionSettingsDialog(window.config, window)
    dlg._panel.ba_api_key.setText("after-dialog-key")
    dlg.apply_connection_to(window.config)
    del dlg

    window.gold_actions.alert_settings.spread_max.setValue(7)
    window.save_btn.click()
    assert window.config.ba_api_key == "after-dialog-key"
    assert window.config.xau_spread_alert_max == 7
    window.close()
    print("DIALOG SAVE REGRESSION PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
