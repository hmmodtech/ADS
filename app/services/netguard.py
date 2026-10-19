"""SSRF guard: server-side fetches of user-supplied URLs must not reach internal networks."""
from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

from ..config import get_settings


class UnsafeURL(ValueError):
    pass


def assert_public_url(url: str) -> str:
    p = urlparse(url)
    if p.scheme not in ("http", "https") or not p.hostname:
        raise UnsafeURL("الرابط يجب أن يبدأ بـ http:// أو https://")
    if get_settings().allow_private_store_urls:
        return url
    try:
        infos = socket.getaddrinfo(p.hostname, p.port or (443 if p.scheme == "https" else 80))
    except socket.gaierror as e:
        raise UnsafeURL(f"تعذّر حلّ اسم النطاق: {p.hostname}") from e
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved
                or ip.is_multicast or ip.is_unspecified):
            raise UnsafeURL("غير مسموح بالاتصال بعناوين داخلية/خاصة")
    return url
