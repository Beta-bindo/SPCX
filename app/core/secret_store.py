"""敏感信息（API 密钥 / MT5 密码）的本地加密存储。

Windows 上使用 DPAPI（CryptProtectData，绑定当前用户）加密，密文以 ``enc:v1:`` 前缀标记。
非 Windows 平台无 DPAPI：protect 直接明文返回，unprotect 对历史密文返回空串
（无法解密）。注意：这意味着在 macOS/Linux 上密钥以明文保存在 config.json 中，
若用于实盘请自行评估该风险或改用系统钥匙串。
"""

from __future__ import annotations

import base64
import ctypes
import sys
from ctypes import wintypes

PROTECTED_PREFIX = "enc:v1:"  # 密文标记前缀，用于区分明文与已加密值


class _DataBlob(ctypes.Structure):
    """对应 Windows DPAPI 的 DATA_BLOB 结构。"""

    _fields_ = [
        ("cbData", wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_ubyte)),
    ]


def _dpapi_available() -> bool:
    """当前平台是否支持 DPAPI（仅 Windows）。"""
    return sys.platform == "win32"


def _to_blob(data: bytes) -> tuple[_DataBlob, ctypes.Array]:
    buf = ctypes.create_string_buffer(data)
    blob = _DataBlob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_ubyte)))
    return blob, buf


def _protect_windows(value: str) -> str:
    crypt32 = ctypes.windll.crypt32
    in_blob, _buf = _to_blob(value.encode("utf-8"))
    out_blob = _DataBlob()
    ok = crypt32.CryptProtectData(
        ctypes.byref(in_blob),
        None,
        None,
        None,
        None,
        0,
        ctypes.byref(out_blob),
    )
    if not ok:
        raise OSError("Windows DPAPI encryption failed")
    try:
        raw = ctypes.string_at(out_blob.pbData, out_blob.cbData)
        return PROTECTED_PREFIX + base64.b64encode(raw).decode("ascii")
    finally:
        ctypes.windll.kernel32.LocalFree(out_blob.pbData)


def _unprotect_windows(value: str) -> str:
    crypt32 = ctypes.windll.crypt32
    raw = base64.b64decode(value.removeprefix(PROTECTED_PREFIX).encode("ascii"))
    in_blob, _buf = _to_blob(raw)
    out_blob = _DataBlob()
    ok = crypt32.CryptUnprotectData(
        ctypes.byref(in_blob),
        None,
        None,
        None,
        None,
        0,
        ctypes.byref(out_blob),
    )
    if not ok:
        raise OSError("Windows DPAPI decryption failed")
    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData).decode("utf-8")
    finally:
        ctypes.windll.kernel32.LocalFree(out_blob.pbData)


def protect_secret(value: str) -> str:
    """加密明文：空值/已加密值原样返回；非 Windows 平台返回明文（无 DPAPI）。"""
    if not value or value.startswith(PROTECTED_PREFIX):
        return value
    if not _dpapi_available():
        return value
    return _protect_windows(value)


def unprotect_secret(value: str) -> str:
    """解密密文：非密文原样返回；非 Windows 平台无法解密历史密文，返回空串。"""
    if not value or not value.startswith(PROTECTED_PREFIX):
        return value
    if not _dpapi_available():
        return ""
    return _unprotect_windows(value)
