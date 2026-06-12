"""Copy certifi CA bundle into app/resources for PyInstaller packaging."""
from __future__ import annotations

import shutil
from pathlib import Path

import certifi


def main() -> None:
    target = Path(__file__).resolve().parent.parent / "app" / "resources" / "cacert.pem"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(certifi.where(), target)
    print(f"copied {target} ({target.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
