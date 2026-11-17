"""End-to-end streaming through real yt-dlp + ffmpeg + FIFOs (Linux only).

Runs in Docker (see Dockerfile.test). Offline by default: a local HTTP server
serves DASH test assets generated with ffmpeg, and the server runs with the
test-only escape hatches (generic extractor + private URLs). Set
VIDDOWN_NETWORK_TESTS=1 to also stream Blender's Big Buck Bunny from YouTube.
"""

from __future__ import annotations

import contextlib
import dataclasses
import functools
import http.server
import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
import uvicorn
from conftest import make_settings

from viddown_server.app import create_app
from viddown_server.config import Settings

pytestmark = [
    pytest.mark.linux,
    pytest.mark.skipif(not sys.platform.startswith("linux"), reason="needs Linux FIFOs/process groups"),
    pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg"),
]

BBB = "https://www.youtube.com/watch?v=aqz-KE-bpKQ"


# ── fixtures ────────────────────────────────────────────────────────────────


def make_dash(out: Path, *, seconds: int, size: str, noisy: bool) -> None:
    out.mkdir(parents=True, exist_ok=True)
    vf = f"testsrc2=size={size}:rate=25" + (",noise=alls=80:allf=t+u" if noisy else "")
    subprocess.run(
        [
            "ffmpeg", "-v", "error", "-y",
            "-f", "lavfi", "-i", vf,
            "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000",
            "-t", str(seconds), "-map", "0:v", "-map", "1:a",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            *(["-qp", "4"] if noisy else ["-crf", "28"]),
            "-g", "25", "-keyint_min", "25", "-sc_threshold", "0",
            "-c:a", "aac", "-b:a", "128k",
            "-f", "dash", "-seg_duration", "2", "-use_template", "1", "-use_timeline", "0",
            "-adaptation_sets", "id=0,streams=v id=1,streams=a",
            str(out / "manifest.mpd"),
        ],
        check=True,
    )  # fmt: skip


@pytest.fixture(scope="module")
def media_server(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    root = tmp_path_factory.mktemp("media")
    make_dash(root / "small", seconds=20, size="640x360", noisy=False)
    make_dash(root / "big", seconds=12, size="1280x720", noisy=True)  # ~100+ MB: overflows socket buffers

    handler = functools.partial(QuietHandler, directory=str(root))
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args: object) -> None:
        pass


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@contextlib.contextmanager
def running_server(s: Settings) -> Iterator[str]:
    port = free_port()
    s = dataclasses.replace(s, port=port)
    config = uvicorn.Config(create_app(s), host="127.0.0.1", port=port, log_level="warning", access_log=False)
    server = uvicorn.Server(config)
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    deadline = time.time() + 60
    while not server.started:
        if time.time() > deadline or not t.is_alive():
            raise RuntimeError("server did not start")
        time.sleep(0.05)
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        t.join(30)


def offline_settings(tmp_path: Path, **kw: object) -> Settings:
    return make_settings(
        tmp_path,
        unsafe_allow_generic=True,
        unsafe_allow_private_urls=True,
        allowed_extractors=("generic",),
        extract_workers=1,
        **kw,  # type: ignore[arg-type]
    )


def job_processes(runtime_dir: Path) -> list[str]:
    """Live yt-dlp / ffmpeg processes that belong to a streaming job."""
    out = subprocess.run(["ps", "-eo", "pid,stat,args"], capture_output=True, text=True).stdout
    return [
        line for line in out.splitlines()[1:] if str(runtime_dir) in line and " Z" not in line and "ps -eo" not in line
    ]


def wait_clean(runtime_dir: Path, timeout: float = 10) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not job_processes(runtime_dir) and not list(runtime_dir.glob("job-*")):
            return
        time.sleep(0.2)
    raise AssertionError(f"leftovers: {job_processes(runtime_dir)} {list(runtime_dir.glob('job-*'))}")


def ffprobe(path: Path) -> dict[str, object]:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout  # fmt: skip
    return json.loads(out)  # type: ignore[no-any-return]


def probe(c: httpx.Client, url: str, **body: object) -> dict[str, object]:
    r = c.post("/v1/probe", json={"url": url, **body}, timeout=120)
    assert r.status_code == 200, r.text
    return r.json()  # type: ignore[no-any-return]


# ── offline tests ───────────────────────────────────────────────────────────


def test_full_video_two_fifos_is_valid_fragmented_mp4(tmp_path: Path, media_server: str) -> None:
    s = offline_settings(tmp_path)
    with running_server(s) as base, httpx.Client(base_url=base) as c:
        info = probe(c, f"{media_server}/small/manifest.mpd")
        assert info["videoFormats"], info
        out = tmp_path / "out.mp4"
        with c.stream("GET", info["stream"]["path"], timeout=60) as r:  # type: ignore[index]
            assert r.status_code == 200 and r.headers["content-type"] == "video/mp4"
            assert "attachment" in r.headers["content-disposition"]
            with out.open("wb") as f:
                for chunk in r.iter_bytes():
                    f.write(chunk)
        head = out.read_bytes()[:64]
        assert head[4:8] == b"ftyp"
        meta = ffprobe(out)
        kinds = sorted(st["codec_type"] for st in meta["streams"])  # type: ignore[index, union-attr]
        assert kinds == ["audio", "video"]
        assert abs(float(meta["format"]["duration"]) - 20) < 1.5  # type: ignore[index]
        wait_clean(s.runtime_dir)


def test_audio_is_mp3(tmp_path: Path, media_server: str) -> None:
    s = offline_settings(tmp_path)
    with running_server(s) as base, httpx.Client(base_url=base) as c:
        info = probe(c, f"{media_server}/small/manifest.mpd", kind="audio")
        r = c.get(info["stream"]["path"], timeout=60)  # type: ignore[index]
        assert r.status_code == 200 and r.headers["content-type"] == "audio/mpeg"
        out = tmp_path / "a.mp3"
        out.write_bytes(r.content)
        meta = ffprobe(out)
        assert meta["format"]["format_name"] == "mp3"  # type: ignore[index]
        wait_clean(s.runtime_dir)


@pytest.mark.parametrize("precise", [False, True])
def test_clip(tmp_path: Path, media_server: str, precise: bool) -> None:
    s = offline_settings(tmp_path)
    with running_server(s) as base, httpx.Client(base_url=base) as c:
        info = probe(c, f"{media_server}/small/manifest.mpd", clip={"start": "0:06", "end": "0:11", "precise": precise})
        r = c.get(info["stream"]["path"], timeout=120)  # type: ignore[index]
        assert r.status_code == 200, r.text
        out = tmp_path / "clip.mp4"
        out.write_bytes(r.content)
        dur = float(ffprobe(out)["format"]["duration"])  # type: ignore[index]
        # Fast mode cuts on keyframes (1 s GOP here); precise re-encodes.
        assert abs(dur - 5) < (0.5 if precise else 2.1), dur
        wait_clean(s.runtime_dir)


def test_client_disconnect_kills_everything(tmp_path: Path, media_server: str) -> None:
    s = offline_settings(tmp_path)
    with running_server(s) as base, httpx.Client(base_url=base) as c:
        info = probe(c, f"{media_server}/big/manifest.mpd")
        with c.stream("GET", info["stream"]["path"], timeout=60) as r:  # type: ignore[index]
            got = 0
            for chunk in r.iter_bytes():
                got += len(chunk)
                if got > 512 * 1024:
                    break
            assert job_processes(s.runtime_dir), "pipeline should be running mid-stream"
        # Response closed -> server sees the disconnect and kills the job.
        wait_clean(s.runtime_dir, timeout=15)
        assert c.get("/healthz").json()["status"] == "ok"


def test_stalled_reader_is_cut_off(tmp_path: Path, media_server: str) -> None:
    s = offline_settings(tmp_path, stall_timeout_s=3.0)
    with running_server(s) as base, httpx.Client(base_url=base) as c:
        path = probe(c, f"{media_server}/big/manifest.mpd")["stream"]["path"]  # type: ignore[index]
        port = int(base.rsplit(":", 1)[1])
        sock = socket.socket()
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)
        sock.connect(("127.0.0.1", port))
        sock.sendall(f"GET {path} HTTP/1.1\r\nHost: x\r\n\r\n".encode())
        assert b"200 OK" in sock.recv(4096)
        assert job_processes(s.runtime_dir), "pipeline should be running"
        # Now never read again: socket buffers fill, send() blocks, stall timer fires.
        t0 = time.time()
        wait_clean(s.runtime_dir, timeout=30)
        elapsed = time.time() - t0
        sock.close()
        # Cut off by the 3 s stall timer (plus the time to fill the socket buffers).
        assert 2 <= elapsed < 30, elapsed


def test_byte_cap_truncates_and_cleans_up(tmp_path: Path, media_server: str) -> None:
    s = offline_settings(tmp_path, max_stream_bytes=1024 * 1024)
    with running_server(s) as base, httpx.Client(base_url=base) as c:
        info = probe(c, f"{media_server}/big/manifest.mpd")
        got = 0
        with pytest.raises(httpx.RemoteProtocolError):  # no terminating chunk: client knows it's incomplete
            with c.stream("GET", info["stream"]["path"], timeout=60) as r:  # type: ignore[index]
                for chunk in r.iter_bytes():
                    got += len(chunk)
        assert 1024 * 1024 <= got < 2 * 1024 * 1024
        wait_clean(s.runtime_dir)
        # healthz detail is only for local callers: this client is 127.0.0.1
        egress = c.get("/healthz").json()["egress"]["bytes"]
        assert egress >= 1024 * 1024


def test_saturation_returns_503_with_retry_after(tmp_path: Path, media_server: str) -> None:
    s = offline_settings(tmp_path, stream_slots=1, max_waiters=0, max_streams_per_ip=5)
    with running_server(s) as base, httpx.Client(base_url=base) as c:
        info = probe(c, f"{media_server}/big/manifest.mpd")
        with c.stream("GET", info["stream"]["path"], timeout=60) as r1:  # type: ignore[index]
            assert r1.status_code == 200
            next(r1.iter_bytes())
            r2 = c.get(info["stream"]["path"], timeout=30)  # type: ignore[index]
            assert r2.status_code == 503 and r2.headers["retry-after"] == "30"
        wait_clean(s.runtime_dir, timeout=15)


# ── network test: the real thing ────────────────────────────────────────────


@pytest.mark.network
@pytest.mark.skipif(os.environ.get("VIDDOWN_NETWORK_TESTS") != "1", reason="set VIDDOWN_NETWORK_TESTS=1")
def test_youtube_big_buck_bunny_stream(tmp_path: Path) -> None:
    s = make_settings(tmp_path, extract_workers=1)
    with running_server(s) as base, httpx.Client(base_url=base) as c:
        t0 = time.time()
        info = probe(c, BBB, formatId=None)
        print(
            f"\nprobe: {time.time() - t0:.1f}s, title={info['title']!r}, formats={[f['format_id'] for f in info['videoFormats']]}"
        )  # type: ignore[union-attr]
        # Stream the 720p-or-lower option for ~8 s.
        fmt = next(f for f in info["videoFormats"] if f["height"] <= 720)  # type: ignore[index, union-attr]
        path = f"/v1/stream/{info['streamTokens'][fmt['format_id']]}"  # type: ignore[index]
        out = tmp_path / "bbb.mp4"
        t0 = time.time()
        ttfb = None
        with c.stream("GET", path, timeout=120) as r, out.open("wb") as f:
            assert r.status_code == 200, r.read()
            for chunk in r.iter_bytes():
                ttfb = ttfb or time.time() - t0
                f.write(chunk)
                if time.time() - t0 > 8:
                    break
        size = out.stat().st_size
        print(f"stream {fmt['format_id']} ({fmt['height']}p): ttfb {ttfb:.1f}s, {size / 1e6:.1f} MB in 8 s")
        assert out.read_bytes()[4:8] == b"ftyp" and size > 500_000
        meta = ffprobe(out)
        codecs = sorted(st["codec_name"] for st in meta["streams"])  # type: ignore[index, union-attr]
        print(f"codecs: {codecs}")
        assert any(st["codec_type"] == "audio" for st in meta["streams"])  # type: ignore[index, union-attr]
        wait_clean(s.runtime_dir, timeout=15)

        # A 5-second fast clip of the same video, end to end.
        info = probe(c, BBB, clip={"start": "1:00", "end": "1:05"})
        r = c.get(info["stream"]["path"], timeout=180)  # type: ignore[index]
        assert r.status_code == 200, r.text
        clip = tmp_path / "clip.mp4"
        clip.write_bytes(r.content)
        dur = float(ffprobe(clip)["format"]["duration"])  # type: ignore[index]
        print(f"clip: {len(r.content) / 1e6:.1f} MB, duration {dur:.2f}s")
        assert 3 < dur < 12
        wait_clean(s.runtime_dir, timeout=15)
