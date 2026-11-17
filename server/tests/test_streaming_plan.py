"""build_plan(): which processes a grant turns into (pure, runs anywhere)."""

from __future__ import annotations

import dataclasses

import pytest

from viddown_server.config import Settings
from viddown_server.security import StreamGrant
from viddown_server.streaming import (
    FMP4_FLAGS,
    PlanError,
    build_plan,
    content_disposition,
    safe_filename,
)

INFO = {
    "title": 'Big Buck Bunny: 60fps / "4K"',
    "durationSec": 635,
    "formats": [
        {"format_id": "140", "vcodec": "none", "acodec": "mp4a.40.2"},
        {"format_id": "137", "vcodec": "avc1", "acodec": "none", "height": 1080},
        {"format_id": "401", "vcodec": "av01", "acodec": "none", "height": 2160},
        {"format_id": "18", "vcodec": "avc1", "acodec": "mp4a.40.2", "height": 360},
        {"format_id": "43", "vcodec": "vp8", "acodec": "vorbis", "height": 360},
    ],
}
URL = "https://www.youtube.com/watch?v=aqz-KE-bpKQ"


def grant(
    kind: str = "video", fid: str = "137", clip: tuple[int, int] | None = None, precise: bool = False
) -> StreamGrant:
    a, b = clip if clip else (None, None)
    return StreamGrant(URL, kind, fid, a, b, precise, 0)


def test_video_only_format_uses_two_fifos_and_stream_copy(settings: Settings) -> None:
    p = build_plan(grant(), INFO, settings)
    assert p.job_class == "stream" and p.container == "mp4" and p.media_type == "video/mp4"
    assert p.inputs == [["-f", "137"], ["-f", "bestaudio[ext=m4a]/bestaudio[acodec^=mp4a]/bestaudio"]]
    assert p.ffmpeg_out[:6] == ["-map", "0:v:0", "-map", "1:a:0?", "-c", "copy"]
    assert ["-movflags", FMP4_FLAGS, "-f", "mp4"] == p.ffmpeg_out[-4:]


def test_combined_format_uses_one_fifo(settings: Settings) -> None:
    p = build_plan(grant(fid="18"), INFO, settings)
    assert p.inputs == [["-f", "18"]]
    assert "-c:a" in p.ffmpeg_out and p.ffmpeg_out[p.ffmpeg_out.index("-c:a") + 1] == "copy"
    # Vorbis can't go into MP4: re-encode just the audio.
    p2 = build_plan(grant(fid="43"), INFO, settings)
    assert p2.ffmpeg_out[p2.ffmpeg_out.index("-c:a") + 1] == "aac"


def test_audio_is_mp3(settings: Settings) -> None:
    p = build_plan(grant(kind="audio", fid="audio"), INFO, settings)
    assert p.container == "mp3" and p.media_type == "audio/mpeg" and p.filename.endswith(".mp3")
    assert p.inputs == [["-f", "bestaudio/best"]]
    assert "libmp3lame" in p.ffmpeg_out and p.job_class == "stream"


def test_fast_clip_is_stream_class_with_sections(settings: Settings) -> None:
    p = build_plan(grant(clip=(323, 404)), INFO, settings)
    assert p.job_class == "stream" and len(p.inputs) == 1
    args = p.inputs[0]
    assert args[args.index("--download-sections") + 1] == "*323-404"
    assert "--force-keyframes-at-cuts" not in args
    assert args[args.index("--downloader-args") + 1] == "ffmpeg_o:-f matroska"
    assert args[args.index("-f") + 1].startswith("137+bestaudio")


def test_precise_clip_is_transcode_class(settings: Settings) -> None:
    p = build_plan(grant(clip=(323, 404), precise=True), INFO, settings)
    assert p.job_class == "transcode"
    assert "--force-keyframes-at-cuts" in p.inputs[0]
    assert "libx264" in p.inputs[0][p.inputs[0].index("--downloader-args") + 1]


def test_rejects_unknown_or_too_tall_formats(settings: Settings) -> None:
    with pytest.raises(PlanError):
        build_plan(grant(fid="999"), INFO, settings)
    with pytest.raises(PlanError):
        build_plan(grant(fid="140"), INFO, settings)  # audio-only id as a video
    with pytest.raises(PlanError):
        build_plan(grant(fid="401"), INFO, settings)  # 2160p > max_height 1080
    assert build_plan(grant(fid="401"), INFO, dataclasses.replace(settings, max_height=2160))


def test_filenames() -> None:
    assert safe_filename('Big Buck Bunny: 60fps / "4K"', "mp4") == "Big Buck Bunny 60fps 4K.mp4"
    assert safe_filename("...", "mp3") == "video.mp3"
    cd = content_disposition("Café ☕.mp4")
    assert cd.startswith('attachment; filename="Caf .mp4"')
    assert "filename*=UTF-8''Caf%C3%A9%20%E2%98%95.mp4" in cd


def test_pipeline_response_streams_chunked_without_content_length(settings: Settings) -> None:
    """Regression: Response() with no body sent "content-length: 0", so the first
    real chunk crashed uvicorn ("Response content longer than Content-Length")."""
    from types import SimpleNamespace

    from viddown_server.streaming import PipelineResponse

    plan = SimpleNamespace(media_type="video/mp4", filename="Big Buck Bunny [480p].mp4")
    resp = PipelineResponse(
        SimpleNamespace(plan=plan),  # type: ignore[arg-type]
        b"first",
        settings,
        on_bytes=lambda n: None,
        on_close=lambda outcome: None,  # type: ignore[arg-type,return-value]
    )
    names = [k.lower() for k, _ in resp.raw_headers]
    assert b"content-length" not in names
    assert b"content-type" in names
    assert b"content-disposition" in names
