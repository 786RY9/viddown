"""Fire-and-forget push notifications via ntfy.sh (free, no account needed)."""

from __future__ import annotations

import asyncio
import logging
import urllib.request

log = logging.getLogger(__name__)


def _post(url: str, title: str, body: str, tags: str) -> None:
    req = urllib.request.Request(
        url,
        data=body.encode("utf-8")[:4000],
        method="POST",
        headers={"Title": title.encode("ascii", "replace").decode(), "Tags": tags},
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        resp.read()


async def ntfy(base_url: str, topic: str, title: str, body: str, tags: str = "envelope") -> bool:
    """Returns False (and logs) on failure; never raises."""
    if not topic:
        return False
    try:
        await asyncio.to_thread(_post, f"{base_url}/{topic}", title, body, tags)
        return True
    except Exception as e:  # network errors must never break a request
        log.warning("ntfy push failed: %s", e)
        return False
