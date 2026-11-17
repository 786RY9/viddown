"""SSRF guard, signed stream tokens and client-IP resolution."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import ipaddress
import json
import socket
import time
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from urllib.parse import urlsplit

MAX_URL_LEN = 2048

# ═════════════════════════════════════════════════════════════════════════════
# SSRF
# ═════════════════════════════════════════════════════════════════════════════


class UnsafeUrl(ValueError):
    """The URL is malformed, not http(s), or points at a non-public address."""


Resolver = Callable[[str, int], Awaitable[list[str]]]


async def default_resolver(host: str, port: int) -> list[str]:
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return [str(info[4][0]) for info in infos]


def is_public_ip(addr: str) -> bool:
    """True only for globally routable unicast addresses.

    ``is_global`` already excludes private (RFC 1918, ULA), loopback,
    link-local, CGNAT (100.64/10), documentation, benchmarking and reserved
    ranges; multicast and IPv4-mapped/6to4 forms are checked explicitly.
    """
    try:
        ip = ipaddress.ip_address(addr.split("%", 1)[0])  # strip IPv6 zone id
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped is not None:
            return is_public_ip(str(ip.ipv4_mapped))
        if ip.sixtofour is not None:
            return is_public_ip(str(ip.sixtofour))
        if ip.teredo is not None:
            return False
    return ip.is_global and not ip.is_multicast and not ip.is_unspecified


async def check_url(url: str, resolver: Resolver = default_resolver, allow_private: bool = False) -> str:
    """Validate a user-supplied URL and return it normalised (stripped).

    This is defence in depth: the primary control is yt-dlp's extractor
    allow-list with the generic extractor disabled, which stops arbitrary
    hosts from being fetched at all.
    """
    url = url.strip()
    if not url or len(url) > MAX_URL_LEN:
        raise UnsafeUrl("URL is empty or too long")
    if any(c in url for c in "\r\n\t\x00 "):
        raise UnsafeUrl("URL contains whitespace or control characters")
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise UnsafeUrl("only http and https URLs are allowed")
    if parts.username or parts.password:
        raise UnsafeUrl("credentials in URLs are not allowed")
    host = parts.hostname
    if not host:
        raise UnsafeUrl("URL has no host")
    try:
        port = parts.port or (443 if parts.scheme == "https" else 80)
    except ValueError as e:
        raise UnsafeUrl("invalid port") from e

    if allow_private:  # test-only escape hatch (VIDDOWN_UNSAFE_ALLOW_PRIVATE_URLS)
        return url

    # Literal IPs are judged directly; names are resolved and *every* address
    # must be public (an attacker controls which one the fetcher picks).
    try:
        ipaddress.ip_address(host)
        addrs = [host]
    except ValueError:
        if host.endswith(".") and host != ".":
            host = host[:-1]
        lowered = host.lower()
        if lowered == "localhost" or lowered.endswith((".localhost", ".local", ".internal", ".arpa")):
            raise UnsafeUrl("internal host names are not allowed")
        try:
            addrs = await resolver(host, port)
        except (OSError, UnicodeError) as e:
            raise UnsafeUrl(f"could not resolve host {host!r}") from e
    if not addrs:
        raise UnsafeUrl("host did not resolve")
    for a in addrs:
        if not is_public_ip(a):
            raise UnsafeUrl("URL resolves to a non-public address")
    return url


# ═════════════════════════════════════════════════════════════════════════════
# Stream tokens
# ═════════════════════════════════════════════════════════════════════════════


class BadToken(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _b64e(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


@dataclass(frozen=True)
class StreamGrant:
    url: str
    kind: str  # "video" | "audio"
    format_id: str  # yt-dlp format id, or "audio"
    clip_start: int | None  # seconds
    clip_end: int | None
    precise: bool
    expires: int  # unix seconds


class TokenSigner:
    """HMAC-SHA256 tokens binding url + format + clip + client IP + expiry.

    Format: ``base64url(json payload) "." base64url(mac)``. The client IP is
    not stored in the token in clear: only a keyed hash of it, so tokens that
    leak into logs don't leak addresses.
    """

    VERSION = 1

    def __init__(self, secret: bytes, clock: Callable[[], float] = time.time) -> None:
        if len(secret) < 16:
            raise ValueError("token secret must be at least 16 bytes")
        self._key = hashlib.sha256(b"viddown-stream-token\x00" + secret).digest()
        self.clock = clock

    def _ip_tag(self, ip: str) -> str:
        return _b64e(hmac.new(self._key, b"ip\x00" + ip.encode(), hashlib.sha256).digest()[:12])

    def sign(self, grant: StreamGrant, client_ip: str) -> str:
        payload = {
            "v": self.VERSION,
            "u": grant.url,
            "k": grant.kind,
            "f": grant.format_id,
            "s": grant.clip_start,
            "e": grant.clip_end,
            "p": int(grant.precise),
            "x": grant.expires,
            "i": self._ip_tag(client_ip),
        }
        body = _b64e(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode())
        mac = hmac.new(self._key, body.encode(), hashlib.sha256).digest()
        return f"{body}.{_b64e(mac)}"

    def verify(self, token: str, client_ip: str) -> StreamGrant:
        if len(token) > 8192 or token.count(".") != 1:
            raise BadToken("malformed")
        body, mac_s = token.split(".")
        try:
            mac = _b64d(mac_s)
        except (ValueError, TypeError) as e:
            raise BadToken("malformed") from e
        want = hmac.new(self._key, body.encode(), hashlib.sha256).digest()
        if not hmac.compare_digest(mac, want):
            raise BadToken("bad_signature")
        try:
            p = json.loads(_b64d(body))
        except (ValueError, TypeError) as e:
            raise BadToken("malformed") from e
        if p.get("v") != self.VERSION:
            raise BadToken("bad_version")
        if int(p["x"]) < self.clock():
            raise BadToken("expired")
        if not hmac.compare_digest(str(p["i"]), self._ip_tag(client_ip)):
            raise BadToken("wrong_client")
        return StreamGrant(
            url=p["u"],
            kind=p["k"],
            format_id=p["f"],
            clip_start=p["s"],
            clip_end=p["e"],
            precise=bool(p["p"]),
            expires=int(p["x"]),
        )


# ═════════════════════════════════════════════════════════════════════════════
# Client IP
# ═════════════════════════════════════════════════════════════════════════════


def client_ip(peer: str | None, forwarded_for: str | None, trusted: Iterable[str]) -> str:
    """The real client address.

    ``X-Forwarded-For`` is honoured only when the direct peer is a trusted
    proxy (Caddy on localhost). We then walk the list right-to-left and take the
    first hop that is not itself trusted, so a client can't spoof its address
    by sending its own X-Forwarded-For header (Caddy appends to it).
    """
    trusted_set = set(trusted)
    peer = peer or "unknown"
    if peer not in trusted_set or not forwarded_for:
        return peer
    hops = [h.strip() for h in forwarded_for.split(",") if h.strip()]
    for hop in reversed(hops):
        if hop not in trusted_set:
            try:
                return str(ipaddress.ip_address(hop))
            except ValueError:
                return peer
    return peer
