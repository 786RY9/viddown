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
    curses.curs_set(0)
    curses.start_color()
    curses.use_default_colors()
    curses.init_pair(1, curses.COLOR_CYAN,   -1)  # selected item
    curses.init_pair(2, curses.COLOR_YELLOW, -1)  # title
    curses.init_pair(3, curses.COLOR_WHITE,  -1)  # normal item
    curses.init_pair(4, curses.COLOR_BLACK,  -1)  # dim / hint

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
    return curses.wrapper(curses_menu, title, options, subtitle)


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


# ─── Rich progress ────────────────────────────────────────────────────────────
DOWNLOAD_RE = re.compile(
    r"\[download\]\s+([\d.]+)%\s+of\s+~?\s*([\d.]+)\s*([\w]+)"
)


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


def _worker(yt_args: list[str], label: str, progress: Progress, task_id: TaskID) -> int:
    """
    Run a single yt-dlp process, updating the given Rich progress task.
    Returns the yt-dlp exit code.
    """
    proc = subprocess.Popen(
        ["yt-dlp"] + yt_args,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )

    phase    = 0      # 0 = video stream, 1 = audio stream, 2 = merging
    last_pct = 0.0

    for raw_line in proc.stdout:
        line = raw_line.rstrip()
        if not line:
            continue

        match_obj = DOWNLOAD_RE.search(line)
        if match_obj:
            pct = float(match_obj.group(1))
            # Detect phase reset: yt-dlp restarts from ~0% when downloading audio
            if pct < last_pct - 10 and last_pct > 80:
                phase += 1
                safe_label = markup_escape(label[:30])
                new_desc = (f"{safe_label} [dim](audio)[/dim]"
                            if phase == 1 else markup_escape(label[:40]))
                progress.update(task_id, description=new_desc, completed=0)
            last_pct = pct
            progress.update(task_id, completed=pct)

        elif "[Merger]" in line or "[SectionDownloader]" in line:
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
            # Use markup_escape so yt-dlp error text can't break Rich rendering
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
    "--fragment-retries", "infinite",  # retry failed fragments until success
    "--retries", "10",                 # retry full download up to 10 times
    "--retry-sleep", "5",              # wait 5 s between retries
]


def _base_args(
    url: str,
    output_template: str,
    clip: Optional[tuple[str, str]] = None,
) -> list[str]:
    args = RESUME_FLAGS + [
        "-o", output_template,
        "--no-playlist",
        "--progress",
        "--newline",
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
    return subprocess.run(["yt-dlp"] + args, capture_output=True, text=True)


def update_ytdlp() -> None:
    print(f"\n{CYAN}  Updating yt-dlp…{RESET}\n")
    proc = subprocess.Popen(
        ["pip", "install", "--upgrade", "yt-dlp"],
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


def fetch_formats(url: str) -> tuple[list[dict], str]:
    print(f"  {DIM}Fetching available formats…{RESET}")
    result = run_ytdlp(["--dump-json", "--no-playlist", url])
    if result.returncode != 0:
        print(f"\n{RED}  Error fetching video info:{RESET}")
        print(f"  {DIM}{result.stderr[:600]}{RESET}")
        sys.exit(1)
    info = json.loads(result.stdout)
    return info.get("formats", []), info.get("title", "Unknown title")


def filter_video_formats(formats: list[dict]) -> list[dict]:
    """Deduplicate by resolution (keep highest bitrate), sort best → worst."""
    seen: dict[int, dict] = {}
    for fmt in formats:
        if fmt.get("vcodec", "none") == "none" or not fmt.get("height"):
            continue
        h = fmt["height"]
        if h not in seen or (fmt.get("tbr") or 0) > (seen[h].get("tbr") or 0):
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
    _run_single(yt_args, label, output_dir)


def download_audio(
    url: str,
    output_dir: str,
    clip: Optional[tuple[str, str]] = None,
) -> None:
    label = (f"audio clip ({clip[0]} → {clip[1]})" if clip
             else "best audio (MP3)")
    tmpl  = ("%(title)s [audio] [%(section_start)s-%(section_end)s].%(ext)s"
             if clip else "%(title)s [audio].%(ext)s")

    console.print(f"\n  [cyan]Downloading {markup_escape(label)}…[/cyan]")
    yt_args = ["-f", "bestaudio/best", "-x",
               "--audio-format", "mp3", "--audio-quality", "0"]
    yt_args += _base_args(url, os.path.join(output_dir, tmpl), clip)
    _run_single(yt_args, label, output_dir)


def _run_single(yt_args: list[str], label: str, output_dir: str) -> None:
    """Run a single download with a Rich progress bar."""
    with _make_progress() as progress:
        task_id = progress.add_task(
            f"[cyan]{markup_escape(label[:40])}[/cyan]", total=100
        )
        rc = _worker(yt_args, label[:40], progress, task_id)

    if rc == 0:
        console.print(
            f"\n  [bold green]✓ Done![/bold green]  "
            f"[dim]Saved to {markup_escape(output_dir)}[/dim]"
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

    if audio_only:
        download_audio(url, output_dir, clip)
        return

    formats, title = fetch_formats(url)
    print(f"  {BOLD}Title:{RESET} {CYAN}{title}{RESET}\n")

    video_fmts = filter_video_formats(formats)
    if not video_fmts:
        print(f"{YELLOW}  No video formats found — falling back to audio-only.{RESET}")
        download_audio(url, output_dir, clip)
        return

    chosen = pick_video_quality(video_fmts, title)
    if chosen is _BACK:
        return
    if chosen is None:
        download_audio(url, output_dir, clip)
    else:
        download_video(url, chosen, output_dir, clip)


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
            formats, title = fetch_formats(args.url)
            print(f"  {BOLD}Title:{RESET} {CYAN}{title}{RESET}\n")
            video_fmts = filter_video_formats(formats)
            if not video_fmts:
                print(f"{YELLOW}  No video formats — falling back to audio clip.{RESET}")
                download_audio(args.url, args.output, cli_clip)
                return
            chosen = pick_video_quality(video_fmts, title)
            if chosen is _BACK or chosen is None:
                download_audio(args.url, args.output, cli_clip)
            else:
                download_video(args.url, chosen, args.output, cli_clip)
            return

    # ── Interactive menu loop ─────────────────────────────────────────────────
    while True:
        choice = run_curses_menu(
            title="viddown  —  What would you like to do?",
            options=MAIN_MENU_OPTIONS,
            subtitle="Use ↑ ↓ arrows to navigate, Enter to select",
        )

        # Quit (index 7 or Q/Escape)
        if choice == -1 or choice == 7:
            print(f"\n  {DIM}Bye! — RY9 · {CONTACT_EMAIL}{RESET}\n")
            break

        # Update yt-dlp (index 6)
        if choice == 6:
            update_ytdlp()
            input(f"\n  {DIM}Press Enter to return to menu…{RESET}")
            continue

        # Parallel downloads (index 4 = video, 5 = audio)
        if choice in (4, 5):
            audio_only = (choice == 5)
            urls = prompt_multiple_urls()
            if urls:
                parallel_download(urls, args.output, audio_only)
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

        run_download_flow(url, args.output, audio_only, clip)

        print(f"\n  {DIM}Press Enter to return to menu, or Ctrl+C to quit…{RESET}")
        try:
            input()
        except (EOFError, KeyboardInterrupt):
            print()
            break


if __name__ == "__main__":
    main()
