#!/usr/bin/env bash
# Smoke test: probe + stream a few seconds from Creative-Commons test videos.
#
#   smoke.sh --slot /opt/viddown/slots/b   start a throw-away instance from that slot (as the
#                                          viddown user, on 127.0.0.1:$SMOKE_PORT) and test it
#   smoke.sh --url http://127.0.0.1:8000   test an already-running instance
#
# Config (env or /etc/viddown/deploy.env):
#   SMOKE_URLS       space-separated page URLs (default: Big Buck Bunny on the Internet Archive + YouTube; YouTube usually
#                    blocks cloud IPs, so SMOKE_MIN_PASS=1 lets the Archive copy carry the test)
#   SMOKE_MIN_PASS   how many URLs must pass (default 1: YouTube often blocks datacenter IPs)
#   SMOKE_SECONDS    how long to stream each (default 5)
#   SMOKE_MIN_BYTES  minimum bytes received in that time (default 100000)
#   SMOKE_PORT       port for --slot mode (default 8099)
set -Eeuo pipefail
HERE=$(dirname "$(readlink -f "$0")")
# shellcheck source=common.sh
. "$HERE/common.sh"
load_config

SMOKE_URLS=${SMOKE_URLS:-"https://archive.org/details/BigBuckBunny_124 https://www.youtube.com/watch?v=aqz-KE-bpKQ"}
SMOKE_MIN_PASS=${SMOKE_MIN_PASS:-1}
SMOKE_SECONDS=${SMOKE_SECONDS:-5}
SMOKE_MIN_BYTES=${SMOKE_MIN_BYTES:-100000}
SMOKE_PORT=${SMOKE_PORT:-8099}

SLOT="" BASE=""
case ${1:-} in
  --slot) SLOT=$(readlink -f "$2") ;;
  --url) BASE=${2%/} ;;
  *) echo "usage: $0 --slot DIR | --url BASE_URL" >&2; exit 2 ;;
esac

TMP=$(mktemp -d /var/tmp/viddown-smoke.XXXXXX)
PID=""
finish() {
  if [[ -n $PID ]]; then
    kill -TERM "$PID" 2>/dev/null || true
    for _ in $(seq 1 20); do kill -0 "$PID" 2>/dev/null || break; sleep 0.5; done
    kill -KILL "$PID" 2>/dev/null || true
  fi
  # On failure, show what the server itself logged before the evidence is deleted.
  if [[ ${SMOKE_FAILED:-0} -eq 1 && -s $TMP/server.log ]]; then
    echo "---- smoke server log (last 40 lines) ----" >&2
    tail -n 40 "$TMP/server.log" >&2
  fi
  rm -rf "$TMP"
}
trap finish EXIT

if [[ -n $SLOT ]]; then
  [[ -x $SLOT/venv/bin/python ]] || die "$SLOT has no venv"
  mkdir -p "$TMP/state" "$TMP/run"
  id viddown >/dev/null 2>&1 && chown -R viddown:viddown "$TMP"
  chmod 0700 "$TMP"
  # Same config as production (extractor allow-list etc.), but isolated dirs/port/secret.
  envs=()
  if [[ -r $ETC/viddown.env ]]; then
    while IFS= read -r line; do
      [[ $line =~ ^[A-Z_][A-Z0-9_]*= ]] && envs+=("$line")
    done <"$ETC/viddown.env"
  fi
  envs+=(
    "VIDDOWN_PORT=$SMOKE_PORT" "VIDDOWN_HOST=127.0.0.1"
    "VIDDOWN_STATE_DIR=$TMP/state" "VIDDOWN_RUNTIME_DIR=$TMP/run"
    "VIDDOWN_SECRET=$(head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n')"
    "VIDDOWN_FFMPEG=$SLOT/bin/ffmpeg" "VIDDOWN_EXTRACT_WORKERS=1"
    "VIDDOWN_MANIFEST_BASE=" "VIDDOWN_NTFY_TOPIC="
    "PATH=$SLOT/bin:$SLOT/venv/bin:/usr/local/bin:/usr/bin:/bin"
    "XDG_CACHE_HOME=${XDG_CACHE_HOME:-/var/cache/viddown}" "PYTHONDONTWRITEBYTECODE=1"
  )
  runner=()
  [[ $EUID -eq 0 ]] && id viddown >/dev/null 2>&1 && runner=(runuser -u viddown --)
  (cd "$SLOT/app" && exec "${runner[@]}" env -i "${envs[@]}" "$SLOT/venv/bin/python" -m viddown_server) \
    >"$TMP/server.log" 2>&1 &
  PID=$!
  BASE="http://127.0.0.1:$SMOKE_PORT"
  for _ in $(seq 1 90); do
    curl -fsS -m 2 "$BASE/healthz" >/dev/null 2>&1 && break
    kill -0 "$PID" 2>/dev/null || { cat "$TMP/server.log" >&2; die "server exited during startup"; }
    sleep 1
  done
fi

curl -fsS -m 5 "$BASE/healthz" >/dev/null || { [[ -f $TMP/server.log ]] && cat "$TMP/server.log" >&2; die "healthz failed"; }
curl -fsS -m 5 "$BASE/v1/manifest" | jq -e '.schema == 1' >/dev/null || die "manifest invalid"
log "healthz + manifest ok at $BASE"

pass=0 total=0
for url in $SMOKE_URLS; do
  total=$((total + 1))
  t0=$(date +%s)
  # No -f: keep the error body so the failure reason is logged.
  code=$(curl -sS -m 90 -H 'Content-Type: application/json' -w '%{http_code}' \
      --data "$(jq -cn --arg u "$url" '{url: $u}')" "$BASE/v1/probe" -o "$TMP/probe.json" || echo 000)
  if [[ $code != 200 ]]; then
    reason=$(jq -r '(.error.kind? // "") + ": " + (.error.message? // "")' "$TMP/probe.json" 2>/dev/null ||
      head -c 300 "$TMP/probe.json" 2>/dev/null)
    log "FAIL probe $url: HTTP $code ${reason:0:400}"
    continue
  fi
  # Pick a modest quality (<= 480p) to keep the test cheap; fall back to the default.
  path=$(jq -r '
    ([.videoFormats[] | select((.height // 0) <= 480)] | first | .format_id) as $id
    | if $id and .streamTokens[$id] then "/v1/stream/" + .streamTokens[$id] else .stream.path end' "$TMP/probe.json")
  title=$(jq -r .title "$TMP/probe.json")
  rc=0
  curl -sS -m "$SMOKE_SECONDS" -o "$TMP/out.bin" "$BASE$path" || rc=$?
  bytes=$(stat -c %s "$TMP/out.bin" 2>/dev/null || echo 0)
  # Compare in hex: raw media bytes include NULs, which bash can't hold in a variable.
  magic=$(head -c 12 "$TMP/out.bin" 2>/dev/null | od -An -tx1 | tr -d ' \n' || true)
  media=0
  if [[ ${magic:8:8} == 66747970 ]]; then media=1                       # MP4: "ftyp" at offset 4
  elif [[ ${magic:0:6} == 494433 ]]; then media=1                       # MP3: "ID3" tag
  elif [[ ${magic:0:2} == ff && -n ${magic:2:2} ]] && (( (0x${magic:2:2} & 0xE0) == 0xE0 )); then media=1  # MP3 frame sync
  fi
  # rc 28 = our own --max-time cut the stream off, which is expected.
  if [[ ($rc -eq 0 || $rc -eq 28) && $bytes -ge $SMOKE_MIN_BYTES && $media -eq 1 ]]; then
    pass=$((pass + 1))
    log "PASS $url ($title): $bytes bytes in ${SMOKE_SECONDS}s, probe+stream $(($(date +%s) - t0))s"
  else
    log "FAIL stream $url: curl rc=$rc, $bytes bytes, magic='$magic'"
  fi
  rm -f "$TMP/out.bin"
done

if [[ -n $PID ]]; then
  # Zero-disk + no-orphans check: the job dirs must be gone once streams end.
  sleep 2
  left=$(find "$TMP/run" -mindepth 1 -maxdepth 1 -name 'job-*' 2>/dev/null | wc -l)
  [[ $left -eq 0 ]] || { log "FAIL: $left job dirs left behind"; pass=0; }
fi

log "smoke: $pass/$total passed (need $SMOKE_MIN_PASS)"
if [[ $pass -lt $SMOKE_MIN_PASS ]]; then SMOKE_FAILED=1; exit 1; fi
