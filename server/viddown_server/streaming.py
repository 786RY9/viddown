"""Zero-disk streaming pipeline.

::

    yt-dlp -f <video> -o - ──► /run/viddown/job-x/v.fifo ─┐
    yt-dlp -f <audio> -o - ──► /run/viddown/job-x/a.fifo ─┼─► ffmpeg -c copy -f mp4 (fragmented) pipe:1
                                                           └─► PipelineResponse (64 KB chunks) ─► client

The FIFOs live in tmpfs (``RuntimeDirectory=viddown``), so media only ever sits
in kernel pipe buffers (64 KB each). If the client reads slowly, the pipes
fill, ffmpeg blocks, and yt-dlp blocks: memory per stream stays bounded.

Every process gets its own session (``start_new_session``) so ``os.killpg``
also takes down helpers they spawn (yt-dlp's own ffmpeg for clips). The job
dir is always removed in ``finally``.

Clips use a single yt-dlp with ``--download-sections`` (its ffmpeg seeks inside
the remote file, so only the section is fetched) writing Matroska into one
FIFO; "precise" clips re-encode there and are admitted as *transcode* jobs.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import re
import shutil
import signal
import tempfile
import time
import unicodedata
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar, Literal
from urllib.parse import quote

from starlette.responses import Response
from starlette.types import Receive, Scope, Send

from .config import Settings
from .extract import classify_error
from .security import StreamGrant

log = logging.getLogger(__name__)

FMP4_FLAGS = "frag_keyframe+empty_moov+default_base_moof"
# Audio codecs the MP4 muxer can stream-copy; anything else is re-encoded to AAC.
MP4_AUDIO_COPY = ("mp4a", "aac", "opus", "mp3", "ac-3", "ec-3", "flac", "alac")
YTDLP_STREAM_FLAGS = [
    "--no-part",
    "--quiet",
    "--no-warnings",
    "--no-progress",
    "--no-playlist",
    "--socket-timeout", "20",
    "--retries", "3",
    "--fragment-retries", "10",
    "--concurrent-fragments", "4",
    "--no-cache-dir",
]  # fmt: skip


class PlanError(ValueError):
    """The grant can't be turned into a stream (e.g. format vanished)."""


@dataclass(frozen=True)
class StreamPlan:
    job_class: Literal["stream", "transcode"]
    container: Literal["mp4", "mp3"]
    media_type: str
    filename: str
    # One yt-dlp argv per FIFO (without the leading command, -o and info json).
    inputs: list[list[str]]
    # ffmpeg argv between the input files and the output; inputs are inserted.
    ffmpeg_out: list[str]
    estimated_bytes: int | None = None


def safe_filename(title: str, ext: str, max_len: int = 120) -> str:
    title = unicodedata.normalize("NFC", title)
    title = re.sub(r'[\x00-\x1f\x7f<>:"/\\|?*]+', " ", title)
    title = re.sub(r"\s+", " ", title).strip(" .") or "video"
    return f"{title[:max_len].rstrip(' .')}.{ext}"


def content_disposition(filename: str) -> str:
    ascii_name = filename.encode("ascii", "ignore").decode() or f"video.{filename.rsplit('.', 1)[-1]}"
    ascii_name = ascii_name.replace('"', "").replace("\\", "")
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(filename, safe='')}"


def estimate_bytes(fmt: dict[str, Any], duration: float | None) -> int | None:
    size = fmt.get("filesize") or fmt.get("filesize_approx")
    if size:
        return int(size)
    if fmt.get("tbr") and duration:
        return int(fmt["tbr"] * 1000 / 8 * duration)
    return None


def _has(codec: str | None) -> bool:
    return bool(codec) and codec != "none"


def build_plan(grant: StreamGrant, info: dict[str, Any], s: Settings) -> StreamPlan:
    """Pure: grant + public MediaInfo -> processes to run. Unit-tested on any OS."""
    title = str(info.get("title") or "video")
    duration = info.get("durationSec")
    clip = grant.clip_start is not None and grant.clip_end is not None
    section: list[str] = []
    if clip:
        assert grant.clip_start is not None and grant.clip_end is not None
        section = ["--download-sections", f"*{grant.clip_start}-{grant.clip_end}"]

    if grant.kind == "audio":
        ffmpeg_out = ["-map", "0:a:0", "-vn", "-c:a", "libmp3lame", "-q:a", "2", "-write_xing", "0", "-f", "mp3"]
        args = ["-f", "bestaudio/best", *section]
        if clip:
            args += ["--downloader-args", "ffmpeg_o:-vn -f matroska"]
        return StreamPlan("stream", "mp3", "audio/mpeg", safe_filename(title, "mp3"), [args], ffmpeg_out)

    fmt = next((f for f in info.get("formats") or [] if f.get("format_id") == grant.format_id), None)
    if fmt is None or not _has(fmt.get("vcodec")):
        raise PlanError("requested format is not available")
    if (fmt.get("height") or 0) > s.max_height:
        raise PlanError("requested quality exceeds the cloud limit")
    fid = grant.format_id
    combined = _has(fmt.get("acodec"))
    est = estimate_bytes(fmt, duration)
    mp4 = ["-movflags", FMP4_FLAGS, "-f", "mp4"]
    name = safe_filename(title, "mp4")

    if clip:
        selector = fid if combined else f"{fid}+bestaudio[ext=m4a]/{fid}+bestaudio/{fid}"
        if grant.precise:
            # Re-encode at the cut points (yt-dlp's ffmpeg); our ffmpeg then just remuxes.
            dl_args = "ffmpeg_o:-f matroska -c:v libx264 -preset veryfast -crf 23 -c:a aac -b:a 160k"
            extra = ["--force-keyframes-at-cuts"]
        else:
            dl_args = "ffmpeg_o:-f matroska"
            extra = []
        args = ["-f", selector, *section, *extra, "--downloader-args", dl_args]
        out = ["-map", "0:v:0", "-map", "0:a:0?", "-c:v", "copy", *_audio_codec(fmt, combined), *mp4]
        return StreamPlan("transcode" if grant.precise else "stream", "mp4", "video/mp4", name, [args], out, None)

    if combined:
        out = ["-map", "0:v:0", "-map", "0:a:0?", "-c:v", "copy", *_audio_codec(fmt, True), *mp4]
        return StreamPlan("stream", "mp4", "video/mp4", name, [["-f", fid]], out, est)

    out = ["-map", "0:v:0", "-map", "1:a:0?", "-c", "copy", *mp4]
    inputs = [["-f", fid], ["-f", "bestaudio[ext=m4a]/bestaudio[acodec^=mp4a]/bestaudio"]]
    return StreamPlan("stream", "mp4", "video/mp4", name, inputs, out, est)


def _audio_codec(fmt: dict[str, Any], combined: bool) -> list[str]:
    acodec = str(fmt.get("acodec") or "")
    if combined and acodec and not acodec.startswith(MP4_AUDIO_COPY):
        return ["-c:a", "aac", "-b:a", "160k"]
    return ["-c:a", "copy"]


# ═════════════════════════════════════════════════════════════════════════════
# Process supervision (Linux only)
# ═════════════════════════════════════════════════════════════════════════════


def child_env() -> dict[str, str]:
    """Children never see our secrets."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("VIDDOWN_")}
    env.setdefault("LANG", "C.UTF-8")
    return env


def cleanup_stale_jobs(runtime_dir: Path) -> int:
    """Remove job dirs left behind by a crash (the service owns this dir)."""
    n = 0
    if runtime_dir.is_dir():
        for d in runtime_dir.glob("job-*"):
            shutil.rmtree(d, ignore_errors=True)
            n += 1
    return n


@dataclass
class _Proc:
    name: str
    proc: asyncio.subprocess.Process
    stderr: deque[str] = field(default_factory=lambda: deque(maxlen=40))
    reader: asyncio.Task[None] | None = None


class Pipeline:
    """Owns the job dir, the FIFOs and every process of one stream."""

    live: ClassVar[set[Pipeline]] = set()  # for shutdown sweeps

    def __init__(self, plan: StreamPlan, info_json: bytes, s: Settings) -> None:
        self.plan = plan
        self.info_json = info_json
        self.s = s
        self.dir: Path | None = None
        self.procs: list[_Proc] = []
        self.ffmpeg: _Proc | None = None
        self.killed: str | None = None
        self._closed = False

    async def start(self) -> None:
        if not hasattr(os, "mkfifo"):
            raise RuntimeError("streaming requires a POSIX host (named pipes)")
        Pipeline.live.add(self)
        self.s.runtime_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.dir = Path(tempfile.mkdtemp(prefix="job-", dir=self.s.runtime_dir))
        info_path = self.dir / "info.json"
        info_path.write_bytes(self.info_json)
        os.chmod(info_path, 0o600)
        fifos = []
        for i in range(len(self.plan.inputs)):
            p = self.dir / f"in{i}.fifo"
            os.mkfifo(p, 0o600)
            fifos.append(p)

        env = child_env()
        ff_cmd = [self.s.ffmpeg_bin, "-hide_banner", "-nostdin", "-loglevel", "error"]
        for p in fifos:
            ff_cmd += ["-i", str(p)]
        ff_cmd += [*self.plan.ffmpeg_out, "pipe:1"]
        # ffmpeg first: it is the only reader of the FIFOs and the producer of
        # our output. stdout uses asyncio's 64 KB reader limit -> backpressure.
        self.ffmpeg = await self._spawn("ffmpeg", ff_cmd, asyncio.subprocess.PIPE, env)

        for i, (p, args) in enumerate(zip(fifos, self.plan.inputs)):
            # Linux allows O_RDWR on a FIFO without blocking for a peer (fifo(7)),
            # which removes any open() ordering dance with ffmpeg. The child's fd
            # is its stdout; we close ours right after spawning.
            fd = os.open(p, os.O_RDWR | os.O_CLOEXEC)
            try:
                cmd = [
                    *self.s.ytdlp_cmd,
                    *YTDLP_STREAM_FLAGS,
                    "--ffmpeg-location", self.s.ffmpeg_bin,
                    "--load-info-json", str(info_path),
                    *args,
                    "-o", "-",
                ]  # fmt: skip
                await self._spawn(f"yt-dlp#{i}", cmd, fd, env)
            finally:
                os.close(fd)

    async def _spawn(self, name: str, cmd: list[str], stdout: int, env: dict[str, str]) -> _Proc:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=stdout,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
            env=env,
            cwd=str(self.dir),
        )
        p = _Proc(name, proc)
        p.reader = asyncio.create_task(self._drain_stderr(p))
        self.procs.append(p)
        return p

    @staticmethod
    async def _drain_stderr(p: _Proc) -> None:
        assert p.proc.stderr is not None
        while True:
            line = await p.proc.stderr.readline()
            if not line:
                return
            p.stderr.append(line.decode("utf-8", "replace").rstrip())

    async def read(self, n: int) -> bytes:
        assert self.ffmpeg is not None and self.ffmpeg.proc.stdout is not None
        return await self.ffmpeg.proc.stdout.read(n)

    def kill(self, reason: str = "killed") -> None:
        """Kill every process group of the job. Idempotent, safe from callbacks."""
        if self.killed is None:
            self.killed = reason
        for p in self.procs:
            if p.proc.returncode is None:
                with contextlib.suppress(ProcessLookupError, PermissionError):
                    os.killpg(p.proc.pid, signal.SIGKILL)

    async def wait_exit(self, timeout: float = 5.0) -> int | None:
        """ffmpeg's exit code after EOF on its stdout."""
        if self.ffmpeg is None:
            return None
        try:
            return await asyncio.wait_for(self.ffmpeg.proc.wait(), timeout)
        except TimeoutError:
            return None

    async def settle(self, timeout: float = 3.0) -> None:
        """Kill the job and give the stderr readers a moment to collect the
        final error lines (used before reporting why a stream never started)."""
        self.kill(self.killed or "no_output")
        readers = [p.reader for p in self.procs if p.reader is not None]
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(asyncio.gather(*readers, return_exceptions=True), timeout)

    def error(self) -> tuple[str, str]:
        """Best guess at why the job failed, classified like the client does."""
        text = "\n".join(line for p in self.procs if p.name != "ffmpeg" for line in p.stderr)
        if not text.strip() and self.ffmpeg is not None:
            text = "\n".join(self.ffmpeg.stderr)
        return classify_error(text or "stream failed")

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self.kill(self.killed or "closed")
            for p in self.procs:
                with contextlib.suppress(TimeoutError, ProcessLookupError):
                    await asyncio.wait_for(p.proc.wait(), 5)
                if p.reader is not None:
                    p.reader.cancel()
        finally:
            if self.dir is not None:
                shutil.rmtree(self.dir, ignore_errors=True)
            Pipeline.live.discard(self)


# ═════════════════════════════════════════════════════════════════════════════
# ASGI response
# ═════════════════════════════════════════════════════════════════════════════


class PipelineResponse(Response):
    """Streams a running pipeline with stall / size / time caps.

    Unlike Starlette's StreamingResponse, every ``send`` is bounded by the stall
    timeout: a client that stops reading (TCP window 0) can't pin a slot.
    An aborted stream ends *without* the terminating chunk, so the client sees
    a truncated transfer instead of a silently short file.
    """

    def __init__(
        self,
        pipeline: Pipeline,
        first_chunk: bytes,
        s: Settings,
        on_bytes: Callable[[int], None],
        on_close: Callable[[str], Awaitable[None]],
    ) -> None:
        plan = pipeline.plan
        super().__init__(
            status_code=200,
            media_type=plan.media_type,
            headers={
                "content-disposition": content_disposition(plan.filename),
                "cache-control": "no-store",
                "x-content-type-options": "nosniff",
                "x-accel-buffering": "no",
            },
        )
        # Response() with no body adds "content-length: 0"; the real length is
        # unknown (remux/transcode output), so drop it and stream chunked.
        self.raw_headers = [(k, v) for k, v in self.raw_headers if k.lower() != b"content-length"]
        self.pipeline = pipeline
        self.first = first_chunk
        self.s = s
        self.on_bytes = on_bytes
        self.on_close = on_close
        self.outcome = "pending"
        self.sent = 0

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        pipe, s = self.pipeline, self.s
        started = time.monotonic()

        async def watch_disconnect() -> None:
            while True:
                msg = await receive()
                if msg["type"] == "http.disconnect":
                    pipe.kill("disconnect")
                    return

        watcher = asyncio.create_task(watch_disconnect())
        try:
            await send({"type": "http.response.start", "status": self.status_code, "headers": self.raw_headers})
            chunk = self.first
            while chunk:
                async with asyncio.timeout(s.stall_timeout_s):
                    await send({"type": "http.response.body", "body": chunk, "more_body": True})
                self.sent += len(chunk)
                self.on_bytes(len(chunk))
                if self.sent >= s.max_stream_bytes:
                    self.outcome = "max_bytes"
                    return
                if time.monotonic() - started >= s.max_stream_s:
                    self.outcome = "max_time"
                    return
                async with asyncio.timeout(s.stall_timeout_s):
                    chunk = await pipe.read(s.chunk_size)
            # EOF from ffmpeg: success only if it exited cleanly and nobody killed it.
            rc = await pipe.wait_exit()
            if rc == 0 and pipe.killed is None:
                await send({"type": "http.response.body", "body": b"", "more_body": False})
                self.outcome = "ok"
            else:
                self.outcome = pipe.killed or f"ffmpeg_exit_{rc}"
        except TimeoutError:
            self.outcome = "stall"
        except OSError:  # includes uvicorn's ClientDisconnected
            self.outcome = "disconnect"
        except asyncio.CancelledError:
            self.outcome = pipe.killed or "cancelled"
            raise
        finally:
            watcher.cancel()
            pipe.kill(self.outcome)
            await self.on_close(self.outcome)
            log.info(
                "stream %s: %s, %.1f MB in %.1f s",
                pipe.plan.filename,
                self.outcome,
                self.sent / 1e6,
                time.monotonic() - started,
            )
