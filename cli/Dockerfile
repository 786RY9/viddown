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

WORKDIR /app
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# Copy the shared core + downloader script
COPY viddown_core /app/viddown_core
COPY downloader.py /app/downloader.py

# Default downloads directory (mount a host volume here)
RUN mkdir -p /downloads

ENTRYPOINT ["python", "/app/downloader.py"]
