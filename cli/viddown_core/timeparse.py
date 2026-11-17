"""Flexible time input -> HH:MM:SS."""

import re
from typing import Optional

_COLON_RE = re.compile(r"(\d+):(\d{2})(?::(\d{2}))?")
_UNITS_RE = re.compile(
    r"(?:(\d+)\s*h)?\s*(?:(\d+)\s*m(?:in)?)?\s*(?:(\d+)\s*s(?:ec)?)?"
)


def _fmt(total: int) -> str:
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def parse_time(raw: str) -> Optional[str]:
    """
    Accept flexible time input and return an HH:MM:SS string, or None.
    Accepts: 5:23 · 1:05:30 · 5m23s · 1h5m30s · 323 (seconds) · 30s
    """
    raw = raw.strip().lower()

    if re.fullmatch(r"\d+", raw):
        return _fmt(int(raw))

    match = _COLON_RE.fullmatch(raw)
    if match:
        if match.group(3) is not None:
            h, m, s = int(match.group(1)), int(match.group(2)), int(match.group(3))
        else:
            h, m, s = 0, int(match.group(1)), int(match.group(2))
        return f"{h:02d}:{m:02d}:{s:02d}"

    match = _UNITS_RE.fullmatch(raw)
    if match and any(match.group(i) for i in (1, 2, 3)):
        h = int(match.group(1) or 0)
        m = int(match.group(2) or 0)
        s = int(match.group(3) or 0)
        return f"{h:02d}:{m:02d}:{s:02d}"

    return None


def to_seconds(t: str) -> int:
    """HH:MM:SS (as returned by parse_time) -> total seconds."""
    h, m, s = map(int, t.split(":"))
    return h * 3600 + m * 60 + s
