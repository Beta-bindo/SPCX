from __future__ import annotations

import sys

# SSL 证书必须在 requests / binance 之前初始化（PyInstaller 单文件 exe）
from app.core.ssl_certs import ensure_ca_bundle

ensure_ca_bundle()

from PySide6.QtWidgets import QApplication

from app.core.branding import APP_NAME, apply_app_branding
from app.core.config import load_config
from app.core.license.service import LicenseService
from app.core.theme import load_stylesheet
from app.main_window import MainWindow

try:
    from app.core.build_config import LICENSE_REQUIRED
except ImportError:
    LICENSE_REQUIRED = True


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description=APP_NAME)
    parser.add_argument(
        "--demo-seed",
        action="store_true",
        help="演示模式载入单边对冲预览持仓（黄金仅BA、白银仅Ex）",
    )
    parser.add_argument(
        "--demo-seed-mixed",
        action="store_true",
        help="演示模式载入混合告警预览（黄金数量不齐、白银方向异常）",
    )
    args = parser.parse_args()

    app = QApplication(sys.argv)
    apply_app_branding(app)
    config = load_config()
    load_stylesheet(app, config.theme)

    license_service = LicenseService()
    if LICENSE_REQUIRED:
        from app.widgets.license_gate import ensure_license_approved

        if ensure_license_approved(license_service=license_service) is None:
            return 0
    else:
        # 免授权版：启动阶段零联网；首次成交或 10 分钟定时器再上报
        license_service.start_heartbeat(flush=False, defer_retry_min=30)

    window = MainWindow(
        license_service=license_service,
        demo_seed=args.demo_seed,
        demo_seed_mixed=args.demo_seed_mixed,
    )
    window.setWindowTitle(APP_NAME)
    if not app.windowIcon().isNull():
        window.setWindowIcon(app.windowIcon())
    window.present()

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
