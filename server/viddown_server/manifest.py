"""GET /v1/manifest: what clients should run, and whether cloud mode is on.

Three inputs, merged on every request (cheap: files are re-read only when
their mtime changes):

* ``$VIDDOWN_MANIFEST_BASE`` (operator-edited JSON, e.g. /etc/viddown/manifest.json):
  latest app versions, notices, kill switches.
* ``$STATE_DIR/validated.json`` (written by deploy/update.sh after smoke tests
  pass): the yt-dlp / ffmpeg / server versions proven to work *today*. Clients
  pin their own yt-dlp to this version.
* Live state: egress budget, config switch, allow-list, stream caps.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import __version__
from .config import Settings

log = logging.getLogger(__name__)

SCHEMA_VERSION = 1


class JsonFile:
    """A JSON file cached by mtime; missing or broken files read as {}."""

    def __init__(self, path: Path | None) -> None:
        self.path = path
        self._mtime: float | None = None
        self._data: dict[str, Any] = {}

    def get(self) -> dict[str, Any]:
        if self.path is None:
            return {}
        try:
            mtime = self.path.stat().st_mtime
        except OSError:
            self._mtime, self._data = None, {}
            return {}
        if mtime != self._mtime:
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                self._data = data if isinstance(data, dict) else {}
            except (OSError, ValueError) as e:
                log.error("cannot read %s: %s", self.path, e)
                self._data = {}
            self._mtime = mtime
        return self._data


def cloud_status(s: Settings, base: dict[str, Any], egress_exceeded: bool) -> tuple[bool, str | None]:
    if not s.cloud_enabled:
        return False, "disabled"
    if (base.get("killSwitches") or {}).get("cloud"):
        return False, "kill_switch"
    if egress_exceeded:
        return False, "egress_budget"
    return True, None


def build_manifest(
    s: Settings,
    base: dict[str, Any],
    validated: dict[str, Any],
    egress_exceeded: bool,
) -> dict[str, Any]:
    enabled, reason = cloud_status(s, base, egress_exceeded)
    return {
        "schema": SCHEMA_VERSION,
        "generatedAt": datetime.now(UTC).isoformat(timespec="seconds"),
        "server": {"version": validated.get("server") or __version__},
        "ytdlp": {
            "version": validated.get("ytdlp"),
            "validatedAt": validated.get("validatedAt"),
        },
        "ffmpeg": {"version": validated.get("ffmpeg")},
        "apps": base.get("apps") or {},
        "notices": base.get("notices") or [],
        "killSwitches": base.get("killSwitches") or {},
        "cloud": {
            "enabled": enabled,
            "reason": reason,
            "extractors": list(s.allowed_extractors),
            "maxHeight": s.max_height,
            "maxDurationSec": s.max_duration_s,
            "maxBytes": s.max_stream_bytes,
            "maxConcurrentStreams": s.max_streams_per_ip,
        },
    }
