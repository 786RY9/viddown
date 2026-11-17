#!/usr/bin/env bash
# Self-contained load test inside the Linux test image (no internet needed):
#   python scripts/vendor_core.py
#   docker build -f Dockerfile.test -t viddown-server-test .
#   docker run --rm --memory 2g --cpus 2 viddown-server-test bash loadtest/docker-loadtest.sh
#
# It generates a DASH test video, serves it locally, starts the server with
# deliberately small capacity (so saturation is reached), runs loadtest.py with
# slow and abandoning readers, then checks: no 5xx except 503+Retry-After, no
# orphaned ffmpeg/yt-dlp, no job dirs left (zero-disk), state dir unchanged.
set -Eeuo pipefail
CLIENTS=${CLIENTS:-24}
DURATION=${DURATION:-60}
WORK=$(mktemp -d)
RUN=$WORK/run
STATE=$WORK/state
mkdir -p "$RUN" "$STATE" "$WORK/media"

echo "== generating 60 s DASH test asset"
ffmpeg -v error -f lavfi -i testsrc2=size=854x480:rate=25 -f lavfi -i sine=frequency=440:sample_rate=48000 \
  -t 60 -map 0:v -map 1:a -c:v libx264 -preset ultrafast -crf 23 -g 25 -keyint_min 25 -sc_threshold 0 \
  -pix_fmt yuv420p -c:a aac -b:a 128k -f dash -seg_duration 2 -use_template 1 -use_timeline 0 \
  -adaptation_sets "id=0,streams=v id=1,streams=a" "$WORK/media/manifest.mpd"
(cd "$WORK/media" && exec python -m http.server 9000 --bind 127.0.0.1 >/dev/null 2>&1) &
MEDIA_PID=$!

echo "== starting server (stream_slots=6, max_waiters=4, queue wait 5 s, stall 10 s)"
env VIDDOWN_SECRET=loadtest-secret-loadtest-secret-x VIDDOWN_PORT=8000 \
  VIDDOWN_STATE_DIR="$STATE" VIDDOWN_RUNTIME_DIR="$RUN" \
  VIDDOWN_STREAM_SLOTS=6 VIDDOWN_EXTRACT_WORKERS=2 VIDDOWN_MAX_WAITERS=4 VIDDOWN_QUEUE_WAIT_S=5 \
  VIDDOWN_STALL_TIMEOUT_S=10 VIDDOWN_PROBE_RATE_PER_MIN=600 VIDDOWN_STREAM_RATE_PER_MIN=600 \
  VIDDOWN_UNSAFE_ALLOW_GENERIC=1 VIDDOWN_UNSAFE_ALLOW_PRIVATE_URLS=1 VIDDOWN_ALLOWED_EXTRACTORS=generic \
  VIDDOWN_LOG_LEVEL=WARNING \
  python -m viddown_server >"$WORK/server.log" 2>&1 &
SERVER_PID=$!
trap 'kill $SERVER_PID $MEDIA_PID 2>/dev/null || true; rm -rf "$WORK"' EXIT
for _ in $(seq 1 60); do curl -fsS -m 2 http://127.0.0.1:8000/healthz >/dev/null 2>&1 && break; sleep 0.5; done
curl -fsS http://127.0.0.1:8000/healthz | python -c 'import json,sys; d=json.load(sys.stdin); print("budget:", d["budget"])'

state_before=$(du -sb "$STATE" | cut -f1)
rss_peak=0
( while kill -0 $SERVER_PID 2>/dev/null; do
    # RSS of the server and all its descendants, in MB.
    rss=$(ps -eo pid,ppid,rss --no-headers | awk -v root=$SERVER_PID '
      {pp[$1]=$2; r[$1]=$3} END {for (p in r) {q=p; while (q && q!=root) q=pp[q]; if (q==root) s+=r[p]} print int(s/1024)}')
    echo "$rss" >>"$WORK/rss.txt"; sleep 1
  done ) &

python loadtest/loadtest.py --base http://127.0.0.1:8000 --url http://127.0.0.1:9000/manifest.mpd \
  --clients "$CLIENTS" --duration "$DURATION" --stream-seconds 8 --slow-kbps 24 && LT=0 || LT=$?

sleep 3
echo "== post-run checks"
orphans=$(pgrep -fa "$RUN" || true)
jobs=$(find "$RUN" -mindepth 1 -maxdepth 1 | wc -l)
state_after=$(du -sb "$STATE" | cut -f1)
rss_peak=$(sort -n "$WORK/rss.txt" | tail -n1)
echo "  orphan processes: ${orphans:-none}"
echo "  job dirs left in runtime dir: $jobs"
echo "  state dir bytes before/after: $state_before / $state_after (SQLite only; media never touches disk)"
echo "  peak RSS of server + children: ${rss_peak} MB"
grep -E "ERROR|Traceback" "$WORK/server.log" | head -5 || true
[[ $LT -eq 0 && -z $orphans && $jobs -eq 0 ]] && echo "LOADTEST PASS" || { echo "LOADTEST FAIL"; exit 1; }
