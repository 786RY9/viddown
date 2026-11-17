#!/usr/bin/env bash
# Blue/green self-update. Run daily by viddown-update.timer (04:00 UTC), as root.
#
#   update.sh               normal run: build the idle slot, smoke-test, flip on pass
#   update.sh --bootstrap   first install (no current slot yet)
#   update.sh --force       rebuild + smoke + flip even if nothing changed
#   update.sh --from-dir D  take the server code from a local checkout (repo root D)
#
# Layout:
#   /opt/viddown/slots/{a,b}/{app,venv,bin}   app = server bundle, bin = ffmpeg/ffprobe/deno
#   /opt/viddown/current -> slots/<live>      systemd runs current/venv/bin/python -m viddown_server
#
# Steps: (1) server code: newer GitHub release (or git), else copy live  (2) fresh venv with
# the *latest* yt-dlp  (3) ffmpeg linuxarm64 build, sha256-verified, only if it changed
# (4) deno (yt-dlp's JS runtime for YouTube)  (5) smoke.sh against the new slot
# (6) pass: flip `current`, publish validated versions to the manifest, graceful restart,
#     health check (auto-rollback if unhealthy). fail: keep the live slot, ntfy alert.
set -Eeuo pipefail
HERE=$(dirname "$(readlink -f "$0")")
# shellcheck source=common.sh
. "$HERE/common.sh"

BOOTSTRAP=0 FORCE=0 FROM_DIR=""
while [[ $# -gt 0 ]]; do
  case $1 in
    --bootstrap) BOOTSTRAP=1; FORCE=1 ;;
    --force) FORCE=1 ;;
    --from-dir) FROM_DIR=$2; shift ;;
    *) die "unknown argument $1" ;;
  esac
  shift
done

[[ $EUID -eq 0 ]] || die "run as root"
load_config
exec 9>/run/lock/viddown-update.lock
flock -n 9 || { log "another update is running"; exit 0; }

WORK=$(mktemp -d /var/tmp/viddown-update.XXXXXX)
NEW=""
FAILED=1
cleanup() {
  rm -rf "$WORK"
  if [[ $FAILED -eq 1 && -n $NEW ]]; then log "new slot left at $NEW for inspection"; fi
}
trap cleanup EXIT
trap 'ntfy "viddown update FAILED on $(hostname)" "update.sh failed at line $LINENO; live slot unchanged. journalctl -u viddown-update" high warning' ERR

CUR=$(readlink -f "$ROOT/current" 2>/dev/null || true)
[[ -n $CUR && -d $CUR ]] || CUR=""
if [[ -z $CUR && $BOOTSTRAP -eq 0 ]]; then die "no current slot; run with --bootstrap"; fi
CUR_SLOT=$([[ -n $CUR ]] && basename "$CUR" || echo none)
NEW_SLOT=$([[ $CUR_SLOT == a ]] && echo b || echo a)
NEW=$ROOT/slots/$NEW_SLOT
log "live slot: $CUR_SLOT, building: $NEW_SLOT"
rm -rf "$NEW"
mkdir -p "$NEW/bin"
CHANGES=()

# ── 1. server code ───────────────────────────────────────────────────────────
CUR_VER=$(cat "$CUR/app/VERSION" 2>/dev/null || echo 0)
BUNDLE=""
if [[ -n $FROM_DIR || $SOURCE == git ]]; then
  src=${FROM_DIR:-$ROOT/src}
  if [[ -z $FROM_DIR ]]; then
    if [[ -d $src/.git ]]; then
      git -C "$src" fetch --depth 1 origin "$GIT_REF" && git -C "$src" reset --hard FETCH_HEAD
    else
      git clone --depth 1 --branch "$GIT_REF" "https://github.com/$GITHUB_REPO" "$src"
    fi
  fi
  python3 "$src/server/scripts/build_bundle.py" --out "$WORK/dist" >/dev/null
  BUNDLE=$(ls "$WORK"/dist/viddown-server-*.tar.gz)
  REL_VER=$(basename "$BUNDLE" .tar.gz); REL_VER=${REL_VER#viddown-server-}
else
  REL_JSON=$(gh_api "repos/$GITHUB_REPO/releases?per_page=30" |
    jq -c '[.[] | select(.draft == false and .prerelease == false and (.tag_name | startswith("server-v")))][0] // empty')
  if [[ -n $REL_JSON ]]; then
    REL_VER=$(jq -r '.tag_name | ltrimstr("server-v")' <<<"$REL_JSON")
    if [[ -z $CUR ]] || version_gt "$REL_VER" "$CUR_VER"; then
      name="viddown-server-$REL_VER.tar.gz"
      url=$(jq -r --arg n "$name" '.assets[] | select(.name == $n) | .browser_download_url' <<<"$REL_JSON")
      sums=$(jq -r --arg n "$name.sha256" '.assets[] | select(.name == $n) | .browser_download_url' <<<"$REL_JSON")
      [[ -n $url && -n $sums ]] || die "release server-v$REL_VER lacks $name or its .sha256"
      fetch_verified "$url" "$sums" "$name" "$WORK/$name" >/dev/null
      BUNDLE=$WORK/$name
    fi
  else
    [[ -n $CUR ]] || die "no server-v* release found in $GITHUB_REPO; set SOURCE=git in $DEPLOY_ENV"
    REL_VER=$CUR_VER
  fi
fi

if [[ -n $BUNDLE ]] && { [[ -z $CUR ]] || version_gt "$REL_VER" "$CUR_VER" || [[ $FORCE -eq 1 ]]; }; then
  tar -xzf "$BUNDLE" -C "$NEW" --no-same-owner
  [[ -f $NEW/app/VERSION ]] || die "bundle has no app/VERSION"
  [[ $(cat "$NEW/app/VERSION") == "$CUR_VER" ]] || CHANGES+=("server $CUR_VER -> $(cat "$NEW/app/VERSION")")
else
  cp -a "$CUR/app" "$NEW/app"
fi

# ── 2. venv with the newest yt-dlp ───────────────────────────────────────────
install_uv
export UV_CACHE_DIR=/var/cache/viddown-uv UV_PYTHON_PREFERENCE=only-system
uv venv --quiet --python python3.12 "$NEW/venv"
uv pip install --quiet --python "$NEW/venv/bin/python" --upgrade -r "$NEW/app/requirements.txt"
YTDLP_NEW=$("$NEW/venv/bin/python" -m yt_dlp --version)
YTDLP_CUR=$([[ -n $CUR ]] && "$CUR/venv/bin/python" -m yt_dlp --version 2>/dev/null || echo none)
[[ $YTDLP_NEW == "$YTDLP_CUR" ]] || CHANGES+=("yt-dlp $YTDLP_CUR -> $YTDLP_NEW")

# ── 3. ffmpeg (yt-dlp/FFmpeg-Builds, linuxarm64, sha256-verified) ────────────
FF_SUMS="$FFMPEG_BASE_URL/checksums.sha256"
FF_WANT=$(remote_sha "$FF_SUMS" "$FFMPEG_ASSET" || true)
reuse_ff=0
if [[ -n $CUR && -x $CUR/bin/ffmpeg ]]; then
  if [[ -z $FF_WANT ]]; then
    reuse_ff=1  # can't reach the checksum list: keep the build that works
  elif [[ -f $CUR/bin/.ffmpeg.sha256 && $(cat "$CUR/bin/.ffmpeg.sha256") == "$FF_WANT" ]]; then
    reuse_ff=1  # upstream build unchanged
  fi
fi
if [[ $reuse_ff -eq 1 ]]; then
  cp -a "$CUR/bin/ffmpeg" "$CUR/bin/ffprobe" "$NEW/bin/"
  cp -a "$CUR/bin/.ffmpeg.sha256" "$NEW/bin/" 2>/dev/null || true
else
  log "downloading $FFMPEG_ASSET"
  sha=$(fetch_verified "$FFMPEG_BASE_URL/$FFMPEG_ASSET" "$FF_SUMS" "$FFMPEG_ASSET" "$WORK/ffmpeg.tar.xz")
  mkdir -p "$WORK/ff" && tar -xJf "$WORK/ffmpeg.tar.xz" -C "$WORK/ff"
  install -m 0755 "$WORK"/ff/*/bin/ffmpeg "$WORK"/ff/*/bin/ffprobe "$NEW/bin/"
  echo "$sha" >"$NEW/bin/.ffmpeg.sha256"
  CHANGES+=("ffmpeg build ${sha:0:12}")
fi
FFMPEG_VER=$("$NEW/bin/ffmpeg" -hide_banner -version | awk 'NR==1 {print $3}')

# ── 4. deno (JavaScript runtime yt-dlp uses for YouTube) ─────────────────────
DENO_TAG=$(gh_api repos/denoland/deno/releases/latest | jq -r .tag_name || true)
if [[ -n $CUR && -x $CUR/bin/deno && ( -z $DENO_TAG || $(cat "$CUR/bin/.deno.version" 2>/dev/null) == "$DENO_TAG" ) ]]; then
  cp -a "$CUR/bin/deno" "$CUR/bin/.deno.version" "$NEW/bin/" 2>/dev/null || true
elif [[ -n $DENO_TAG ]]; then
  base="https://github.com/denoland/deno/releases/download/$DENO_TAG"
  fetch_verified "$base/$DENO_ASSET" "$base/$DENO_ASSET.sha256sum" "$DENO_ASSET" "$WORK/deno.zip" >/dev/null
  unzip -q -o "$WORK/deno.zip" -d "$WORK/deno"
  install -m 0755 "$WORK/deno/deno" "$NEW/bin/deno"
  echo "$DENO_TAG" >"$NEW/bin/.deno.version"
  CHANGES+=("deno $DENO_TAG")
fi

if [[ ${#CHANGES[@]} -eq 0 && $FORCE -eq 0 ]]; then
  log "everything is up to date (yt-dlp $YTDLP_NEW, ffmpeg $FFMPEG_VER, server $CUR_VER)"
  rm -rf "$NEW"; NEW=""; FAILED=0
  exit 0
fi
log "changes: ${CHANGES[*]:-forced rebuild}"

# The service user may read and execute the slot but never modify it.
chown -R root:root "$NEW"
chmod -R u=rwX,go=rX "$NEW"

# ── 5. smoke test the new slot ───────────────────────────────────────────────
if ! "$HERE/smoke.sh" --slot "$NEW" >"$WORK/smoke.log" 2>&1; then
  cat "$WORK/smoke.log" >&2
  ntfy "viddown update REJECTED on $(hostname)" \
    "Smoke test failed for: ${CHANGES[*]:-forced rebuild}. Live slot $CUR_SLOT kept.
$(tail -n 15 "$WORK/smoke.log")" high warning
  trap - ERR
  exit 1
fi
cat "$WORK/smoke.log" >&2

# ── 6. flip, publish, restart, verify ────────────────────────────────────────
flip() { ln -sfn "slots/$1" "$ROOT/current.new" && mv -Tf "$ROOT/current.new" "$ROOT/current"; }
flip "$NEW_SLOT"
mkdir -p "$STATE"
jq -n --arg server "$(cat "$NEW/app/VERSION")" --arg ytdlp "$YTDLP_NEW" --arg ffmpeg "$FFMPEG_VER" \
  --arg deno "$(cat "$NEW/bin/.deno.version" 2>/dev/null || echo unknown)" \
  --arg at "$(date -u +%FT%TZ)" --arg slot "$NEW_SLOT" \
  '{server: $server, ytdlp: $ytdlp, ffmpeg: $ffmpeg, deno: $deno, validatedAt: $at, slot: $slot}' \
  >"$STATE/validated.json.new"
[[ -f $STATE/validated.json ]] && cp -a "$STATE/validated.json" "$WORK/validated.prev"
chown viddown:viddown "$STATE/validated.json.new" 2>/dev/null || true
mv -f "$STATE/validated.json.new" "$STATE/validated.json"

log "restarting viddown (graceful drain, up to 5 min)"
systemctl daemon-reload
systemctl enable viddown.service >/dev/null 2>&1 || true
systemctl restart viddown.service
PORT=$(sed -n 's/^VIDDOWN_PORT=//p' "$ETC/viddown.env" 2>/dev/null | tail -n1)
healthy=0
for _ in $(seq 1 60); do
  if curl -fsS -m 3 "http://127.0.0.1:${PORT:-8000}/healthz" >/dev/null 2>&1; then healthy=1; break; fi
  sleep 1
done
if [[ $healthy -ne 1 ]]; then
  if [[ -n $CUR ]]; then
    log "new slot unhealthy after restart; rolling back to $CUR_SLOT"
    flip "$CUR_SLOT"
    [[ -f $WORK/validated.prev ]] && cp -a "$WORK/validated.prev" "$STATE/validated.json"
    systemctl restart viddown.service
  fi
  ntfy "viddown ROLLED BACK on $(hostname)" "Service failed health check after update (${CHANGES[*]:-forced}); back on slot $CUR_SLOT." urgent rotating_light
  trap - ERR
  exit 1
fi

FAILED=0
log "live on slot $NEW_SLOT: ${CHANGES[*]:-forced rebuild}"
ntfy "viddown updated" "${CHANGES[*]:-forced rebuild} (slot $NEW_SLOT)" low white_check_mark
