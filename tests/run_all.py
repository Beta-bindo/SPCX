#!/usr/bin/env python3
"""Run full test battery."""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TESTS = [
    "tests/test_pnl.py",
    "tests/test_connectors.py",
    "tests/test_profit_export.py",
    "tests/test_ui_idempotency.py",
    "tests/test_layout.py",
    "tests/test_dialog_regression.py",
    "tests/test_settings.py",
    "tests/test_auto_trade.py",
    "tests/test_theme.py",
    "tests/test_comprehensive.py",
    "tests/test_data_correctness.py",
    "tests/test_regression.py",
    "tests/test_smoke.py",
]

PY = ROOT / ".venv" / "bin" / "python"


def main() -> int:
    failed: list[str] = []
    for rel in TESTS:
        path = ROOT / rel
        print(f"\n>>> {rel}")
        r = subprocess.run([str(PY), str(path)], cwd=ROOT)
        if r.returncode != 0:
            failed.append(rel)
    print("\n" + "=" * 60)
    if failed:
        print(f"FAILED ({len(failed)}): {', '.join(failed)}")
        return 1
    print(f"ALL {len(TESTS)} SUITES PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
