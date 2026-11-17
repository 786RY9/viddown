"""HTTP surface with a fake extractor and a fake machine (runs on any OS)."""

from __future__ import annotations

import dataclasses
import json
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from conftest import GB, FakeProbe, make_settings
from fastapi.testclient import TestClient
from test_extract import YT_INFO, fake_runner

from viddown_server.app import create_app
from viddown_server.config import Settings

URL = "https://www.youtube.com/watch?v=aqz-KE-bpKQ"


async def public_resolver(host: str, port: int) -> list[str]:
    return {"www.youtube.com": ["142.250.74.46"], "rebind.test": ["10.0.0.1"]}.get(host, ["93.184.216.34"])


def client_for(settings: Settings, ip: str = "203.0.113.10", **probe_kw: Any) -> TestClient:
    run, _ = fake_runner(delay=0)
    app = create_app(settings, system_probe=FakeProbe(**probe_kw), runner=run, resolver=public_resolver)
    return TestClient(app, client=(ip, 50000))


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    with client_for(settings) as c:
        yield c


def test_manifest_defaults(client: TestClient) -> None:
    r = client.get("/v1/manifest")
    assert r.status_code == 200
    body = r.json()
    assert body["schema"] == 1
    assert body["cloud"]["enabled"] is True and body["cloud"]["reason"] is None
    assert "youtube" in body["cloud"]["extractors"]
    assert r.headers["cache-control"].startswith("public")


def test_manifest_merges_base_and_validated(tmp_path: Path) -> None:
    base = tmp_path / "manifest.json"
    base.write_text(json.dumps({"apps": {"desktop": {"latest": "1.2.0"}}, "notices": [{"id": "n1", "text": "hi"}]}))
    s = make_settings(tmp_path, manifest_base_path=base)
    s.state_dir.mkdir(parents=True)
    (s.state_dir / "validated.json").write_text(
        json.dumps(
            {"ytdlp": "2026.10.26", "ffmpeg": "N-12345", "server": "0.1.0", "validatedAt": "2026-11-17T04:05:00Z"}
        )
    )
    with client_for(s) as c:
        body = c.get("/v1/manifest").json()
    assert body["ytdlp"] == {"version": "2026.10.26", "validatedAt": "2026-11-17T04:05:00Z"}
    assert body["ffmpeg"]["version"] == "N-12345"
    assert body["apps"]["desktop"]["latest"] == "1.2.0" and body["notices"][0]["id"] == "n1"


def test_egress_cutoff_disables_cloud(tmp_path: Path) -> None:
    s = make_settings(tmp_path, egress_cutoff_bytes=1000)
    with client_for(s) as c:
        assert c.get("/v1/manifest").json()["cloud"]["enabled"] is True
        c.app.state.services.egress.add(1000)  # type: ignore[attr-defined]
        m = c.get("/v1/manifest").json()
        assert m["cloud"] == {**m["cloud"], "enabled": False, "reason": "egress_budget"}
        r = c.post("/v1/probe", json={"url": URL})
        assert r.status_code == 503 and r.json()["error"]["kind"] == "cloud_disabled"
        assert int(r.headers["retry-after"]) > 0


def test_kill_switch_disables_cloud(tmp_path: Path) -> None:
    base = tmp_path / "m.json"
    base.write_text(json.dumps({"killSwitches": {"cloud": True}}))
    with client_for(make_settings(tmp_path, manifest_base_path=base)) as c:
        assert c.get("/v1/manifest").json()["cloud"]["reason"] == "kill_switch"


def test_probe_returns_media_info_and_tokens(client: TestClient) -> None:
    r = client.post("/v1/probe", json={"url": URL})
    assert r.status_code == 200, r.text
    b = r.json()
    for key in ("url", "id", "title", "extractor", "durationSec", "thumbnail", "formats", "videoFormats"):
        assert key in b
    assert b["title"] == "Big Buck Bunny"
    assert set(b["streamTokens"]) == {"137", "136", "audio"}
    assert b["stream"]["formatId"] == "137" and b["stream"]["kind"] == "video"
    assert b["stream"]["path"] == f"/v1/stream/{b['stream']['token']}"
    assert "SECRET" not in r.text  # no media URLs


def test_probe_audio_and_specific_format(client: TestClient) -> None:
    b = client.post("/v1/probe", json={"url": URL, "kind": "audio"}).json()
    assert b["stream"]["kind"] == "audio" and b["stream"]["container"] == "mp3"
    b = client.post("/v1/probe", json={"url": URL, "formatId": "136"}).json()
    assert b["stream"]["formatId"] == "136"
    r = client.post("/v1/probe", json={"url": URL, "formatId": "401"})  # 2160p is over the cap
    assert r.status_code == 422


def test_probe_clip_validation(client: TestClient) -> None:
    ok = client.post("/v1/probe", json={"url": URL, "clip": {"start": "5:23", "end": "6m44s"}})
    assert ok.status_code == 200
    for clip in ({"start": "1:00", "end": "0:30"}, {"start": "abc", "end": "1:00"}, {"start": "20:00", "end": "21:00"}):
        r = client.post("/v1/probe", json={"url": URL, "clip": clip})
        assert r.status_code == 400 and r.json()["error"]["kind"] == "invalid"


def test_probe_ssrf_rejected(client: TestClient) -> None:
    for url in ("http://127.0.0.1:8000/healthz", "http://rebind.test/x", "file:///etc/passwd", "ftp://x.test/a"):
        r = client.post("/v1/probe", json={"url": url})
        assert r.status_code == 400, url
        assert r.json()["error"]["kind"] == "unsupported"


def test_probe_rate_limited_per_ip(tmp_path: Path) -> None:
    s = make_settings(tmp_path, probe_rate_per_min=3, probe_burst=3)
    with client_for(s) as c:
        codes = [c.post("/v1/probe", json={"url": URL}).status_code for _ in range(4)]
        assert codes == [200, 200, 200, 429]
        r = c.post("/v1/probe", json={"url": URL})
        assert r.json()["error"]["kind"] == "rate_limited" and int(r.headers["retry-after"]) >= 1
    with client_for(s, ip="198.51.100.1") as other:  # a different client is unaffected
        assert other.post("/v1/probe", json={"url": URL}).status_code == 200


def test_probe_invalid_body(client: TestClient) -> None:
    r = client.post("/v1/probe", json={"url": "x"})
    assert r.status_code == 400 and r.json()["error"]["kind"] == "invalid"


def test_stream_token_checks(client: TestClient, settings: Settings) -> None:
    token = client.post("/v1/probe", json={"url": URL}).json()["stream"]["token"]
    assert client.get("/v1/stream/garbage").status_code == 403
    # The token is bound to the client IP that probed.
    with client_for(settings, ip="198.51.100.77") as other:
        r = other.get(f"/v1/stream/{token}")
        assert r.status_code == 403 and "wrong_client" in r.json()["error"]["message"]


def test_stream_per_ip_concurrency_cap(client: TestClient) -> None:
    svc = client.app.state.services  # type: ignore[attr-defined]
    token = client.post("/v1/probe", json={"url": URL}).json()["stream"]["token"]
    ip = "203.0.113.10"
    assert svc.streams_per_ip.try_acquire(ip) and svc.streams_per_ip.try_acquire(ip)
    r = client.get(f"/v1/stream/{token}")
    assert r.status_code == 429 and r.json()["error"]["reason"] == "streams_per_client"
    svc.streams_per_ip.release(ip), svc.streams_per_ip.release(ip)


def test_stream_returns_503_when_saturated(tmp_path: Path) -> None:
    s = make_settings(tmp_path, stream_slots=1, max_waiters=0)
    with client_for(s) as c:
        svc = c.app.state.services  # type: ignore[attr-defined]
        token = c.post("/v1/probe", json={"url": URL}).json()["stream"]["token"]
        svc.admission.classes["stream"].active.append(object())  # occupy the only slot
        r = c.get(f"/v1/stream/{token}")
        assert r.status_code == 503 and r.headers["retry-after"] == "30"
        assert r.json()["error"]["kind"] == "busy"
        svc.admission.classes["stream"].active.clear()
        assert svc.streams_per_ip.count("203.0.113.10") == 0  # released on rejection


def test_contact_stores_message_and_honeypot(client: TestClient, settings: Settings) -> None:
    ok = client.post("/v1/contact", json={"name": "Ada", "email": "ada@example.com", "message": "Hire you?"})
    assert ok.status_code == 202
    bot = client.post("/v1/contact", json={"name": "b", "email": "b@b.co", "message": "spam", "website": "x"})
    assert bot.status_code == 202
    assert client.post("/v1/contact", json={"name": "x", "email": "nope", "message": "m"}).status_code == 400
    rows = (
        sqlite3.connect(settings.state_dir / "viddown.db")
        .execute("SELECT name, email, message FROM contact")
        .fetchall()
    )
    assert rows == [("Ada", "ada@example.com", "Hire you?")]


def test_ping_counts_without_storing_ips(client: TestClient, settings: Settings) -> None:
    for _ in range(3):
        assert (
            client.post("/v1/ping", json={"event": "install", "platform": "android", "appVersion": "1.0.0"}).status_code
            == 204
        )
    client.post("/v1/ping", json={"event": "launch", "platform": "windows", "appVersion": "evil'; DROP"})
    assert client.post("/v1/ping", json={"event": "hack", "platform": "android"}).status_code == 400
    db = sqlite3.connect(settings.state_dir / "viddown.db")
    rows = db.execute("SELECT event, platform, app_version, n FROM counters ORDER BY event").fetchall()
    assert [r[:4] for r in rows] == [("install", "android", "1.0.0", 3), ("launch", "windows", "other", 1)]
    dump = "\n".join(db.iterdump())
    assert "203.0.113.10" not in dump
    stats = client.get("/v1/stats").json()
    assert stats["totals"]["install"] == 3 and stats["installsByPlatform"] == {"android": 3}


def test_healthz_detail_only_for_localhost(settings: Settings) -> None:
    with client_for(settings) as c:
        assert set(c.get("/healthz").json()) == {"status", "version"}
    with client_for(settings, ip="127.0.0.1", total_mb=24 * GB, cpus=4) as c:
        body = c.get("/healthz").json()
        assert body["budget"]["stream_slots"] == 40 and body["budget"]["extract_workers"] == 4
        assert body["admission"]["classes"]["transcode"]["capacity"] == 3
        c.app.state.services.admission.start_drain()  # type: ignore[attr-defined]
        r = c.get("/healthz")
        assert r.status_code == 503 and r.json()["status"] == "draining"


def test_drain_rejects_probe_with_503(settings: Settings) -> None:
    with client_for(dataclasses.replace(settings)) as c:
        c.app.state.services.admission.start_drain()  # type: ignore[attr-defined]
        r = c.post("/v1/probe", json={"url": "https://www.youtube.com/watch?v=other"})
        assert r.status_code == 503 and "draining" in r.json()["error"]["reason"]


def test_media_info_matches_ts_type() -> None:
    """Keys in the probe response must exist on the TS MediaInfo interface."""
    ts = Path(__file__).resolve().parents[2] / "packages" / "core" / "src" / "types.ts"
    if not ts.exists():
        pytest.skip("packages/core not present (deploy bundle)")
    text = ts.read_text(encoding="utf-8")
    block = text[text.index("export interface MediaInfo") :]
    block = block[: block.index("}")]
    for key in ("url", "id", "title", "uploader", "extractor", "durationSec", "thumbnail", "formats", "videoFormats"):
        assert f"  {key}" in block, key
    assert YT_INFO["id"]


def test_cors_preflight_allows_site_and_desktop_origins(client: TestClient) -> None:
    """Regression: the contact form preflight from the site / Tauri webview got 405."""
    for origin in ("https://786ry9.github.io", "tauri://localhost", "http://tauri.localhost"):
        r = client.options(
            "/v1/contact",
            headers={
                "Origin": origin,
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type",
            },
        )
        assert r.status_code == 200, origin
        assert r.headers["access-control-allow-origin"] == origin
        assert "access-control-allow-credentials" not in r.headers


def test_cors_rejects_unknown_origin(client: TestClient) -> None:
    r = client.options(
        "/v1/contact",
        headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "POST"},
    )
    assert "access-control-allow-origin" not in r.headers
    # Simple GETs still work, but the browser won't expose them to that origin.
    g = client.get("/v1/manifest", headers={"Origin": "https://evil.example"})
    assert g.status_code == 200 and "access-control-allow-origin" not in g.headers
