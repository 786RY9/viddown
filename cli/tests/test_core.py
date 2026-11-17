"""Run the shared /fixtures vectors against viddown_core."""

import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "cli"))

from viddown_core import (  # noqa: E402
    audio_args,
    base_args,
    filter_video_formats,
    parse_time,
    to_seconds,
    video_args,
)

FIXTURES = ROOT / "fixtures"
TIME_CASES = json.loads((FIXTURES / "parse_time.json").read_text(encoding="utf-8"))["cases"]
FORMAT_CASES = json.loads((FIXTURES / "filter_formats.json").read_text(encoding="utf-8"))["cases"]


@pytest.mark.parametrize("case", TIME_CASES, ids=lambda c: repr(c["input"]))
def test_parse_time(case):
    assert parse_time(case["input"]) == case["expected"]


@pytest.mark.parametrize("case", FORMAT_CASES, ids=lambda c: c["name"])
def test_filter_video_formats(case):
    got = [f["format_id"] for f in filter_video_formats(case["formats"])]
    assert got == case["expected"]


def test_to_seconds():
    assert to_seconds("01:05:30") == 3930


def test_base_args_clip_and_url_last():
    args = base_args("https://x/y", "out.%(ext)s", ("00:00:05", "00:00:15"))
    assert args[-1] == "https://x/y"
    assert args[args.index("--download-sections") + 1] == "*00:00:05-00:00:15"
    assert "--force-keyframes-at-cuts" in args
    assert "--download-sections" not in base_args("u", "o")


def test_format_arg_builders():
    assert video_args("137") == ["-f", "137+bestaudio/best", "--merge-output-format", "mp4"]
    assert audio_args()[:3] == ["-f", "bestaudio/best", "-x"]
