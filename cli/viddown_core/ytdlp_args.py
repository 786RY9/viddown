"""yt-dlp argument builders."""

from typing import Optional

# Resume / retry behaviour shared by every download.
RESUME_FLAGS = [
    "--continue",                      # resume partial downloads
    "--concurrent-fragments", "8",     # parallel fragment downloads (fast 2K/4K)
    "--fragment-retries", "infinite",  # retry failed fragments until success
    "--retries", "10",                 # retry the whole download up to 10 times
    "--retry-sleep", "5",              # wait 5 s between retries
]

Clip = Optional[tuple[str, str]]


def base_args(url: str, output_template: str, clip: Clip = None) -> list[str]:
    args = RESUME_FLAGS + [
        "-o", output_template,
        "--no-playlist",
        "--no-quiet",   # ensure progress lines are emitted even in pipe mode
        "--progress",
        "--newline",    # one progress line per \n (no \r overwriting)
    ]
    if clip:
        start, end = clip
        args += ["--download-sections", f"*{start}-{end}", "--force-keyframes-at-cuts"]
    args.append(url)
    return args


def video_args(format_id: str) -> list[str]:
    """Chosen video stream + best audio, merged into MP4."""
    return ["-f", f"{format_id}+bestaudio/best", "--merge-output-format", "mp4"]


def audio_args() -> list[str]:
    """Best audio converted to top-quality MP3."""
    return ["-f", "bestaudio/best", "-x", "--audio-format", "mp3", "--audio-quality", "0"]
