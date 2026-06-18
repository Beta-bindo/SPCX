from __future__ import annotations

import os
import sys

# 禁用系统代理自动探测，避免 Windows 弹出代理认证小窗后迅速消失
os.environ.setdefault("NO_PROXY", "*")
os.environ.setdefault("no_proxy", "*")

# SSL 证书必须在 requests / binance 之前初始化（PyInstaller 单文件 exe）
from app.core.ssl_certs import ensure_ca_bundle

ensure_ca_bundle()

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from app.core.branding import APP_NAME, apply_app_branding
from app.core.config import load_config
from app.core.license.service import LicenseService
from app.core.single_instance import acquire_single_instance
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
        help="演示模式载入单边对冲预览持仓（黄金仅BA、SPCXUSDT仅Ex）",
    )
    parser.add_argument(
        "--demo-seed-mixed",
        action="store_true",
        help="演示模式载入混合告警预览（黄金数量不齐、SPCXUSDT方向异常）",
    )
    args = parser.parse_args()

    app = QApplication(sys.argv)
    apply_app_branding(app)
    instance_lock = acquire_single_instance(APP_NAME)
    if instance_lock is None:
        return 0
    app._instance_lock = instance_lock  # 保持锁文件存活，防止被 GC 释放

    config = load_config()
    load_stylesheet(app, config.theme)

    license_service = LicenseService()
    if LICENSE_REQUIRED:
        from app.widgets.license_gate import ensure_license_approved

        if ensure_license_approved(service=license_service) is None:
            return 0
    else:
        # 免授权版：启动后延迟静默注册+心跳，日常仍由 10 分钟定时器续期
        license_service.start_heartbeat(flush=False, defer_retry_min=30)
        QTimer.singleShot(5000, license_service.ensure_reporting_ready)

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
