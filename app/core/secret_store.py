from __future__ import annotations

import base64
import ctypes
import sys
from ctypes import wintypes

PROTECTED_PREFIX = "enc:v1:"


class _DataBlob(ctypes.Structure):
    _fields_ = [
        ("cbData", wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_ubyte)),
    ]


def _dpapi_available() -> bool:
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
    if not value or value.startswith(PROTECTED_PREFIX):
        return value
    if not _dpapi_available():
        return value
    return _protect_windows(value)


def unprotect_secret(value: str) -> str:
    if not value or not value.startswith(PROTECTED_PREFIX):
        return value
    if not _dpapi_available():
        return ""
    return _unprotect_windows(value)
