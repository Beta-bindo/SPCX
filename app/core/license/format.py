"""授权到期时间的桌面端展示格式。"""

from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

_BJ = ZoneInfo("Asia/Shanghai")


def format_license_expires_label(expires_at: str | None) -> str:
    """格式化为「授权到 YYYY-MM-DD HH：MM：SS」（北京时间）；无到期则永久。"""
    if not expires_at or not str(expires_at).strip():
        return "授权到：永久"
    raw = str(expires_at).strip()
    try:
        if "T" in raw:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        else:
            dt = datetime.fromisoformat(raw.replace(" ", "T"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        bj = dt.astimezone(_BJ)
        return f"授权到 {bj.strftime('%Y-%m-%d %H：%M：%S')}"
    except ValueError:
        cleaned = raw.replace("T", " ").replace("+00:00", "").strip()
        return f"授权到 {cleaned}"
