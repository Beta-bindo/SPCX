"""单实例启动：重复启动时激活已有窗口，避免任务栏连闪多个图标。"""

from __future__ import annotations

import sys

from PySide6.QtCore import QDir, QLockFile


def _activate_existing_window(title: str) -> None:
    if sys.platform != "win32":
        return
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        found: list[int] = []

        WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

        def _callback(hwnd: int, _lparam: int) -> bool:
            if not user32.IsWindowVisible(hwnd):
                return True
            length = user32.GetWindowTextLengthW(hwnd)
            if length <= 0:
                return True
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            if title in buf.value:
                found.append(hwnd)
            return True

        user32.EnumWindows(WNDENUMPROC(_callback), 0)
        if not found:
            return
        hwnd = found[0]
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, 9)  # SW_RESTORE
        else:
            user32.ShowWindow(hwnd, 5)  # SW_SHOW
        user32.SetForegroundWindow(hwnd)
    except Exception:
        return


def acquire_single_instance(app_name: str) -> QLockFile | None:
    """获取单实例锁；若已有实例在运行则激活其窗口并返回 None。"""
    lock = QLockFile(QDir.temp().absoluteFilePath(f"{app_name}.lock"))
    lock.setStaleLockTime(0)
    if lock.tryLock(200):
        return lock
    _activate_existing_window(app_name)
    return None
