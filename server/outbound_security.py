"""Pin public-only outbound fetches to validated DNS answers.

HTTPX's sni_hostname extension retains TLS name verification while the URL
connects to a vetted IP address. Never issue the request to the original
hostname after checking its DNS response (DNS rebinding / SSRF TOCTOU).
"""
from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlsplit, urlunsplit


def pinned_request(url: str) -> tuple[str, dict[str, str], dict[str, str]]:
    parsed = urlsplit(url)
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("Invalid outbound URL port") from exc
    if (parsed.scheme not in ("http", "https") or not parsed.hostname or
            parsed.username or parsed.password or port not in (None, 80, 443) or
            len(url) > 2000 or any(c in url for c in "\r\n")):
        raise ValueError("Only public HTTP(S) URLs with standard ports are allowed")
    host = parsed.hostname
    try:
        addresses = socket.getaddrinfo(
            host, port or (443 if parsed.scheme == "https" else 80),
            type=socket.SOCK_STREAM)
        resolved = [ipaddress.ip_address(info[4][0]) for info in addresses]
    except (socket.gaierror, ValueError, OSError) as exc:
        raise ValueError("Cannot resolve public source hostname") from exc
    if not resolved or any(not addr.is_global for addr in resolved):
        raise ValueError("Outbound destination must resolve only to public IP addresses")
    chosen = resolved[0]
    address = f"[{chosen}]" if chosen.version == 6 else str(chosen)
    authority = address + (f":{port}" if port is not None else "")
    original_host = host if ":" not in host else f"[{host}]"
    if port is not None:
        original_host += f":{port}"
    target = urlunsplit((parsed.scheme, authority, parsed.path or "/", parsed.query, ""))
    return target, {"Host": original_host}, {"sni_hostname": host}
