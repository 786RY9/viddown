"""SSRF guard, stream-token signing, client IP resolution."""

from __future__ import annotations

import pytest

from viddown_server.security import (
    BadToken,
    StreamGrant,
    TokenSigner,
    UnsafeUrl,
    check_url,
    client_ip,
    is_public_ip,
)


def resolver_for(mapping: dict[str, list[str]]):  # type: ignore[no-untyped-def]
    async def resolve(host: str, port: int) -> list[str]:
        if host not in mapping:
            raise OSError("NXDOMAIN")
        return mapping[host]

    return resolve


PUBLIC = resolver_for({"www.youtube.com": ["142.250.74.46", "2a00:1450:4001:82b::200e"]})


@pytest.mark.parametrize(
    "addr",
    [
        "127.0.0.1", "10.1.2.3", "172.16.0.1", "192.168.1.1", "169.254.169.254",  # IMDS
        "100.64.0.1", "0.0.0.0", "224.0.0.1", "240.0.0.1", "192.0.2.1", "198.18.0.1",
        "::1", "fe80::1", "fc00::1", "::ffff:127.0.0.1", "::ffff:10.0.0.1", "2002:0a00:0001::1",
        "not-an-ip",
    ],
)  # fmt: skip
def test_non_public_ips_rejected(addr: str) -> None:
    assert not is_public_ip(addr)


@pytest.mark.parametrize("addr", ["142.250.74.46", "1.1.1.1", "2a00:1450:4001:82b::200e"])
def test_public_ips_accepted(addr: str) -> None:
    assert is_public_ip(addr)


async def test_accepts_public_https_url() -> None:
    url = "https://www.youtube.com/watch?v=aqz-KE-bpKQ"
    assert await check_url(f"  {url} ", resolver=PUBLIC) == url


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "ftp://example.com/x",
        "gopher://example.com",
        "javascript:alert(1)",
        "https://user:pass@www.youtube.com/watch",
        "https:///nohost",
        "https://www.youtube.com:99999/",
        "https://www.youtube.com/a b",
        "https://www.youtube.com/\r\nHost: evil",
        "https://" + "a" * 2100 + ".com",
    ],
)
async def test_rejects_bad_urls(url: str) -> None:
    with pytest.raises(UnsafeUrl):
        await check_url(url, resolver=PUBLIC)


@pytest.mark.parametrize(
    ("url", "addrs"),
    [
        ("http://localhost/", None),
        ("http://foo.localhost/", None),
        ("http://metadata.google.internal/", None),
        ("http://127.0.0.1:8000/healthz", None),
        ("http://[::1]/", None),
        ("http://169.254.169.254/opc/v2/instance/", None),  # OCI metadata service
        ("http://rebind.example/", ["93.184.216.34", "10.0.0.5"]),  # one bad address is enough
        ("http://evil.example/", ["::ffff:192.168.0.1"]),
        ("http://nxdomain.example/", []),
    ],
)
async def test_rejects_private_targets(url: str, addrs: list[str] | None) -> None:
    host = url.split("/")[2]
    resolver = resolver_for({host: addrs} if addrs else {})
    with pytest.raises(UnsafeUrl):
        await check_url(url, resolver=resolver)


async def test_private_allowed_only_with_escape_hatch() -> None:
    assert await check_url("http://127.0.0.1:9/x.mpd", allow_private=True) == "http://127.0.0.1:9/x.mpd"
    with pytest.raises(UnsafeUrl):  # scheme check still applies
        await check_url("file:///etc/passwd", allow_private=True)


# ── tokens ──────────────────────────────────────────────────────────────────

GRANT = StreamGrant("https://www.youtube.com/watch?v=x", "video", "299", 10, 20, False, 2_000_000_000)


def test_token_roundtrip() -> None:
    signer = TokenSigner(b"k" * 32, clock=lambda: 1_000)
    tok = signer.sign(GRANT, "203.0.113.7")
    assert signer.verify(tok, "203.0.113.7") == GRANT
    assert "203.0.113.7" not in tok  # IP only as a keyed hash
    import base64

    assert b"203.0.113.7" not in base64.urlsafe_b64decode(tok.split(".")[0] + "==")


def test_token_bound_to_ip_expiry_and_key() -> None:
    signer = TokenSigner(b"k" * 32, clock=lambda: 1_000)
    tok = signer.sign(GRANT, "203.0.113.7")
    with pytest.raises(BadToken, match="wrong_client"):
        signer.verify(tok, "203.0.113.8")
    with pytest.raises(BadToken, match="expired"):
        TokenSigner(b"k" * 32, clock=lambda: 3_000_000_000).verify(tok, "203.0.113.7")
    with pytest.raises(BadToken, match="bad_signature"):
        TokenSigner(b"z" * 32).verify(tok, "203.0.113.7")


def test_token_tampering_detected() -> None:
    signer = TokenSigner(b"k" * 32, clock=lambda: 1_000)
    tok = signer.sign(GRANT, "1.2.3.4")
    other = signer.sign(StreamGrant(GRANT.url, "video", "137", None, None, False, GRANT.expires), "1.2.3.4")
    body, mac = tok.split(".")
    with pytest.raises(BadToken):
        signer.verify(other.split(".")[0] + "." + mac, "1.2.3.4")  # swapped payload
    for bad in ("", "abc", "a.b.c", body + ".!!!", "x" * 9000):
        with pytest.raises(BadToken):
            signer.verify(bad, "1.2.3.4")


def test_short_secret_rejected() -> None:
    with pytest.raises(ValueError):
        TokenSigner(b"short")


# ── client ip ───────────────────────────────────────────────────────────────

TRUSTED = ("127.0.0.1", "::1")


def test_client_ip_direct_peer_ignores_xff() -> None:
    assert client_ip("198.51.100.9", "1.1.1.1", TRUSTED) == "198.51.100.9"


def test_client_ip_behind_caddy() -> None:
    assert client_ip("127.0.0.1", "203.0.113.5", TRUSTED) == "203.0.113.5"
    # A client-supplied XFF is on the left; Caddy appends the real peer on the right.
    assert client_ip("127.0.0.1", "6.6.6.6, 203.0.113.5", TRUSTED) == "203.0.113.5"
    assert client_ip("127.0.0.1", "garbage", TRUSTED) == "127.0.0.1"
    assert client_ip("127.0.0.1", None, TRUSTED) == "127.0.0.1"
