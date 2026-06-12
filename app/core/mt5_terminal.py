from __future__ import annotations

import os
from pathlib import Path

TERMINAL_EXE = "terminal64.exe"


def _normalize_terminal_path(raw: str) -> Path | None:
    text = raw.strip().strip('"')
    if not text:
        return None
    path = Path(text)
    if path.is_dir():
        path = path / TERMINAL_EXE
    return path if path.is_file() else None


def _read_origin_path(origin_file: Path) -> Path | None:
    for encoding in ("utf-16-le", "utf-16", "utf-8"):
        try:
            lines = origin_file.read_text(encoding=encoding, errors="ignore").splitlines()
        except OSError:
            continue
        if not lines:
            continue
        candidate = _normalize_terminal_path(lines[0])
        if candidate:
            return candidate
    return None


def _paths_from_origin_files() -> list[Path]:
    root = Path(os.environ.get("APPDATA", "")) / "MetaQuotes" / "Terminal"
    if not root.is_dir():
        return []
    found: list[Path] = []
    for item in root.iterdir():
        if not item.is_dir():
            continue
        origin = item / "origin.txt"
        if not origin.is_file():
            continue
        candidate = _read_origin_path(origin)
        if candidate:
            found.append(candidate)
    return found


def _common_install_paths() -> list[Path]:
    folder_names = (
        "MetaTrader 5",
        "Exness MetaTrader 5",
        "Exness MT5",
        "Exness",
    )
    roots = [
        Path(r"C:\Program Files"),
        Path(r"C:\Program Files (x86)"),
        Path.home() / "AppData" / "Local" / "Programs",
    ]
    found: list[Path] = []
    for base in roots:
        if not base.is_dir():
            continue
        for name in folder_names:
            for relative in (Path(name) / TERMINAL_EXE, Path(name) / "MetaTrader 5" / TERMINAL_EXE):
                exe = base / relative
                if exe.is_file():
                    found.append(exe)
        try:
            for child in base.iterdir():
                if not child.is_dir():
                    continue
                label = child.name.lower()
                if "meta" not in label and "exness" not in label and "mt5" not in label:
                    continue
                for relative in (Path(TERMINAL_EXE), Path("MetaTrader 5") / TERMINAL_EXE):
                    exe = child / relative
                    if exe.is_file():
                        found.append(exe)
        except OSError:
            continue
    return found


def find_mt5_terminal(configured_path: str = "") -> Path | None:
    configured = _normalize_terminal_path(configured_path)
    if configured:
        return configured

    seen: set[str] = set()
    for candidate in _paths_from_origin_files() + _common_install_paths():
        key = str(candidate.resolve()).lower()
        if key in seen:
            continue
        seen.add(key)
        if candidate.is_file():
            return candidate
    return None


def mt5_terminal_hint() -> str:
    return (
        "请安装 Exness MetaTrader 5（64 位），安装后打开终端并登录；"
        "若已安装，可在「设置 → Exness (MT5)」中指定 terminal64.exe 路径"
    )
