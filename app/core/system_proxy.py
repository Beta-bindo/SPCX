from __future__ import annotations

import socket
import sys
from dataclasses import dataclass


@dataclass(frozen=True)
class SystemProxy:
    host: str
    port: int


def tcp_port_open(host: str, port: int, timeout: float = 0.4) -> bool:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect((host, port))
        return True
    except OSError:
        return False
    finally:
        sock.close()


def _parse_proxy_server(raw: str) -> SystemProxy | None:
    text = (raw or "").strip()
    if not text:
        return None
    part = text.split(";")[0].split("=")[-1].strip()
    if ":" not in part:
        return None
    host, _, port_str = part.rpartition(":")
    try:
        port = int(port_str)
    except ValueError:
        return None
    return SystemProxy(host=host or "127.0.0.1", port=port)


def read_windows_http_proxy() -> SystemProxy | None:
    if sys.platform != "win32":
        return None
    try:
        import winreg

        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Internet Settings",
        )
        try:
            enabled = winreg.QueryValueEx(key, "ProxyEnable")[0]
            if not enabled:
                return None
            server = winreg.QueryValueEx(key, "ProxyServer")[0]
        finally:
            winreg.CloseKey(key)
        return _parse_proxy_server(str(server))
    except OSError:
        return None


def resolve_http_proxy(configured_host: str, configured_port: int) -> tuple[str, int, bool]:
    """Return (host, port, used_fallback). Prefer configured port if reachable."""
    host = (configured_host or "127.0.0.1").strip() or "127.0.0.1"
    if tcp_port_open(host, configured_port):
        return host, configured_port, False
    fallback = read_windows_http_proxy()
    if fallback and tcp_port_open(fallback.host, fallback.port):
        return fallback.host, fallback.port, True
    return host, configured_port, False
