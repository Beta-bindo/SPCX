"""Harden requests sessions for Binance API calls through local HTTP proxies."""

from __future__ import annotations

import ssl
import time
from typing import Callable, Mapping, TypeVar

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

T = TypeVar("T")


def is_transient_network_error(exc: BaseException) -> bool:
    """True for proxy/TLS glitches that often succeed on retry."""
    if exc is None:
        return False
    name = type(exc).__name__
    if name in {"SSLError", "SSLEOFError", "ProxyError", "ConnectionError", "ProtocolError"}:
        return True
    msg = str(exc).lower()
    needles = (
        "ssl",
        "eof occurred in violation of protocol",
        "connection reset",
        "connection aborted",
        "broken pipe",
        "max retries exceeded",
    )
    return any(n in msg for n in needles)


def clear_session_pools(session: requests.Session) -> None:
    for adapter in session.adapters.values():
        try:
            adapter.close()
        except Exception:
            pass


def _proxy_ssl_context() -> ssl.SSLContext:
    from urllib3.util.ssl_ import create_urllib3_context

    ctx = create_urllib3_context()
    # Some Clash/V2Ray HTTP CONNECT paths fail TLS 1.3 handshakes intermittently.
    if hasattr(ssl, "TLSVersion"):
        ctx.maximum_version = ssl.TLSVersion.TLSv1_2
    return ctx


class _ProxyTlsAdapter(HTTPAdapter):
    def init_poolmanager(self, *args, **kwargs):
        kwargs.setdefault("ssl_context", _proxy_ssl_context())
        return super().init_poolmanager(*args, **kwargs)

    def proxy_manager_for(self, proxy, **proxy_kwargs):
        proxy_kwargs.setdefault("ssl_context", _proxy_ssl_context())
        return super().proxy_manager_for(proxy, **proxy_kwargs)


def configure_requests_session(
    session: requests.Session,
    *,
    verify: str | bool,
    proxies: Mapping[str, str] | None = None,
    through_proxy: bool = False,
    retry_on_rate_limit: bool = False,
) -> None:
    """Apply retry, TLS and pooling settings suited for Binance + local proxy."""
    status_forcelist = (408, 500, 502, 503, 504)
    if retry_on_rate_limit:
        status_forcelist = (408, 429, 500, 502, 503, 504)
    retry = Retry(
        total=2 if not retry_on_rate_limit else 4,
        connect=2 if not retry_on_rate_limit else 4,
        read=2 if not retry_on_rate_limit else 4,
        backoff_factor=0.4,
        status_forcelist=status_forcelist,
        allowed_methods=frozenset(["GET", "POST", "PUT", "DELETE", "HEAD", "OPTIONS"]),
        raise_on_status=False,
    )
    adapter_cls = _ProxyTlsAdapter if through_proxy else HTTPAdapter
    adapter = adapter_cls(
        max_retries=retry,
        pool_connections=1,
        pool_maxsize=1,
    )
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    session.trust_env = False
    session.verify = verify
    session.proxies = dict(proxies or {})
    # Avoid reusing half-dead TLS tunnels through Clash HTTP proxy.
    session.headers.setdefault("Connection", "close")


def run_with_network_retry(
    fn: Callable[[], T],
    *,
    session: requests.Session | None = None,
    attempts: int = 3,
    base_delay: float = 0.35,
) -> T:
    last_exc: BaseException | None = None
    for attempt in range(attempts):
        try:
            return fn()
        except BaseException as exc:
            last_exc = exc
            if not is_transient_network_error(exc) or attempt >= attempts - 1:
                raise
            if session is not None:
                clear_session_pools(session)
            time.sleep(base_delay * (attempt + 1))
    assert last_exc is not None
    raise last_exc
