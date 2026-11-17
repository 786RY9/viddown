"""Pure, dependency-free viddown logic shared by the CLI and the server.

Every function here has a TypeScript twin in packages/core; both are checked
against the vectors in /fixtures so client and server behave identically.
"""

from .timeparse import parse_time, to_seconds
from .formats import filter_video_formats
from .ytdlp_args import RESUME_FLAGS, base_args, video_args, audio_args

__all__ = [
    "parse_time",
    "to_seconds",
    "filter_video_formats",
    "RESUME_FLAGS",
    "base_args",
    "video_args",
    "audio_args",
]
