"""Fail release build if the exe embeds local BA / Exness credentials."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core.config import CONFIG_FILE, load_config
from app.core.secret_store import unprotect_secret


def _credential_needles() -> list[bytes]:
    needles: list[bytes] = []
    if not CONFIG_FILE.is_file():
        return needles
    try:
        cfg = load_config()
    except Exception:
        return needles
    for value in (
        cfg.ba_api_key.strip(),
        unprotect_secret(cfg.ba_api_secret).strip(),
        str(cfg.mt5_login).strip() if cfg.mt5_login else "",
        unprotect_secret(cfg.mt5_password).strip(),
        cfg.mt5_server.strip(),
    ):
        if len(value) >= 6:
            needles.append(value.encode("utf-8"))
    return needles


def verify_exe(exe_path: Path) -> list[str]:
    if not exe_path.is_file():
        return [f"未找到打包产物：{exe_path}"]
    data = exe_path.read_bytes()
    problems: list[str] = []
    for needle in _credential_needles():
        if needle in data:
            problems.append(
                "检测到本地账号信息被打进 exe（请确认 build.spec 未打包 config.json，"
                f"且源码中无硬编码密钥）：{needle[:3].decode('utf-8', errors='ignore')}***"
            )
    banned_fragments = (
        b"tests/test_comprehensive.py",
        b"tests/test_connectors.py",
        b"test_hedge_direction.py",
        b"pytest.main(",
        b"def test_",
    )
    for frag in banned_fragments:
        if frag in data:
            problems.append(f"检测到测试代码片段被打包：{frag.decode('utf-8', errors='ignore')}")
    return problems


def main(argv: list[str] | None = None) -> int:
    argv = argv or sys.argv[1:]
    if not argv:
        print("用法: python scripts/verify_release_exe.py <TradeAssistant.exe>")
        return 2
    exe_path = Path(argv[0])
    problems = verify_exe(exe_path)
    if problems:
        for item in problems:
            print(f"[ERROR] {item}")
        return 1
    print(f"RELEASE VERIFY OK: {exe_path.name} 未包含本地 BA/Exness 账号及 tests 源码")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
