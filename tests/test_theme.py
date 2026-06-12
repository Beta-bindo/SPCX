"""Theme switch smoke test."""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from app.core.theme import load_stylesheet, stylesheet_path
from app.main_window import MainWindow
from app.widgets.config_panel import ConfigPanel


def main() -> int:
    errors: list[str] = []
    app = QApplication(sys.argv)

    for theme in ("dark", "light"):
        p = stylesheet_path(theme)
        if not p.exists():
            errors.append(f"missing theme file: {p}")

    window = MainWindow()
    window.show()
    config_panel = ConfigPanel(embedded=False)
    window.start_btn.click()

    def toggle_themes():
        for theme in ("light", "dark", "light"):
            idx = window.theme_combo.findData(theme)
            window.theme_combo.setCurrentIndex(idx)
            QApplication.processEvents()
            ss = app.styleSheet()
            if theme == "light" and "#f1f5f9" not in ss:
                errors.append("light theme stylesheet not applied to app")
            if theme == "dark" and "#0b0f14" not in ss:
                errors.append("dark theme stylesheet not applied to app")
            local = config_panel.ba_card.styleSheet()
            if local:
                errors.append(f"ba_card local stylesheet after {theme}: {local!r}")

        if errors:
            print("THEME TEST FAILED:")
            for e in errors:
                print(f"  - {e}")
            window.close()
            app.exit(1)
        else:
            print("THEME TEST PASSED")
            window.close()
            app.exit(0)

    QTimer.singleShot(2000, toggle_themes)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
