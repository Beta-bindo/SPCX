"""Layout geometry and leverage persistence checks."""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from app.core.config import load_config, save_config
from app.core.models import AppConfig, ConnectionMode
from app.main_window import MainWindow


def main() -> int:
    errors: list[str] = []
    app = QApplication(sys.argv)
    window = MainWindow()
    window.resize(1400, 880)
    window.show()

    def verify() -> None:
        if not window.engine.is_running:
            window.start_btn.click()
        QApplication.processEvents()

        log = window.log_panel
        splitter = log.parent()
        top = splitter.widget(0)
        log_rect = log.geometry()
        top_rect = top.geometry()

        if abs(log_rect.width() - top_rect.width()) > 8:
            errors.append(
                f"log width {log_rect.width()} != top width {top_rect.width()}"
            )

        if window.gold_actions.spread_value._main.text() == "--":
            errors.append("xau spread not shown")

        window.config.xau_spread_alert_max = 50
        save_config(window.config)
        if load_config().xau_spread_alert_max != 50:
            errors.append("spread alert not saved")

        save_config(AppConfig(connection_mode=ConnectionMode.DEMO.value))

        if errors:
            print("LAYOUT TEST FAILED:")
            for err in errors:
                print(f"  - {err}")
            window.close()
            app.exit(1)
        else:
            print("LAYOUT TEST PASSED")
            window.close()
            app.exit(0)

    QTimer.singleShot(2000, verify)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
