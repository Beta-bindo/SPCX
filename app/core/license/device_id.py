from __future__ import annotations

import hashlib
import os
import sys
import uuid


def get_device_id() -> str:
    parts: list[str] = []
    if sys.platform == "win32":
        try:
            import winreg

            key = winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"SOFTWARE\Microsoft\Cryptography",
            )
            guid, _ = winreg.QueryValueEx(key, "MachineGuid")
            winreg.CloseKey(key)
            parts.append(str(guid))
        except OSError:
            pass
    parts.append(str(uuid.getnode()))
    parts.append(os.environ.get("COMPUTERNAME", ""))
    raw = "|".join(parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]
