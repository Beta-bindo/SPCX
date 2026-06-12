"""PyInstaller runtime hook: stable SSL CA path before any HTTPS import."""

import os
import shutil
import sys


def _find_source_bundle(meipass: str) -> str:
    candidates = [
        os.path.join(meipass, "app", "resources", "cacert.pem"),
        os.path.join(meipass, "certifi", "cacert.pem"),
    ]
    for path in candidates:
        if os.path.isfile(path) and os.path.getsize(path) > 1000:
            return path
    return ""


def _bootstrap_ssl() -> None:
    if not getattr(sys, "frozen", False):
        return

    meipass = getattr(sys, "_MEIPASS", "")
    src = _find_source_bundle(meipass)
    dest_dir = os.path.join(os.path.expanduser("~"), ".xau_assistant")
    os.makedirs(dest_dir, exist_ok=True)
    dest = os.path.join(dest_dir, "cacert.pem")

    bundle = dest
    if src:
        try:
            if (not os.path.isfile(dest)) or os.path.getsize(dest) < 1000:
                shutil.copyfile(src, dest)
        except OSError:
            bundle = src
    elif os.path.isfile(dest) and os.path.getsize(dest) > 1000:
        bundle = dest
    else:
        return

    os.environ["SSL_CERT_FILE"] = bundle
    os.environ["REQUESTS_CA_BUNDLE"] = bundle
    os.environ["CURL_CA_BUNDLE"] = bundle


_bootstrap_ssl()
