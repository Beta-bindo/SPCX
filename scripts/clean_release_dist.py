"""Remove sensitive sidecar files from dist before shipping."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"

SENSITIVE_NAMES = {
    "config.json",
    "license.json",
    ".env",
    "build_log.txt",
}


def main() -> int:
    removed: list[str] = []
    if not DIST.is_dir():
        print("clean_release_dist: dist/ 不存在，跳过")
        return 0
    for path in DIST.rglob("*"):
        if path.is_file() and path.name in SENSITIVE_NAMES:
            path.unlink()
            removed.append(str(path.relative_to(ROOT)))
    for name in removed:
        print(f"removed: {name}")
    print("CLEAN RELEASE DIST OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
