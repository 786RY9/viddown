"""LRU/TTL cache, single-flight, Extractor orchestration, error mapping."""

from __future__ import annotations

import asyncio
import dataclasses
import json
import zlib
from typing import Any

import pytest

from viddown_server.config import Settings
from viddown_server.extract import (
    ExtractError,
    Extractor,
    SingleFlight,
    TTLCache,
    classify_error,
    to_media_info,
)
from viddown_server.resources import AdmissionController, Budget, Overloaded

YT_INFO: dict[str, Any] = {
    "id": "aqz-KE-bpKQ",
    "title": "Big Buck Bunny",
    "uploader": "Blender",
    "extractor_key": "Youtube",
    "duration": 635,
    "thumbnail": "https://i.ytimg.com/x.jpg",
    "formats": [
        {"format_id": "sb0", "protocol": "mhtml", "vcodec": "none"},
        {"format_id": "140", "ext": "m4a", "vcodec": "none", "acodec": "mp4a.40.2", "protocol": "https", "url": "SECRET"},
        {"format_id": "137", "ext": "mp4", "vcodec": "avc1", "acodec": "none", "height": 1080, "tbr": 4000, "protocol": "https"},
        {"format_id": "614", "ext": "mp4", "vcodec": "vp9", "acodec": "none", "height": 1080, "tbr": 3000, "protocol": "m3u8_native"},
        {"format_id": "136", "ext": "mp4", "vcodec": "avc1", "acodec": "none", "height": 720, "tbr": 2000, "protocol": "https"},
        {"format_id": "401", "ext": "mp4", "vcodec": "av01", "acodec": "none", "height": 2160, "tbr": 20000, "protocol": "https"},
    ],
}  # fmt: skip


def test_to_media_info_shape_and_no_urls() -> None:
    mi = to_media_info(YT_INFO, "https://youtu.be/aqz-KE-bpKQ", max_height=1080)
    assert set(mi) == {
        "url",
        "id",
        "title",
        "uploader",
        "extractor",
        "durationSec",
        "thumbnail",
        "formats",
        "videoFormats",
    }
    assert mi["extractor"] == "Youtube" and mi["durationSec"] == 635
    assert all("url" not in f for f in mi["formats"])  # media URLs never leave the server
    assert "sb0" not in [f["format_id"] for f in mi["formats"]]
    # filter_video_formats from viddown_core: one per height, DASH over HLS; capped at max_height.
    assert [f["format_id"] for f in mi["videoFormats"]] == ["137", "136"]


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        ("ERROR: [youtube] x: Sign in to confirm you're not a bot", "blocked"),
        ("ERROR: [youtube] x: Video unavailable. This video is private", "unavailable"),
        ("ERROR: Unsupported URL: https://example.com", "unsupported"),
        ("ERROR: No suitable extractor found for URL x", "unsupported"),
        ("WARNING: foo\nERROR: unable to download: HTTP Error 503", "network"),
        ("ERROR: <urlopen error timed out>", "network"),
        ("something odd", "unknown"),
    ],
)
def test_classify_error(text: str, kind: str) -> None:
    assert classify_error(text)[0] == kind


def test_ttl_cache_lru_and_expiry() -> None:
    now = [0.0]
    c: TTLCache[str, int] = TTLCache(2, ttl_s=600, clock=lambda: now[0])
    c.put("a", 1)
    c.put("b", 2)
    assert c.get("a") == 1  # a is now most recent
    c.put("c", 3)  # evicts b (least recently used)
    assert c.get("b") is None and c.get("a") == 1 and c.get("c") == 3
    now[0] = 601
    assert c.get("a") is None and len(c) == 1


async def test_single_flight_coalesces_and_survives_leader_cancel() -> None:
    sf: SingleFlight[str, int] = SingleFlight()
    calls = 0
    gate = asyncio.Event()

    async def work() -> int:
        nonlocal calls
        calls += 1
        await gate.wait()
        return 42

    leader = asyncio.create_task(sf.do("k", work))
    followers = [asyncio.create_task(sf.do("k", work)) for _ in range(5)]
    await asyncio.sleep(0.01)
    leader.cancel()  # the first caller disconnects...
    gate.set()
    assert await asyncio.gather(*followers) == [42] * 5  # ...the rest still get the result
    assert calls == 1 and sf.coalesced == 5 and len(sf) == 0


def admission(settings: Settings, extract: int = 1, waiters: int = 4) -> AdmissionController:
    s = dataclasses.replace(settings, max_waiters=waiters, queue_wait_s=0.2)
    return AdmissionController(Budget(4096, None, 2, 2000, extract, 4, 1), s)


def fake_runner(delay: float = 0.05, fail: tuple[str, str] | None = None):  # type: ignore[no-untyped-def]
    calls: list[str] = []

    async def run(url: str) -> tuple[str, Any]:
        calls.append(url)
        await asyncio.sleep(delay)
        if fail:
            return "error", fail
        info = to_media_info(YT_INFO, url, 1080)
        return "ok", (info, zlib.compress(json.dumps(YT_INFO).encode()))

    return run, calls


async def test_extractor_single_flight_then_cache(settings: Settings) -> None:
    run, calls = fake_runner()
    ex = Extractor(settings, admission(settings), run)
    url = "https://www.youtube.com/watch?v=aqz-KE-bpKQ"
    results = await asyncio.gather(*(ex.probe(url) for _ in range(10)))
    assert len(calls) == 1 and all(r is results[0] for r in results)
    await ex.probe(url)
    assert len(calls) == 1 and ex.cache.hits == 1
    assert json.loads(results[0].info_json())["id"] == "aqz-KE-bpKQ"
    assert ex.admission.active_count() == 0


async def test_extractor_errors_are_typed_and_not_cached(settings: Settings) -> None:
    run, calls = fake_runner(fail=("blocked", "Sign in to confirm you're not a bot"))
    ex = Extractor(settings, admission(settings), run)
    for _ in range(2):
        with pytest.raises(ExtractError) as e:
            await ex.probe("https://x.test/v")
        assert e.value.kind == "blocked"
    assert len(calls) == 2 and ex.admission.active_count() == 0


async def test_extractor_uses_extract_slots(settings: Settings) -> None:
    run, calls = fake_runner(delay=0.5)
    ex = Extractor(settings, admission(settings, extract=1, waiters=0), run)
    first = asyncio.create_task(ex.probe("https://x.test/1"))
    await asyncio.sleep(0.01)
    with pytest.raises(Overloaded):  # 1 worker busy, no queue
        await ex.probe("https://x.test/2")
    await first


async def test_extractor_timeout_keeps_slot_until_worker_finishes(settings: Settings) -> None:
    run, _ = fake_runner(delay=0.3)
    s = dataclasses.replace(settings, extract_timeout_s=0.05)
    ex = Extractor(s, admission(s), run)
    with pytest.raises(ExtractError) as e:
        await ex.probe("https://x.test/slow")
    assert e.value.kind == "network"
    assert ex.admission.classes["extract"].snapshot()["active"] == 1  # worker still busy
    await asyncio.sleep(0.4)
    assert ex.admission.active_count() == 0
