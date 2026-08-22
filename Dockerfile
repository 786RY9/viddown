# ── Stage: runtime ────────────────────────────────────────────────────────────
FROM python:3.12-slim

LABEL maintainer="viddown"
LABEL description="CLI video downloader for YouTube, Instagram, TikTok — powered by yt-dlp"

# System deps: ffmpeg (for merging/encoding) + curl (for yt-dlp self-update)
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        ffmpeg \
        curl \
        ca-certificates && \
    rm -rf /var/lib/apt/lists/*

# Install yt-dlp (latest stable via pip)
RUN pip install --no-cache-dir "yt-dlp[default]" "rich>=13"

# Copy the downloader script
WORKDIR /app
COPY downloader.py /app/downloader.py

# Default downloads directory (mount a host volume here)
RUN mkdir -p /downloads

ENTRYPOINT ["python", "/app/downloader.py"]
