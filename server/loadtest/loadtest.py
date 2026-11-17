#!/usr/bin/env python3
"""Load test for the viddown server: probes + streams with fast, slow and abandoning readers.

    python loadtest/loadtest.py --base http://127.0.0.1:8000 \
        --url https://www.youtube.com/watch?v=aqz-KE-bpKQ --clients 40 --duration 120

Each virtual client loops: probe -> stream for --stream-seconds. A fraction of
clients read slowly (--slow-kbps) to exercise backpressure and the stall timer,
and a fraction hang up after ~1 s to exercise disconnect cleanup.

When pointed at 127.0.0.1 (a trusted proxy) every virtual client sends its own
X-Forwarded-For, so per-IP limits apply per virtual client rather than to the
whole test. The script samples /healthz (local callers get admission detail)
and prints a summary: status codes, 503s with Retry-After, TTFB percentiles,
throughput, and the peak number of live pipelines.

Pass criteria to look for (see server/README.md, "Load testing"):
  * saturation produces 503 + Retry-After, never 500s or hangs;
  * after the run, /healthz shows 0 pipelines and every class at 0 active;
  * on Linux: no orphaned ffmpeg / yt-dlp (`pgrep -fa 'ffmpeg|yt_dlp'`) and no
    job-* dirs left in the runtime dir (zero-disk check).
"""

from __future__ import annotations

import argparse
import asyncio
import random
import statistics
import time
from collections import Counter
from dataclasses import dataclass, field

import httpx


@dataclass
class Stats:
    codes: Counter[str] = field(default_factory=Counter)
    retry_after: Counter[str] = field(default_factory=Counter)
    ttfb: list[float] = field(default_factory=list)
    bytes: int = 0
    streams_ok: int = 0
    errors: Counter[str] = field(default_factory=Counter)
    peak_pipelines: int = 0
    peak_active: dict[str, int] = field(default_factory=dict)
    max_waiting: dict[str, int] = field(default_factory=dict)


def pick_path(info: dict, max_height: int) -> str:
    tokens = info.get("streamTokens") or {}
    for f in info.get("videoFormats") or []:
        if (f.get("height") or 0) <= max_height and f.get("format_id") in tokens:
            return f"/v1/stream/{tokens[f['format_id']]}"
    return str(info["stream"]["path"])


async def client_loop(i: int, args: argparse.Namespace, stats: Stats, stop: float) -> None:
    fake_ip = f"10.{(i >> 16) & 255}.{(i >> 8) & 255}.{i & 255 or 1}"
    headers = {"X-Forwarded-For": fake_ip}
    rnd = random.Random(i)
    kind = (
        "slow" if rnd.random() < args.slow_fraction else "abandon" if rnd.random() < args.abandon_fraction else "fast"
    )
    timeout = httpx.Timeout(connect=10, read=args.read_timeout, write=10, pool=10)
    async with httpx.AsyncClient(base_url=args.base, headers=headers, timeout=timeout) as c:
        while time.monotonic() < stop:
            try:
                r = await c.post("/v1/probe", json={"url": args.url})
                stats.codes[f"probe {r.status_code}"] += 1
                if r.status_code != 200:
                    await backoff(r, stats, "probe")
                    continue
                path = pick_path(r.json(), args.max_height)
                t0 = time.monotonic()
                async with c.stream("GET", path) as s:
                    stats.codes[f"stream {s.status_code}"] += 1
                    if s.status_code != 200:
                        await s.aread()
                        await backoff(s, stats, "stream")
                        continue
                    first = True
                    budget = 1.0 if kind == "abandon" else args.stream_seconds
                    async for chunk in s.aiter_bytes(16 * 1024):
                        if first:
                            stats.ttfb.append(time.monotonic() - t0)
                            first = False
                        stats.bytes += len(chunk)
                        if kind == "slow":
                            await asyncio.sleep(len(chunk) / (args.slow_kbps * 1024))
                        if time.monotonic() - t0 >= budget:
                            break
                    else:
                        stats.streams_ok += 1
                        continue
                    stats.streams_ok += 1  # we chose to stop: counts as a successful stream
            except (httpx.HTTPError, OSError) as e:
                stats.errors[type(e).__name__] += 1
                await asyncio.sleep(1)


async def backoff(r: httpx.Response, stats: Stats, what: str) -> None:
    ra = r.headers.get("retry-after")
    if r.status_code in (429, 503):
        stats.retry_after[f"{what} {r.status_code} retry-after={'yes' if ra else 'NO'}"] += 1
    # Real clients fall back to their local engine; here we just wait a little.
    await asyncio.sleep(min(float(ra or 2), 5) * random.uniform(0.5, 1.0))


async def sampler(args: argparse.Namespace, stats: Stats, stop: float) -> None:
    async with httpx.AsyncClient(base_url=args.base, timeout=5) as c:
        while time.monotonic() < stop + 5:
            try:
                h = (await c.get("/healthz")).json()
                stats.peak_pipelines = max(stats.peak_pipelines, int(h.get("pipelines", 0)))
                for name, cls in (h.get("admission", {}).get("classes") or {}).items():
                    stats.peak_active[name] = max(stats.peak_active.get(name, 0), cls["active"])
                    stats.max_waiting[name] = max(stats.max_waiting.get(name, 0), cls["waiting"])
            except (httpx.HTTPError, ValueError):
                pass
            await asyncio.sleep(1)


def pct(xs: list[float], p: float) -> float:
    if not xs:
        return float("nan")
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(p / 100 * len(xs)))]


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", default="http://127.0.0.1:8000")
    ap.add_argument("--url", required=True, help="page URL to probe/stream")
    ap.add_argument("--clients", type=int, default=20)
    ap.add_argument("--duration", type=float, default=60, help="seconds")
    ap.add_argument("--stream-seconds", type=float, default=10)
    ap.add_argument("--slow-fraction", type=float, default=0.3)
    ap.add_argument("--slow-kbps", type=float, default=32, help="KB/s for slow readers")
    ap.add_argument("--abandon-fraction", type=float, default=0.2)
    ap.add_argument("--max-height", type=int, default=480)
    ap.add_argument("--read-timeout", type=float, default=120)
    args = ap.parse_args()

    stats = Stats()
    t0 = time.monotonic()
    stop = t0 + args.duration
    await asyncio.gather(
        sampler(args, stats, stop), *(client_loop(i + 1, args, stats, stop) for i in range(args.clients))
    )
    wall = time.monotonic() - t0

    # Let the server finish tearing down, then look at the final state.
    await asyncio.sleep(3)
    async with httpx.AsyncClient(base_url=args.base, timeout=5) as c:
        final = (await c.get("/healthz")).json()

    print(f"\n== viddown load test: {args.clients} clients, {wall:.0f}s ==")
    for k, v in sorted(stats.codes.items()):
        print(f"  {k:<14} {v}")
    for k, v in sorted(stats.retry_after.items()):
        print(f"  {k:<40} {v}")
    if stats.errors:
        print(f"  client errors: {dict(stats.errors)}")
    if stats.ttfb:
        print(
            f"  TTFB p50 {statistics.median(stats.ttfb):.2f}s  p95 {pct(stats.ttfb, 95):.2f}s  max {max(stats.ttfb):.2f}s"
        )
    print(
        f"  streams completed {stats.streams_ok}, {stats.bytes / 1e6:.1f} MB, {stats.bytes * 8 / wall / 1e6:.1f} Mbit/s"
    )
    print(f"  peak pipelines {stats.peak_pipelines}, peak active {stats.peak_active}, max waiting {stats.max_waiting}")
    leftover = final.get("pipelines")
    active = {n: c["active"] for n, c in (final.get("admission", {}).get("classes") or {}).items()}
    print(f"  after run: pipelines={leftover} active={active} status={final.get('status')}")
    server_errors = sum(v for k, v in stats.codes.items() if k.split()[1].startswith("5") and not k.endswith("503"))
    ok = server_errors == 0 and (leftover in (0, None)) and all(v == 0 for v in active.values())
    missing_ra = sum(v for k, v in stats.retry_after.items() if k.endswith("NO"))
    ok = ok and missing_ra == 0
    print("  RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
