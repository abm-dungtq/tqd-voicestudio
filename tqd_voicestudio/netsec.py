"""Network exposure rules: non-loopback binds require an API key."""

from __future__ import annotations

import ipaddress

REFUSE_MESSAGE = (
    "Không thể mở server trên địa chỉ {host} khi chưa có API key. "
    "Chạy `tqd-voicestudio key new` để tạo key, rồi khởi động lại."
)
_WILDCARDS = ("", "0.0.0.0", "::", "[::]")


def is_loopback(host: str) -> bool:
    """127.0.0.0/8, ::1 and "localhost" are loopback; anything else is not."""
    h = (host or "").strip().strip("[]").lower()
    if h == "localhost":
        return True
    try:
        return ipaddress.ip_address(h).is_loopback
    except ValueError:
        return False


def requires_key(host: str, api_key: str) -> bool:
    return not is_loopback(host) and not (api_key or "").strip()


def connect_host(host: str) -> str:
    """Address local clients use to reach a server bound to ``host``."""
    h = (host or "").strip()
    if h in _WILDCARDS or is_loopback(h):
        return "127.0.0.1"
    return h.strip("[]")
