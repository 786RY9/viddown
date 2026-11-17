"""Typed settings read from environment variables (see .env.example).

Every knob has a production-safe default so the service starts with an empty
environment; only VIDDOWN_SECRET must be set for tokens to survive restarts.
"""

from __future__ import annotations

import logging
import os
import secrets
import sys
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

# Single-video extractors that cloud mode may use. Names are yt-dlp IE_NAMEs
# (`yt-dlp --list-extractors`), matched case-insensitively as full regexes.
DEFAULT_EXTRACTORS: tuple[str, ...] = (
    "youtube",
    "youtube:clip",
    "instagram",
    "tiktok",
    "twitter",
    "reddit",
    "vimeo",
    "facebook",
    "facebook:reel",
    "dailymotion",
    "streamable",
    "bluesky",
    "twitch:clips",
    "soundcloud",
    # Internet Archive: public-domain / Creative Commons media, and it serves
    # datacenter IPs, so the smoke test uses it (YouTube blocks cloud IPs).
    r"archive\.org",
)


def _env(env: Mapping[str, str], key: str, default: str) -> str:
    return env.get(f"VIDDOWN_{key}", default).strip()


def _int(env: Mapping[str, str], key: str, default: int) -> int:
    raw = _env(env, key, "")
    return int(raw) if raw else default


def _float(env: Mapping[str, str], key: str, default: float) -> float:
    raw = _env(env, key, "")
    return float(raw) if raw else default


def _bool(env: Mapping[str, str], key: str, default: bool) -> bool:
    raw = _env(env, key, "").lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


def _list(env: Mapping[str, str], key: str, default: tuple[str, ...]) -> tuple[str, ...]:
    raw = _env(env, key, "")
    if not raw:
        return default
    return tuple(p.strip() for p in raw.split(",") if p.strip())


def _optional_int(env: Mapping[str, str], key: str) -> int | None:
    raw = _env(env, key, "")
    return int(raw) if raw else None


@dataclass(frozen=True)
class Settings:
    # ── network ────────────────────────────────────────────────────────────
    host: str = "127.0.0.1"
    port: int = 8000
    trusted_proxies: tuple[str, ...] = ("127.0.0.1", "::1")
    public_base_url: str = ""

    # ── secrets / storage ──────────────────────────────────────────────────
    secret: bytes = field(default_factory=lambda: secrets.token_bytes(32), repr=False)
    state_dir: Path = Path("./state")
    runtime_dir: Path = Path(tempfile.gettempdir()) / "viddown"
    manifest_base_path: Path | None = None

    # ── resource budget (resources.py) ─────────────────────────────────────
    reserved_mb: int = 1500
    base_mb: int = 200
    extract_mb: int = 150
    stream_mb: int = 180
    transcode_mb: int = 400
    streams_per_cpu: int = 15
    net_stream_cap: int = 40
    extract_workers: int | None = None
    stream_slots: int | None = None
    transcode_slots: int | None = None
    max_waiters: int = 32
    queue_wait_s: float = 20.0

    # ── per-IP limits ──────────────────────────────────────────────────────
    probe_rate_per_min: float = 20.0
    probe_burst: int = 20
    stream_rate_per_min: float = 10.0
    max_streams_per_ip: int = 2
    contact_rate_per_hour: float = 5.0
    ping_rate_per_hour: float = 60.0

    # ── memory-pressure guard ──────────────────────────────────────────────
    pressure_interval_s: float = 2.0
    psi_some_avg10_pressure: float = 10.0
    psi_full_avg10_critical: float = 5.0
    mem_available_low_mb: int = 800
    mem_available_critical_mb: int = 300

    # ── egress budget ──────────────────────────────────────────────────────
    egress_cutoff_bytes: int = 8 * 10**12
    egress_flush_s: float = 30.0

    # ── browsers ───────────────────────────────────────────────────────────
    # Web origins allowed to call the API from a browser: the landing page
    # (viddown.dayframe.studio on Vercel, plus the old GitHub Pages address) and the desktop app's webview (Tauri: Windows uses
    # http(s)://tauri.localhost, macOS/Linux use tauri://localhost).
    # Native Android requests don't use CORS.
    cors_origins: tuple[str, ...] = (
        "https://viddown.dayframe.studio",
        "https://786ry9.github.io",
        "tauri://localhost",
        "http://tauri.localhost",
        "https://tauri.localhost",
    )

    # ── extraction ─────────────────────────────────────────────────────────
    allowed_extractors: tuple[str, ...] = DEFAULT_EXTRACTORS
    probe_cache_size: int = 256
    probe_cache_ttl_s: float = 600.0
    extract_timeout_s: float = 45.0
    max_tasks_per_child: int = 50
    max_height: int = 1080
    max_duration_s: int = 3 * 3600

    # ── streaming ──────────────────────────────────────────────────────────
    token_ttl_s: int = 600
    stall_timeout_s: float = 60.0
    max_stream_bytes: int = 2 * 1024**3
    max_stream_s: float = 3 * 3600.0
    chunk_size: int = 64 * 1024
    ytdlp_cmd: tuple[str, ...] = (sys.executable, "-m", "yt_dlp")
    ffmpeg_bin: str = "ffmpeg"
    drain_timeout_s: int = 300

    # ── notifications / misc ───────────────────────────────────────────────
    ntfy_url: str = "https://ntfy.sh"
    ntfy_topic: str = ""
    cloud_enabled: bool = True
    log_level: str = "INFO"

    # ── test-only escape hatches (never enable in production) ─────────────
    unsafe_allow_generic: bool = False
    unsafe_allow_private_urls: bool = False

    @property
    def ytdlp_extractor_patterns(self) -> list[str]:
        """Patterns for yt-dlp's `allowed_extractors`; generic is always excluded
        unless the test-only escape hatch is on."""
        pats = list(self.allowed_extractors)
        if self.unsafe_allow_generic:
            return pats + ["generic"]
        return pats + ["-generic"]

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        env = os.environ if env is None else env
        secret_raw = _env(env, "SECRET", "")
        if secret_raw:
            secret = secret_raw.encode()
        else:
            log.warning("VIDDOWN_SECRET is not set; stream tokens will not survive a restart")
            secret = secrets.token_bytes(32)

        # systemd sets RUNTIME_DIRECTORY / STATE_DIRECTORY when the unit declares them.
        runtime = env.get("RUNTIME_DIRECTORY") or _env(env, "RUNTIME_DIR", "")
        state = env.get("STATE_DIRECTORY") or _env(env, "STATE_DIR", "")
        manifest_base = _env(env, "MANIFEST_BASE", "")
        ytdlp_cmd = _env(env, "YTDLP_CMD", "")

        d = cls()  # defaults
        return cls(
            host=_env(env, "HOST", d.host),
            port=_int(env, "PORT", d.port),
            trusted_proxies=_list(env, "TRUSTED_PROXIES", d.trusted_proxies),
            public_base_url=_env(env, "PUBLIC_BASE_URL", d.public_base_url).rstrip("/"),
            secret=secret,
            state_dir=Path(state.split(":")[0]) if state else d.state_dir,
            runtime_dir=Path(runtime.split(":")[0]) if runtime else d.runtime_dir,
            manifest_base_path=Path(manifest_base) if manifest_base else None,
            reserved_mb=_int(env, "RESERVED_MB", d.reserved_mb),
            base_mb=_int(env, "BASE_MB", d.base_mb),
            extract_mb=_int(env, "EXTRACT_MB", d.extract_mb),
            stream_mb=_int(env, "STREAM_MB", d.stream_mb),
            transcode_mb=_int(env, "TRANSCODE_MB", d.transcode_mb),
            streams_per_cpu=_int(env, "STREAMS_PER_CPU", d.streams_per_cpu),
            net_stream_cap=_int(env, "NET_STREAM_CAP", d.net_stream_cap),
            extract_workers=_optional_int(env, "EXTRACT_WORKERS"),
            stream_slots=_optional_int(env, "STREAM_SLOTS"),
            transcode_slots=_optional_int(env, "TRANSCODE_SLOTS"),
            max_waiters=_int(env, "MAX_WAITERS", d.max_waiters),
            queue_wait_s=_float(env, "QUEUE_WAIT_S", d.queue_wait_s),
            probe_rate_per_min=_float(env, "PROBE_RATE_PER_MIN", d.probe_rate_per_min),
            probe_burst=_int(env, "PROBE_BURST", d.probe_burst),
            stream_rate_per_min=_float(env, "STREAM_RATE_PER_MIN", d.stream_rate_per_min),
            max_streams_per_ip=_int(env, "MAX_STREAMS_PER_IP", d.max_streams_per_ip),
            contact_rate_per_hour=_float(env, "CONTACT_RATE_PER_HOUR", d.contact_rate_per_hour),
            ping_rate_per_hour=_float(env, "PING_RATE_PER_HOUR", d.ping_rate_per_hour),
            pressure_interval_s=_float(env, "PRESSURE_INTERVAL_S", d.pressure_interval_s),
            psi_some_avg10_pressure=_float(env, "PSI_SOME_PRESSURE", d.psi_some_avg10_pressure),
            psi_full_avg10_critical=_float(env, "PSI_FULL_CRITICAL", d.psi_full_avg10_critical),
            mem_available_low_mb=_int(env, "MEM_LOW_MB", d.mem_available_low_mb),
            mem_available_critical_mb=_int(env, "MEM_CRITICAL_MB", d.mem_available_critical_mb),
            egress_cutoff_bytes=int(_float(env, "EGRESS_CUTOFF_TB", d.egress_cutoff_bytes / 10**12) * 10**12),
            egress_flush_s=_float(env, "EGRESS_FLUSH_S", d.egress_flush_s),
            cors_origins=_list(env, "CORS_ORIGINS", d.cors_origins),
            allowed_extractors=_list(env, "ALLOWED_EXTRACTORS", d.allowed_extractors),
            probe_cache_size=_int(env, "PROBE_CACHE_SIZE", d.probe_cache_size),
            probe_cache_ttl_s=_float(env, "PROBE_CACHE_TTL_S", d.probe_cache_ttl_s),
            extract_timeout_s=_float(env, "EXTRACT_TIMEOUT_S", d.extract_timeout_s),
            max_tasks_per_child=_int(env, "MAX_TASKS_PER_CHILD", d.max_tasks_per_child),
            max_height=_int(env, "MAX_HEIGHT", d.max_height),
            max_duration_s=_int(env, "MAX_DURATION_S", d.max_duration_s),
            token_ttl_s=_int(env, "TOKEN_TTL_S", d.token_ttl_s),
            stall_timeout_s=_float(env, "STALL_TIMEOUT_S", d.stall_timeout_s),
            max_stream_bytes=int(_float(env, "MAX_STREAM_GB", d.max_stream_bytes / 1024**3) * 1024**3),
            max_stream_s=_float(env, "MAX_STREAM_HOURS", d.max_stream_s / 3600) * 3600,
            chunk_size=_int(env, "CHUNK_SIZE", d.chunk_size),
            ytdlp_cmd=tuple(ytdlp_cmd.split()) if ytdlp_cmd else d.ytdlp_cmd,
            ffmpeg_bin=_env(env, "FFMPEG", d.ffmpeg_bin),
            drain_timeout_s=_int(env, "DRAIN_TIMEOUT_S", d.drain_timeout_s),
            ntfy_url=_env(env, "NTFY_URL", d.ntfy_url).rstrip("/"),
            ntfy_topic=_env(env, "NTFY_TOPIC", d.ntfy_topic),
            cloud_enabled=_bool(env, "CLOUD_ENABLED", d.cloud_enabled),
            log_level=_env(env, "LOG_LEVEL", d.log_level).upper(),
            unsafe_allow_generic=_bool(env, "UNSAFE_ALLOW_GENERIC", False),
            unsafe_allow_private_urls=_bool(env, "UNSAFE_ALLOW_PRIVATE_URLS", False),
        )
