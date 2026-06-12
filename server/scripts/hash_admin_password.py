from __future__ import annotations

import getpass
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.auth import hash_admin_password


def main() -> int:
    password = getpass.getpass("Admin password: ")
    confirm = getpass.getpass("Confirm password: ")
    if password != confirm:
        print("Passwords do not match", file=sys.stderr)
        return 1
    if len(password) < 12:
        print("Password must be at least 12 characters", file=sys.stderr)
        return 1
    print(hash_admin_password(password))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
