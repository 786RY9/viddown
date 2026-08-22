#!/usr/bin/env python3
"""
viddown — CLI video downloader for YouTube, Instagram, TikTok (and more).
Powered by yt-dlp + ffmpeg + rich.

Created by RY9
Contact: rashidyaseen5484@gmail.com
"""

import sys
import os
import re
import json
import curses
import subprocess
import threading
from typing import Optional, Union

from rich.console import Console
from rich.markup import escape as markup_escape
from rich.progress import (
    Progress,
    SpinnerColumn,
    BarColumn,
    TextColumn,
    TimeRemainingColumn,
    TaskID,
)

# ─── Global console (shared across threads) ───────────────────────────────────
console = Console(highlight=False)

# ─── Constants ────────────────────────────────────────────────────────────────
CONTACT_EMAIL = "rashidyaseen5484@gmail.com"
BRAND         = "RY9"

# Sentinel returned by pick_video_quality when user picks "Back"
_BACK = object()

# ─── ANSI colours (used in banner / curses sections) ─────────────────────────
RESET   = "\033[0m"
BOLD    = "\033[1m"
CYAN    = "\033[96m"
GREEN   = "\033[92m"
YELLOW  = "\033[93m"
RED     = "\033[91m"
DIM     = "\033[2m"
MAGENTA = "\033[95m"


# ─── Banner ───────────────────────────────────────────────────────────────────
def banner() -> None:
    width = 70
    rule  = "─" * width
    print(f"""\
{CYAN}{BOLD}
 ██╗   ██╗██╗██████╗ ██████╗  ██████╗ ██╗    ██╗███╗   ██╗
 ██║   ██║██║██╔══██╗██╔══██╗██╔═══██╗██║    ██║████╗  ██║
 ██║   ██║██║██║  ██║██║  ██║██║   ██║██║ █╗ ██║██╔██╗ ██║
 ╚██╗ ██╔╝██║██║  ██║██║  ██║██║   ██║██║███╗██║██║╚██╗██║
  ╚████╔╝ ██║██████╔╝██████╔╝╚██████╔╝╚███╔███╔╝██║ ╚████║
   ╚═══╝  ╚═╝╚═════╝ ╚═════╝  ╚═════╝  ╚══╝╚══╝ ╚═╝  ╚═══╝
{RESET}
  {DIM}YouTube · Instagram · TikTok · and more{RESET}{CYAN}{BOLD}{"RY9":>31}{RESET}
  {DIM}{rule}{RESET}
  {DIM}Built to be useful. If you ever want to reach out → {RESET}{YELLOW}{CONTACT_EMAIL}{RESET}
  {DIM}{rule}{RESET}
""")


# ─── Curses menu ──────────────────────────────────────────────────────────────
def curses_menu(stdscr, title: str, options: list[str], subtitle: str = "") -> int:
    """Arrow-key menu. Returns selected index (0-based) or -1 on Q/Escape."""
    try:
        curses.curs_set(0)
    except Exception:
        pass
    try:
        curses.start_color()
        curses.use_default_colors()
        curses.init_pair(1, curses.COLOR_CYAN,   -1)  # selected item
        curses.init_pair(2, curses.COLOR_YELLOW, -1)  # title
        curses.init_pair(3, curses.COLOR_WHITE,  -1)  # normal item
        curses.init_pair(4, curses.COLOR_BLACK,  -1)  # dim / hint
    except Exception:
        pass

    current = 0
    while True:
        stdscr.erase()
        height, width = stdscr.getmaxyx()

        # Title
        pad = max((width - len(title)) // 2, 0)
        stdscr.attron(curses.color_pair(2) | curses.A_BOLD)
        stdscr.addstr(1, pad, title[:width - 1])
        stdscr.attroff(curses.color_pair(2) | curses.A_BOLD)

        # Subtitle
        if subtitle:
            sub_pad = max((width - len(subtitle)) // 2, 0)
            stdscr.attron(curses.color_pair(4))
            stdscr.addstr(2, sub_pad, subtitle[:width - 1])
            stdscr.attroff(curses.color_pair(4))

        # Menu items
        for idx, opt in enumerate(options):
            row = 4 + idx
            if row >= height - 2:
                break
            prefix = "  ❯  " if idx == current else "     "
            label  = f"{prefix}{opt}"
            if idx == current:
                stdscr.attron(curses.color_pair(1) | curses.A_BOLD)
                stdscr.addstr(row, 2, label[:width - 3])
                stdscr.attroff(curses.color_pair(1) | curses.A_BOLD)
            else:
                stdscr.attron(curses.color_pair(3))
                stdscr.addstr(row, 2, label[:width - 3])
                stdscr.attroff(curses.color_pair(3))

        # Footer hint
        hint = "↑ ↓  Navigate    Enter  Select    Q  Quit"
        hint_pad = max((width - len(hint)) // 2, 0)
        stdscr.attron(curses.color_pair(4))
        stdscr.addstr(height - 2, hint_pad, hint[:width - 1])
        stdscr.attroff(curses.color_pair(4))

        stdscr.refresh()
        key = stdscr.getch()

        if key in (curses.KEY_UP, ord("k")):
            current = (current - 1) % len(options)
        elif key in (curses.KEY_DOWN, ord("j")):
            current = (current + 1) % len(options)
        elif key in (curses.KEY_ENTER, 10, 13):
            return current
        elif key in (ord("q"), ord("Q"), 27):
            return -1


def run_curses_menu(title: str, options: list[str], subtitle: str = "") -> int:
    result = curses.wrapper(curses_menu, title, options, subtitle)
    # curses can leave the terminal in a broken state where backspace prints ^H
    # instead of deleting. `stty sane` resets all line-discipline settings.
    try:
        subprocess.run(["stty", "sane"], check=False, capture_output=True)
    except Exception:
        pass
    return result



# ─── Time helpers ─────────────────────────────────────────────────────────────
def parse_time(raw: str) -> Optional[str]:
    """
    Accept flexible time input → HH:MM:SS string, or None on failure.
    Accepts: 5:23 · 1:05:30 · 5m23s · 1h5m30s · 323 (seconds) · 30s
    """
    raw = raw.strip().lower()

    # Pure integer → treat as total seconds
    if re.fullmatch(r"\d+", raw):
        total = int(raw)
        h, rem = divmod(total, 3600)
        mins, secs = divmod(rem, 60)       # renamed: no collision with re match below
        return f"{h:02d}:{mins:02d}:{secs:02d}"

    # MM:SS or HH:MM:SS
    match_obj = re.fullmatch(r"(\d+):(\d{2})(?::(\d{2}))?", raw)
    if match_obj:
        if match_obj.group(3) is not None:
            h    = int(match_obj.group(1))
            mins = int(match_obj.group(2))
            secs = int(match_obj.group(3))
        else:
            h    = 0
            mins = int(match_obj.group(1))
            secs = int(match_obj.group(2))
        return f"{h:02d}:{mins:02d}:{secs:02d}"

    # Flexible: 1h5m30s / 5m23s / 30s / 1h
    pat = re.fullmatch(
        r"(?:(\d+)\s*h)?\s*(?:(\d+)\s*m(?:in)?)?\s*(?:(\d+)\s*s(?:ec)?)?", raw
    )
    if pat and any(pat.group(i) for i in (1, 2, 3)):
        h    = int(pat.group(1) or 0)
        mins = int(pat.group(2) or 0)
        secs = int(pat.group(3) or 0)
        return f"{h:02d}:{mins:02d}:{secs:02d}"

    return None


def _to_seconds(t: str) -> int:
    h, m, s = map(int, t.split(":"))
    return h * 3600 + m * 60 + s


def prompt_time(label: str) -> str:
    examples = "e.g. 5:23  or  5m23s  or  323  or  1:05:30"
    while True:
        try:
            raw = input(f"  {BOLD}{label}{RESET} {DIM}({examples}){RESET}: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            sys.exit(0)
        if not raw:
            print(f"  {RED}Please enter a time.{RESET}")
            continue
        parsed = parse_time(raw)
        if parsed:
            return parsed
        print(f"  {RED}Couldn't parse '{raw}'. Try: 5:23, 5m23s, 323{RESET}")


def prompt_clip_times() -> tuple[str, str]:
    print(f"\n  {BOLD}{CYAN}Set clip time range{RESET}")
    print(f"  {DIM}Formats: 5:23  ·  5m23s  ·  1:05:30  ·  323{RESET}\n")
    while True:
        start = prompt_time("Start time:")
        end   = prompt_time("End   time:")
        if _to_seconds(end) <= _to_seconds(start):
            print(f"  {RED}End time must be after start time. Try again.{RESET}\n")
            continue
        return start, end


# Percentage-based progress  → normal HTTP downloads
# e.g.: [download]  45.2% of ~1.23GiB at 5.00MiB/s ETA 00:15
DOWNLOAD_PCT_RE = re.compile(
    r"\[download\]\s+([\d.]+)%"
)

# Fragment-based progress → DASH / HLS streams (YouTube 4K, etc.)
# e.g.: [download] Downloading fragment 12 of 340
DOWNLOAD_FRAG_RE = re.compile(
    r"\[download\] Downloading fragment\s+(\d+)\s+of\s+(\d+|unknown)",
    re.IGNORECASE,
)

# FFmpeg clip progress → e.g. time=00:01:23.45 or elapsed=0:00:12.34
FFMPEG_TIME_RE = re.compile(
    r"time=(\d+):(\d{2}):(\d{2})\.(\d+)"
)
FFMPEG_ELAPSED_RE = re.compile(
    r"elapsed=(\d+):(\d{2}):(\d{2})\.(\d+)"
)


def iter_lines(proc):
    """Yield stdout/stderr lines split by either \\r or \\n from raw file descriptor."""
    fd = proc.stdout.fileno()
    buffer = ""
    while True:
        try:
            chunk_bytes = os.read(fd, 1024)
        except OSError:
            chunk_bytes = b""
        if not chunk_bytes:
            if buffer.strip():
                yield buffer.strip()
            break
        buffer += chunk_bytes.decode("utf-8", errors="replace")
        while "\r" in buffer or "\n" in buffer:
            r_pos = buffer.find("\r")
            n_pos = buffer.find("\n")
            if r_pos != -1 and (n_pos == -1 or r_pos < n_pos):
                line = buffer[:r_pos]
                buffer = buffer[r_pos + 1:]
            else:
                line = buffer[:n_pos]
                buffer = buffer[n_pos + 1:]
            if line.strip():
                yield line.strip()


def _make_progress() -> Progress:
    """Create a Rich Progress bar for viddown."""
    return Progress(
        SpinnerColumn(spinner_name="dots", style="cyan"),
        TextColumn("[bold cyan]{task.description}"),
        BarColumn(bar_width=32, style="cyan", complete_style="green"),
        TextColumn("[progress.percentage]{task.percentage:>5.1f}%"),
        TimeRemainingColumn(),
        console=console,
        transient=False,
        expand=False,
    )


def _worker(
    yt_args: list[str],
    label: str,
    progress: Progress,
    task_id: TaskID,
    clip: Optional[tuple[str, str]] = None,
    video_duration: Optional[float] = None,
) -> int:
    """
    Run a single yt-dlp process, updating the given Rich progress task.
    Handles:
      - Percentage-based progress (HTTP, single-file streams)
      - Fragment-based progress   (DASH / HLS streams — YouTube 4K, etc.)
      - FFmpeg time progress      (Clip & m3u8 downloads)
    Returns the yt-dlp exit code.
    """
    proc = subprocess.Popen(
        [YTDLP_BIN] + yt_args,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=False,
        bufsize=0,
        env={**os.environ, "PYTHONUNBUFFERED": "1"},  # ensure unbuffered output
    )

    phase      = 0      # 0 = video, 1 = audio, 2 = post-processing
    last_pct   = 0.0
    total_frag = 0      # total fragments (DASH/HLS — learned from first match)

    clip_duration = None
    if clip:
        try:
            clip_duration = max(1, _to_seconds(clip[1]) - _to_seconds(clip[0]))
        except Exception:
            clip_duration = None

    # Immediately show the bar is alive
    progress.update(task_id, description=f"[cyan]{markup_escape(label[:40])}[/cyan] [dim]starting…[/dim]")

    for line in iter_lines(proc):
        # ── FFmpeg / m3u8 time progress ───────────────────────────────────────
        time_match = FFMPEG_TIME_RE.search(line)
        if time_match:
            h = int(time_match.group(1))
            m = int(time_match.group(2))
            s = int(time_match.group(3))
            curr_sec = h * 3600 + m * 60 + s
            target_dur = clip_duration or video_duration
            if target_dur and target_dur > 0:
                pct = min(99.0, max(0.0, (curr_sec / target_dur) * 100.0))
                last_pct = pct
                safe_label = markup_escape(label[:28])
                progress.update(
                    task_id,
                    completed=pct,
                    description=f"[cyan]{safe_label}[/cyan] [dim]{h:02d}:{m:02d}:{s:02d}[/dim]",
                )
            continue

        if "time=N/A" in line:
            el_match = FFMPEG_ELAPSED_RE.search(line)
            if el_match:
                em = el_match.group(2)
                es = el_match.group(3)
                safe_label = markup_escape(label[:28])
                progress.update(
                    task_id,
                    description=f"[cyan]{safe_label}[/cyan] [dim]seeking… {em}:{es}[/dim]",
                )
            continue

        # ── Fragment-based (DASH / HLS — YouTube 4K, Instagram, etc.) ─────────
        frag_match = DOWNLOAD_FRAG_RE.search(line)
        if frag_match:
            current_frag = int(frag_match.group(1))
            total_frag_str = frag_match.group(2)
            if total_frag_str.isdigit():
                total_frag = int(total_frag_str)
                if total_frag > 0:
                    pct = (current_frag / total_frag) * 100.0
                    # Detect audio phase reset
                    if pct < last_pct - 15:
                        phase += 1
                        safe = markup_escape(label[:30])
                        desc = (f"{safe} [dim](audio)[/dim]"
                                if phase == 1 else markup_escape(label[:40]))
                        progress.update(task_id, description=f"[cyan]{desc}[/cyan]", completed=0)
                    last_pct = pct
                    progress.update(
                        task_id,
                        completed=pct,
                        description=(
                            f"[cyan]{markup_escape(label[:28])}[/cyan] "
                            f"[dim]frag {current_frag}/{total_frag}[/dim]"
                        ),
                    )
            else:
                safe = markup_escape(label[:28])
                progress.update(
                    task_id,
                    description=f"[cyan]{safe}[/cyan] [dim]frag {current_frag}[/dim]",
                )
            continue

        # ── Percentage-based (normal HTTP streams) ────────────────────────────
        pct_match = DOWNLOAD_PCT_RE.search(line)
        if pct_match:
            pct = float(pct_match.group(1))
            # Detect audio phase reset
            if pct < last_pct - 10 and last_pct > 80:
                phase += 1
                safe = markup_escape(label[:30])
                desc = (f"{safe} [dim](audio)[/dim]"
                        if phase == 1 else markup_escape(label[:40]))
                progress.update(task_id, description=f"[cyan]{desc}[/cyan]", completed=0)
            last_pct = pct
            progress.update(task_id, completed=pct)
            continue

        # ── Post-processing lines ─────────────────────────────────────────────
        if "[Merger]" in line or "[SectionDownloader]" in line:
            safe_label = markup_escape(label[:32])
            progress.update(
                task_id,
                description=f"[magenta]{safe_label} merging…[/magenta]",
                completed=100,
            )
        elif "[ExtractAudio]" in line:
            safe_label = markup_escape(label[:32])
            progress.update(
                task_id,
                description=f"[magenta]{safe_label} converting…[/magenta]",
                completed=100,
            )
        elif line.startswith("ERROR:") or "[error]" in line.lower():
            console.print(f"  [red]{markup_escape(line)}[/red]")

    proc.wait()
    if proc.returncode == 0:
        safe_label = markup_escape(label[:36])
        progress.update(task_id, completed=100,
                        description=f"[green]✓ {safe_label}[/green]")
    else:
        safe_label = markup_escape(label[:36])
        progress.update(task_id,
                        description=f"[red]✗ {safe_label} (failed)[/red]")
    return proc.returncode


# ─── Core download flags (resume / retry) ────────────────────────────────────
RESUME_FLAGS = [
    "--continue",                      # resume partial downloads
    "--concurrent-fragments", "8",     # 8x multi-threaded parallel fragment downloads (fast 2K/4K)
    "--fragment-retries", "infinite",  # retry failed fragments until success
    "--retries", "10",                 # retry full download up to 10 times
    "--retry-sleep", "5",              # wait 5 s between retries
]

# Resolve the yt-dlp binary: prefer the active venv, then fall back to PATH.
def _ytdlp_bin() -> str:
    # Check the venv that ships with this install first
    venv_bin = os.path.join(sys.prefix, "bin", "yt-dlp")
    if os.path.isfile(venv_bin):
        return venv_bin
    # Fall back to whatever is on PATH
    import shutil
    found = shutil.which("yt-dlp")
    if found:
        return found
    # Last resort: let subprocess raise a clear error
    return "yt-dlp"

YTDLP_BIN = _ytdlp_bin()


def _base_args(
    url: str,
    output_template: str,
    clip: Optional[tuple[str, str]] = None,
) -> list[str]:
    args = RESUME_FLAGS + [
        "-o", output_template,
        "--no-playlist",
        "--no-quiet",          # ensure progress lines are emitted even in pipe mode
        "--progress",
        "--newline",           # one progress line per \n (no \r overwriting)
    ]
    if clip:
        start, end = clip
        args += [
            "--download-sections", f"*{start}-{end}",
            "--force-keyframes-at-cuts",
        ]
    args.append(url)
    return args


# ─── yt-dlp helpers ───────────────────────────────────────────────────────────
def run_ytdlp(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run([YTDLP_BIN] + args, capture_output=True, text=True)


def update_ytdlp() -> None:
    print(f"\n{CYAN}  Updating yt-dlp…{RESET}\n")
    # Upgrade inside the same pip that manages the current env
    pip_bin = os.path.join(sys.prefix, "bin", "pip")
    if not os.path.isfile(pip_bin):
        pip_bin = "pip"
    proc = subprocess.Popen(
        [pip_bin, "install", "--upgrade", "yt-dlp"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, bufsize=1,
    )
    for line in proc.stdout:
        line = line.rstrip()
        if "Successfully installed" in line:
            print(f"  {GREEN}{BOLD}{line}{RESET}")
        elif "already up-to-date" in line.lower() or "already satisfied" in line.lower():
            print(f"  {YELLOW}{line}{RESET}")
        else:
            print(f"  {DIM}{line}{RESET}")
    proc.wait()
    ver = run_ytdlp(["--version"])
    if proc.returncode == 0:
        print(f"\n  {GREEN}{BOLD}✓ yt-dlp is now at version {ver.stdout.strip()}{RESET}")
    else:
        print(f"\n  {RED}Update failed (exit {proc.returncode}).{RESET}")


def fetch_formats(url: str) -> tuple[list[dict], str, Optional[float]]:
    print(f"  {DIM}Fetching available formats…{RESET}")
    result = run_ytdlp(["--dump-json", "--no-playlist", url])
    if result.returncode != 0:
        print(f"\n{RED}  Error fetching video info:{RESET}")
        print(f"  {DIM}{result.stderr[:600]}{RESET}")
        sys.exit(1)
    info = json.loads(result.stdout)
    return info.get("formats", []), info.get("title", "Unknown title"), info.get("duration")


def filter_video_formats(formats: list[dict]) -> list[dict]:
    """Deduplicate by resolution (prefer direct HTTP/DASH over m3u8, then highest bitrate), sort best → worst."""
    seen: dict[int, dict] = {}
    for fmt in formats:
        if fmt.get("vcodec", "none") == "none" or not fmt.get("height"):
            continue
        h = fmt["height"]
        protocol = fmt.get("protocol", "")
        is_m3u8 = "m3u8" in protocol

        if h not in seen:
            seen[h] = fmt
        else:
            prev_fmt = seen[h]
            prev_is_m3u8 = "m3u8" in prev_fmt.get("protocol", "")
            # Prefer direct HTTP/DASH over m3u8 (m3u8 is much slower); if same type, compare bitrate
            if prev_is_m3u8 and not is_m3u8:
                seen[h] = fmt
            elif prev_is_m3u8 == is_m3u8:
                if (fmt.get("tbr") or 0) > (prev_fmt.get("tbr") or 0):
                    seen[h] = fmt
    return sorted(seen.values(), key=lambda f: f.get("height", 0), reverse=True)


def pick_video_quality(
    video_fmts: list[dict], title: str
) -> Union[dict, None, object]:
    """
    Show quality picker.
    Returns:
        dict   — chosen video format
        None   — user chose audio-only
        _BACK  — user chose to go back to main menu
    """
    options: list[str] = []
    for fmt in video_fmts:
        height   = fmt.get("height", "?")
        fps      = fmt.get("fps") or "?"
        ext      = fmt.get("ext", "?")
        filesize = fmt.get("filesize") or fmt.get("filesize_approx")
        tbr      = fmt.get("tbr")
        size_str = (f"~{filesize / 1_048_576:.1f} MB" if filesize
                    else f"~{tbr:.0f} kbps"           if tbr
                    else "size unknown")
        options.append(f"{height}p  {fps}fps  [{ext}]  {size_str}")
    options.append("Audio only  (best quality → MP3)")
    options.append("← Back to main menu")

    idx = run_curses_menu(
        title=f"Select quality — {title[:50]}",
        options=options,
        subtitle="Video + best audio will be merged into MP4",
    )

    if idx == -1 or idx == len(options) - 1:
        return _BACK
    if idx == len(options) - 2:
        return None          # audio-only chosen
    return video_fmts[idx]


# ─── Single download ──────────────────────────────────────────────────────────
def download_video(
    url: str,
    fmt: dict,
    output_dir: str,
    clip: Optional[tuple[str, str]] = None,
    video_duration: Optional[float] = None,
) -> None:
    height = fmt.get("height", "?")
    fmt_id = fmt["format_id"]
    label  = (f"{height}p clip ({clip[0]} → {clip[1]})" if clip
              else f"{height}p video")
    tmpl   = ("%(title)s [%(height)sp] [%(section_start)s-%(section_end)s].%(ext)s"
              if clip else "%(title)s [%(height)sp].%(ext)s")

    console.print(f"\n  [cyan]Downloading {markup_escape(label)} + best audio…[/cyan]")
    yt_args = ["-f", f"{fmt_id}+bestaudio/best", "--merge-output-format", "mp4"]
    yt_args += _base_args(url, os.path.join(output_dir, tmpl), clip)
    _run_single(yt_args, label, output_dir, clip, video_duration)


def download_audio(
    url: str,
    output_dir: str,
    clip: Optional[tuple[str, str]] = None,
    video_duration: Optional[float] = None,
) -> None:
    label = (f"audio clip ({clip[0]} → {clip[1]})" if clip
              else "best audio (MP3)")
    tmpl  = ("%(title)s [audio] [%(section_start)s-%(section_end)s].%(ext)s"
              if clip else "%(title)s [audio].%(ext)s")

    console.print(f"\n  [cyan]Downloading {markup_escape(label)}…[/cyan]")
    yt_args = ["-f", "bestaudio/best", "-x",
               "--audio-format", "mp3", "--audio-quality", "0"]
    yt_args += _base_args(url, os.path.join(output_dir, tmpl), clip)
    _run_single(yt_args, label, output_dir, clip, video_duration)


def get_display_path(path: str) -> str:
    """Format path for clean CLI output."""
    if path == "/downloads":
        return "current directory (./)"
    if path.startswith("/downloads/"):
        rel = path[len("/downloads/"):].lstrip("/")
        return f"./{rel}"
    home = os.path.expanduser("~")
    if path.startswith(home):
        return "~" + path[len(home):]
    return path


def prompt_change_output_dir(current_dir: str) -> str:
    """Prompt user for a new download folder location."""
    display_curr = get_display_path(current_dir)
    print(f"\n  {BOLD}{CYAN}Set Download Save Folder{RESET}")
    print(f"  {DIM}Current folder:{RESET} {YELLOW}{display_curr}{RESET}")
    print(f"  {DIM}Enter a subfolder name or full path (e.g. clips or /home/ry/Videos):{RESET}")
    try:
        raw = input("  > ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return current_dir
    if not raw:
        print(f"  {DIM}Keeping current folder: {display_curr}{RESET}\n")
        return current_dir

    if current_dir == "/downloads" and not raw.startswith("/"):
        new_path = os.path.normpath(os.path.join("/downloads", raw))
    else:
        new_path = os.path.expanduser(raw)

    try:
        os.makedirs(new_path, exist_ok=True)
        print(f"  {GREEN}✓ Save folder set to: {get_display_path(new_path)}{RESET}\n")
        return new_path
    except Exception as err:
        print(f"  {RED}Could not create directory: {err}{RESET}\n")
        return current_dir


def _run_single(
    yt_args: list[str],
    label: str,
    output_dir: str,
    clip: Optional[tuple[str, str]] = None,
    video_duration: Optional[float] = None,
) -> None:
    """Run a single download with a Rich progress bar."""
    with _make_progress() as progress:
        task_id = progress.add_task(
            f"[cyan]{markup_escape(label[:40])}[/cyan]", total=100
        )
        rc = _worker(yt_args, label[:40], progress, task_id, clip=clip, video_duration=video_duration)

    if rc == 0:
        console.print(
            f"\n  [bold green]✓ Done![/bold green]  "
            f"[dim]Saved to {markup_escape(get_display_path(output_dir))}[/dim]"
        )
    else:
        console.print(f"\n  [bold red]✗ Download failed (exit {rc})[/bold red]")
        sys.exit(rc)


# ─── Parallel download ────────────────────────────────────────────────────────
def prompt_multiple_urls() -> list[str]:
    """Prompt for multiple URLs; blank line to finish."""
    print(f"\n  {BOLD}{CYAN}Enter video URLs{RESET}")
    print(f"  {DIM}One URL per line. Press Enter on a blank line when done.{RESET}\n")

    urls: list[str] = []
    n = 1
    while True:
        try:
            raw = input(f"  {BOLD}URL #{n}:{RESET} ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not raw:
            if not urls:
                print(f"  {RED}Please enter at least one URL.{RESET}")
                continue
            break
        urls.append(raw)
        n += 1
    return urls


def parallel_download(urls: list[str], output_dir: str, audio_only: bool) -> None:
    """Download all URLs in parallel, each with its own progress bar row."""
    os.makedirs(output_dir, exist_ok=True)

    jobs: list[dict] = []
    for i, url in enumerate(urls, 1):
        # Build a short label — escape any Rich-special characters
        raw_label   = f"#{i} {url[:35]}…" if len(url) > 38 else f"#{i} {url}"
        short_label = raw_label

        if audio_only:
            tmpl    = "%(title)s [audio].%(ext)s"
            yt_args = (
                ["-f", "bestaudio/best", "-x",
                 "--audio-format", "mp3", "--audio-quality", "0"]
                + _base_args(url, os.path.join(output_dir, tmpl))
            )
        else:
            tmpl    = "%(title)s [%(height)sp].%(ext)s"
            yt_args = (
                ["-f", "bestvideo+bestaudio/best", "--merge-output-format", "mp4"]
                + _base_args(url, os.path.join(output_dir, tmpl))
            )

        jobs.append({"label": short_label, "yt_args": yt_args})

    mode = "audio (MP3)" if audio_only else "best quality video"
    console.print(
        f"\n  [bold cyan]Starting {len(jobs)} parallel download(s) — {mode}[/bold cyan]\n"
    )

    results: dict[str, int] = {}

    with _make_progress() as progress:
        task_ids = {
            job["label"]: progress.add_task(
                f"[cyan]{markup_escape(job['label'])}[/cyan]", total=100
            )
            for job in jobs
        }

        def _make_worker(j: dict):
            """Factory avoids late-binding closure bug in loop."""
            def _run():
                rc = _worker(j["yt_args"], j["label"], progress, task_ids[j["label"]])
                results[j["label"]] = rc
            return _run

        threads = [
            threading.Thread(target=_make_worker(job), daemon=True)
            for job in jobs
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

    console.print()
    ok  = sum(1 for rc in results.values() if rc == 0)
    err = len(results) - ok
    for lbl, rc in results.items():
        icon = "[green]✓[/green]" if rc == 0 else "[red]✗[/red]"
        console.print(f"  {icon}  {markup_escape(lbl)}")
    console.print(
        f"\n  [bold green]{ok} succeeded[/bold green]"
        + (f"  [bold red]{err} failed[/bold red]" if err else "")
    )


# ─── Main flow ────────────────────────────────────────────────────────────────
MAIN_MENU_OPTIONS = [
    "📥  Download full video        (pick quality)",
    "✂️   Download a clip            (set time range)",
    "🎵  Download audio only        (full · best MP3)",
    "🎙️   Download audio clip        (set time range · MP3)",
    "📦  Download multiple videos   (parallel · auto best quality)",
    "🎶  Download multiple as audio (parallel · MP3)",
    "📁  Change save folder         (specify download location)",
    "🔄  Update yt-dlp to latest version",
    "❌  Quit",
]


def prompt_url() -> str:
    print(f"  {BOLD}Paste a YouTube / Instagram / TikTok URL:{RESET}")
    try:
        url = input("  > ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        sys.exit(0)
    if not url:
        print(f"  {RED}No URL provided.{RESET}")
        sys.exit(1)
    return url


def run_download_flow(
    url: str,
    output_dir: str,
    audio_only: bool,
    clip: Optional[tuple[str, str]] = None,
) -> None:
    os.makedirs(output_dir, exist_ok=True)

    formats, title, video_duration = fetch_formats(url)
    print(f"  {BOLD}Title:{RESET} {CYAN}{title}{RESET}\n")

    if audio_only:
        download_audio(url, output_dir, clip, video_duration)
        return

    video_fmts = filter_video_formats(formats)
    if not video_fmts:
        print(f"{YELLOW}  No video formats found — falling back to audio-only.{RESET}")
        download_audio(url, output_dir, clip, video_duration)
        return

    chosen = pick_video_quality(video_fmts, title)
    if chosen is _BACK:
        return
    if chosen is None:
        download_audio(url, output_dir, clip, video_duration)
    else:
        download_video(url, chosen, output_dir, clip, video_duration)


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description="viddown — download videos from YouTube, Instagram, TikTok, and more."
    )
    parser.add_argument("url", nargs="?",
                        help="Video URL (optional — prompted if omitted)")
    parser.add_argument("-o", "--output", default="/downloads",
                        help="Output directory (default: /downloads)")
    parser.add_argument("--audio-only", action="store_true",
                        help="Non-interactive: download audio only")
    parser.add_argument("--update", action="store_true",
                        help="Update yt-dlp and exit")
    parser.add_argument("--start",
                        help="Clip start time, e.g. 5:23 or 5m23s")
    parser.add_argument("--end",
                        help="Clip end time,   e.g. 6:44 or 6m44s")
    args = parser.parse_args()

    banner()

    # ── Non-interactive: --update ─────────────────────────────────────────────
    if args.update:
        update_ytdlp()
        return

    # ── Parse optional CLI clip times ─────────────────────────────────────────
    cli_clip: Optional[tuple[str, str]] = None
    if args.start or args.end:
        if not (args.start and args.end):
            print(f"{RED}  Both --start and --end are required for a clip.{RESET}")
            sys.exit(1)
        s = parse_time(args.start)
        e = parse_time(args.end)
        if not s or not e:
            print(f"{RED}  Could not parse --start / --end times.{RESET}")
            sys.exit(1)
        if _to_seconds(e) <= _to_seconds(s):
            print(f"{RED}  --end must be after --start.{RESET}")
            sys.exit(1)
        cli_clip = (s, e)

    # ── Non-interactive: URL + flags given on command line ────────────────────
    if args.url:
        os.makedirs(args.output, exist_ok=True)
        console.print(f"  [bold]URL:[/bold] [dim]{markup_escape(args.url)}[/dim]\n")

        if args.audio_only:
            # Non-interactive audio (with optional clip)
            download_audio(args.url, args.output, cli_clip)
            return

        if cli_clip:
            # Non-interactive video clip — fetch formats, pick best, download clip
            formats, title, video_duration = fetch_formats(args.url)
            print(f"  {BOLD}Title:{RESET} {CYAN}{title}{RESET}\n")
            video_fmts = filter_video_formats(formats)
            if not video_fmts:
                print(f"{YELLOW}  No video formats — falling back to audio clip.{RESET}")
                download_audio(args.url, args.output, cli_clip, video_duration)
                return
            chosen = video_fmts[0]
            download_video(args.url, chosen, args.output, cli_clip, video_duration)
            return

    # ── Interactive menu loop ─────────────────────────────────────────────────
    current_output_dir = args.output

    while True:
        choice = run_curses_menu(
            title="viddown  —  What would you like to do?",
            options=MAIN_MENU_OPTIONS,
            subtitle=f"Save folder: {get_display_path(current_output_dir)}  ·  Use ↑ ↓ arrows to navigate",
        )

        # Quit (index 8 or Q/Escape)
        if choice == -1 or choice == 8:
            print(f"\n  {DIM}Bye! — RY9 · {CONTACT_EMAIL}{RESET}\n")
            break

        # Update yt-dlp (index 7)
        if choice == 7:
            update_ytdlp()
            input(f"\n  {DIM}Press Enter to return to menu…{RESET}")
            continue

        # Change save folder (index 6)
        if choice == 6:
            current_output_dir = prompt_change_output_dir(current_output_dir)
            input(f"\n  {DIM}Press Enter to return to menu…{RESET}")
            continue

        # Parallel downloads (index 4 = video, 5 = audio)
        if choice in (4, 5):
            audio_only = (choice == 5)
            urls = prompt_multiple_urls()
            if urls:
                parallel_download(urls, current_output_dir, audio_only)
            input(f"\n  {DIM}Press Enter to return to menu…{RESET}")
            continue

        # Single download — get URL
        if args.url:
            url = args.url
            console.print(f"  [bold]URL:[/bold] [dim]{markup_escape(url)}[/dim]\n")
        else:
            print()
            url = prompt_url()
            print()

        audio_only = choice in (2, 3)
        needs_clip = choice in (1, 3)

        clip: Optional[tuple[str, str]] = None
        if needs_clip:
            clip = prompt_clip_times()
            s_fmt, e_fmt = clip
            print(f"\n  {DIM}Clip: {CYAN}{s_fmt}{DIM} → {CYAN}{e_fmt}{RESET}\n")

        run_download_flow(url, current_output_dir, audio_only, clip)

        print(f"\n  {DIM}Press Enter to return to menu, or Ctrl+C to quit…{RESET}")
        try:
            input()
        except (EOFError, KeyboardInterrupt):
            print()
            break


if __name__ == "__main__":
    main()
