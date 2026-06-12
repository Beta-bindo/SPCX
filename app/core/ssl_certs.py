"""Ensure requests/urllib3 can find CA certificates in dev and PyInstaller builds."""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path
from typing import Optional

from app.core.paths import user_data_dir

_cached_bundle: Optional[str] = None
_patched = False


def _resource_cert_path() -> Optional[Path]:
    if getattr(sys, "frozen", False):
        meipass = Path(sys._MEIPASS)
        bundled = meipass / "app" / "resources" / "cacert.pem"
        if bundled.is_file() and bundled.stat().st_size > 1000:
            return bundled
        legacy = meipass / "certifi" / "cacert.pem"
        if legacy.is_file() and legacy.stat().st_size > 1000:
            return legacy
    dev_copy = Path(__file__).resolve().parent.parent / "resources" / "cacert.pem"
    if dev_copy.is_file() and dev_copy.stat().st_size > 1000:
        return dev_copy
    try:
        import certifi

        candidate = Path(certifi.where())
        if candidate.is_file() and candidate.stat().st_size > 1000:
            return candidate
    except ImportError:
        pass
    return None


def _patch_certifi(bundle_path: str) -> None:
    global _patched
    if _patched:
        return
    _patched = True

    def _where() -> str:
        return bundle_path

    try:
        import certifi

        certifi.where = _where  # type: ignore[method-assign]
        import certifi.core as core

        core.where = _where  # type: ignore[method-assign]
        if hasattr(core, "_CACERT_PATH"):
            core._CACERT_PATH = bundle_path
    except Exception:
        pass

    try:
        import requests.certs as req_certs

        req_certs.where = _where  # type: ignore[method-assign]
    except Exception:
        pass


def ensure_ca_bundle() -> str:
    """Return stable CA bundle path; copy out of _MEI temp when packaged."""
    global _cached_bundle
    if _cached_bundle:
        cached = Path(_cached_bundle)
        if cached.is_file() and cached.stat().st_size > 1000:
            return _cached_bundle

    target = user_data_dir() / "cacert.pem"
    if target.is_file() and target.stat().st_size > 1000:
        bundle = str(target)
    else:
        source = _resource_cert_path()
        if source is None:
            raise RuntimeError(
                "无法加载 SSL 根证书。请重新下载 TradeAssistant.exe 或联系管理员"
            )
        shutil.copyfile(source, target)
        bundle = str(target)

    os.environ["SSL_CERT_FILE"] = bundle
    os.environ["REQUESTS_CA_BUNDLE"] = bundle
    os.environ["CURL_CA_BUNDLE"] = bundle
    _patch_certifi(bundle)
    _cached_bundle = bundle
    return bundle
