"""Metadata extraction: pre-warmed yt-dlp process pool + single-flight + LRU/TTL cache.

yt-dlp extraction is CPU-bound Python that can also leak memory over time, so
it runs in a ``ProcessPoolExecutor`` whose workers import yt-dlp once at start
(pre-warmed) and are recycled after ``max_tasks_per_child`` jobs. The main
process never imports yt-dlp, which keeps the event loop lean.

A worker returns two things: the public ``MediaInfo`` dict (shape of
packages/core/src/types.ts, no media URLs) and the full sanitized info JSON,
zlib-compressed. The latter is handed to the streaming yt-dlp processes via
``--load-info-json`` so a stream doesn't extract the page a second time.
"""

from __future__ import annotations

import asyncio
import functools
import json
import logging
import multiprocessing
import os
import re
import sys
import time
import zlib
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Hashable
from concurrent.futures import Future, ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from dataclasses import dataclass, field
from typing import Any, Generic, TypeVar

from ._core import filter_video_formats
from .config import Settings
from .resources import AdmissionController, Ticket

log = logging.getLogger(__name__)

# ═════════════════════════════════════════════════════════════════════════════
# Error classification (mirror of packages/core/src/errors.ts)
# ═════════════════════════════════════════════════════════════════════════════

_RULES: list[tuple[str, re.Pattern[str]]] = [
    ("unsupported", re.compile(r"unsupported url|no suitable extractor|is not a valid url", re.IGNORECASE)),
    (
        "unavailable",
        re.compile(
            r"private video|video unavailable|has been removed|members-only|join this channel|"
            r"premieres in|drm|this live event will begin|copyright",
            re.IGNORECASE,
        ),
    ),
    (
        "blocked",
        re.compile(
            r"sign in to confirm|not a bot|http error 403|http error 429|too many requests|"
            r"not available in your country|geo.?restrict|login required|rate.?limit",
            re.IGNORECASE,
        ),
    ),
    (
        "network",
        re.compile(
            r"timed out|timeout|connection (reset|refused|aborted)|network is unreachable|"
            r"temporary failure in name resolution|getaddrinfo|ssl|eof occurred|incompleteread|"
            r"http error 5\d\d",
            re.IGNORECASE,
        ),
    ),
]


def last_error_line(text: str) -> str:
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    err = next((ln for ln in reversed(lines) if ln.startswith("ERROR:")), None)
    line = err if err is not None else (lines[-1] if lines else "Unknown error")
    return re.sub(r"^ERROR:\s*", "", line)


def classify_error(text: str) -> tuple[str, str]:
    """yt-dlp/ffmpeg output -> (ErrorKind, message)."""
    line = last_error_line(text)
    for kind, rx in _RULES:
        if rx.search(line):
            return kind, line
    return "unknown", line


class ExtractError(Exception):
    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind
        self.message = message


# ═════════════════════════════════════════════════════════════════════════════
# Worker side (runs inside the pool processes)
# ═════════════════════════════════════════════════════════════════════════════

_FORMAT_FIELDS = (
    "format_id", "ext", "vcodec", "acodec", "height", "width", "fps", "tbr", "abr",
    "protocol", "filesize", "filesize_approx",
)  # fmt: skip

_worker_opts: dict[str, Any] = {}


def worker_init(opts: dict[str, Any]) -> None:
    """Pool initializer: import yt-dlp and build the extractor table once."""
    global _worker_opts
    _worker_opts = dict(opts)
    import yt_dlp  # noqa: F401
    from yt_dlp.extractor import gen_extractor_classes

    gen_extractor_classes()


def worker_warm() -> int:
    return os.getpid()


class _CollectLogger:
    """yt-dlp logger that keeps errors instead of printing them."""

    def __init__(self) -> None:
        self.errors: list[str] = []

    def debug(self, msg: str) -> None:
        pass

    def info(self, msg: str) -> None:
        pass

    def warning(self, msg: str) -> None:
        pass

    def error(self, msg: str) -> None:
        self.errors.append(msg)


def to_media_info(info: dict[str, Any], url: str, max_height: int) -> dict[str, Any]:
    """yt-dlp info dict -> MediaInfo (packages/core/src/types.ts). No media URLs."""
    formats = [
        {k: f.get(k) for k in _FORMAT_FIELDS if k in f}
        for f in info.get("formats") or []
        if f.get("format_id") and f.get("protocol") != "mhtml"
    ]
    video = [f for f in filter_video_formats(formats) if (f.get("height") or 0) <= max_height]
    return {
        "url": url,
        "id": str(info.get("id") or ""),
        "title": str(info.get("title") or info.get("id") or "video"),
        "uploader": info.get("uploader") or info.get("channel"),
        "extractor": str(info.get("extractor_key") or info.get("extractor") or "unknown"),
        "durationSec": info.get("duration"),
        "thumbnail": info.get("thumbnail"),
        "formats": formats,
        "videoFormats": video,
    }


def worker_extract(url: str) -> tuple[str, Any]:
    """Returns ("ok", (media_info, zlib(json(info)))) or ("error", (kind, message)).

    Never raises: exceptions don't always pickle cleanly across processes.
    """
    import yt_dlp

    logger = _CollectLogger()
    opts = {
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "noplaylist": True,
        "skip_download": True,
        "socket_timeout": 15,
        "extractor_retries": 1,
        "logger": logger,
        **{k: v for k, v in _worker_opts.items() if k not in ("max_height", "max_duration")},
    }
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
            if not info:
                return "error", ("unknown", "extraction returned nothing")
            if info.get("_type") in ("playlist", "multi_video", "url", "url_transparent"):
                return "error", ("unsupported", "Playlists and multi-video posts are not supported in cloud mode")
            if info.get("is_live") or info.get("live_status") in ("is_live", "is_upcoming"):
                return "error", ("unsupported", "Live streams are not supported in cloud mode")
            max_dur = _worker_opts.get("max_duration") or 0
            if max_dur and (info.get("duration") or 0) > max_dur:
                return "error", ("unsupported", "Video is too long for cloud mode")
            clean = ydl.sanitize_info(info)
        public = to_media_info(clean, url, int(_worker_opts.get("max_height") or 1080))
        blob = zlib.compress(json.dumps(clean, separators=(",", ":")).encode(), 6)
        return "ok", (public, blob)
    except Exception as e:  # yt_dlp.utils.DownloadError and anything else
        text = "\n".join(logger.errors) or str(e)
        return "error", classify_error(text)


# ═════════════════════════════════════════════════════════════════════════════
# Main-process side
# ═════════════════════════════════════════════════════════════════════════════

K = TypeVar("K", bound=Hashable)
V = TypeVar("V")


class TTLCache(Generic[K, V]):
    """LRU bounded by entry count, with a per-entry TTL (media URLs expire)."""

    def __init__(self, max_size: int, ttl_s: float, clock: Callable[[], float] = time.monotonic) -> None:
        self.max_size = max_size
        self.ttl = ttl_s
        self.clock = clock
        self._d: OrderedDict[K, tuple[float, V]] = OrderedDict()
        self.hits = 0
        self.misses = 0

    def get(self, key: K) -> V | None:
        item = self._d.get(key)
        if item is None:
            self.misses += 1
            return None
        expires, value = item
        if expires <= self.clock():
            del self._d[key]
            self.misses += 1
            return None
        self._d.move_to_end(key)
        self.hits += 1
        return value

    def put(self, key: K, value: V) -> None:
        self._d[key] = (self.clock() + self.ttl, value)
        self._d.move_to_end(key)
        while len(self._d) > self.max_size:
            self._d.popitem(last=False)

    def __len__(self) -> int:
        return len(self._d)


class SingleFlight(Generic[K, V]):
    """Coalesce concurrent calls with the same key into one execution.

    The work runs in its own task; callers await it through ``shield`` so one
    impatient caller disconnecting doesn't cancel the work for the others.
    """

    def __init__(self) -> None:
        self._inflight: dict[K, asyncio.Task[V]] = {}
        self.coalesced = 0

    async def do(self, key: K, fn: Callable[[], Awaitable[V]]) -> V:
        task = self._inflight.get(key)
        if task is None:
            task = asyncio.ensure_future(fn())
            self._inflight[key] = task
            task.add_done_callback(functools.partial(self._forget, key))
        else:
            self.coalesced += 1
        return await asyncio.shield(task)

    def _forget(self, key: K, _task: object) -> None:
        self._inflight.pop(key, None)

    def __len__(self) -> int:
        return len(self._inflight)


@dataclass
class ProbeResult:
    info: dict[str, Any]
    info_json_z: bytes
    created: float = field(default_factory=time.time)

    def info_json(self) -> bytes:
        return zlib.decompress(self.info_json_z)


Runner = Callable[[str], Awaitable[tuple[str, Any]]]


class Extractor:
    """probe(url) = cache -> single-flight -> extract slot -> worker process."""

    def __init__(self, s: Settings, admission: AdmissionController, runner: Runner | None = None) -> None:
        self.s = s
        self.admission = admission
        self.cache: TTLCache[str, ProbeResult] = TTLCache(s.probe_cache_size, s.probe_cache_ttl_s)
        self.flight: SingleFlight[str, ProbeResult] = SingleFlight()
        self._pool: ProcessPoolExecutor | None = None
        self._runner = runner or self._run_in_pool
        self._background: set[asyncio.Task[Any]] = set()

    # -- pool lifecycle ------------------------------------------------------
    def _make_pool(self) -> ProcessPoolExecutor:
        method = "forkserver" if sys.platform.startswith("linux") else "spawn"
        opts = {
            "allowed_extractors": self.s.ytdlp_extractor_patterns,
            "max_height": self.s.max_height,
            "max_duration": self.s.max_duration_s,
        }
        return ProcessPoolExecutor(
            max_workers=self.admission.budget.extract_workers,
            mp_context=multiprocessing.get_context(method),
            initializer=worker_init,
            initargs=(opts,),
            max_tasks_per_child=self.s.max_tasks_per_child,
        )

    async def start(self) -> None:
        if self._runner != self._run_in_pool:
            return
        self._pool = self._make_pool()
        loop = asyncio.get_running_loop()
        n = self.admission.budget.extract_workers
        # Submitting n concurrent tasks makes the executor spawn all n workers now.
        pids = await asyncio.gather(*(loop.run_in_executor(self._pool, worker_warm) for _ in range(n)))
        log.info("extract pool warm: %d workers (pids %s)", len(set(pids)), sorted(set(pids)))

    async def shutdown(self) -> None:
        if self._pool is not None:
            self._pool.shutdown(wait=False, cancel_futures=True)
            self._pool = None

    async def _run_in_pool(self, url: str) -> tuple[str, Any]:
        if self._pool is None:
            self._pool = self._make_pool()
        loop = asyncio.get_running_loop()
        try:
            return await asyncio.wrap_future(self._pool.submit(worker_extract, url), loop=loop)
        except BrokenProcessPool:
            log.error("extract pool broke (worker killed?); recreating")
            self._pool.shutdown(wait=False, cancel_futures=True)
            self._pool = self._make_pool()
            return "error", ("unknown", "Extractor crashed; please retry")

    # -- API -----------------------------------------------------------------
    async def probe(self, url: str) -> ProbeResult:
        cached = self.cache.get(url)
        if cached is not None:
            return cached
        return await self.flight.do(url, lambda: self._extract(url))

    async def _extract(self, url: str) -> ProbeResult:
        ticket = await self.admission.acquire("extract")
        work = asyncio.ensure_future(self._runner(url))
        try:
            status, payload = await asyncio.wait_for(asyncio.shield(work), self.s.extract_timeout_s)
        except TimeoutError:
            # The worker can't be interrupted; keep its slot reserved until it
            # really finishes so capacity accounting stays honest.
            self._release_when_done(work, ticket)
            raise ExtractError("network", "Extraction timed out") from None
        except BaseException:
            self._release_when_done(work, ticket)
            raise
        self.admission.release(ticket)
        if status != "ok":
            kind, message = payload
            raise ExtractError(kind, message)
        public, blob = payload
        result = ProbeResult(public, blob)
        self.cache.put(url, result)
        return result

    def _release_when_done(self, work: asyncio.Future[Any], ticket: Ticket) -> None:
        def done(_f: asyncio.Future[Any] | Future[Any]) -> None:
            self.admission.release(ticket)
            if not _f.cancelled():
                _f.exception()  # mark retrieved

        if work.done():
            done(work)
        else:
            work.add_done_callback(done)

    def snapshot(self) -> dict[str, int]:
        return {
            "cached": len(self.cache),
            "cacheHits": self.cache.hits,
            "cacheMisses": self.cache.misses,
            "inflight": len(self.flight),
            "coalesced": self.flight.coalesced,
        }
