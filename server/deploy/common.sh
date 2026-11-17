#!/usr/bin/env bash
# Shared helpers for install.sh / update.sh / smoke.sh / backup.sh. Source, don't run.

ROOT=${VIDDOWN_ROOT:-/opt/viddown}
ETC=${VIDDOWN_ETC:-/etc/viddown}
STATE=${VIDDOWN_STATE:-/var/lib/viddown}
DEPLOY_ENV=${DEPLOY_ENV:-$ETC/deploy.env}

FFMPEG_BASE_URL=${FFMPEG_BASE_URL:-https://github.com/yt-dlp/FFmpeg-Builds/releases/download/latest}
FFMPEG_ASSET=${FFMPEG_ASSET:-ffmpeg-master-latest-linuxarm64-gpl.tar.xz}
UV_ASSET=${UV_ASSET:-uv-aarch64-unknown-linux-gnu.tar.gz}
DENO_ASSET=${DENO_ASSET:-deno-aarch64-unknown-linux-gnu.zip}

log() { printf '%s [%s] %s\n' "$(date -u +%FT%TZ)" "${0##*/}" "$*" >&2; }
die() { log "ERROR: $*"; exit 1; }

load_config() {
  # shellcheck disable=SC1090
  [[ -f $DEPLOY_ENV ]] && { set -a; . "$DEPLOY_ENV"; set +a; }
  GITHUB_REPO=${GITHUB_REPO:-786RY9/viddown}
  SOURCE=${SOURCE:-release}
  GIT_REF=${GIT_REF:-main}
  NTFY_URL=${NTFY_URL:-https://ntfy.sh}
  NTFY_TOPIC=${NTFY_TOPIC:-}
}

# ntfy TITLE MESSAGE [PRIORITY] [TAGS] — never fails the caller.
ntfy() {
  [[ -n ${NTFY_TOPIC:-} ]] || return 0
  curl -fsS -m 15 -H "Title: $1" -H "Priority: ${3:-default}" -H "Tags: ${4:-gear}" \
    --data-binary "$2" "$NTFY_URL/$NTFY_TOPIC" >/dev/null 2>&1 || log "ntfy push failed"
}

# gh_api PATH — unauthenticated GitHub API (60 req/h is plenty for a daily job).
gh_api() { curl -fsSL -m 30 -H 'Accept: application/vnd.github+json' "https://api.github.com/$1"; }

# fetch_verified URL SUMS_URL ASSET_NAME DEST — download and check against a sha256sum-style file.
# Note: the checksum comes from the same GitHub release as the file, so this
# guards against corrupt/truncated downloads, not against a compromised release.
fetch_verified() {
  local url=$1 sums_url=$2 name=$3 dest=$4 want got
  want=$(curl -fsSL -m 60 "$sums_url" | awk -v n="$name" '$2 == n || $2 == "*"n {print $1; exit}')
  [[ $want =~ ^[0-9a-f]{64}$ ]] || die "no sha256 for $name in $sums_url"
  curl -fsSL -m 900 --retry 3 -o "$dest" "$url"
  got=$(sha256sum "$dest" | awk '{print $1}')
  [[ $got == "$want" ]] || die "sha256 mismatch for $name (want $want, got $got)"
  printf '%s' "$want"
}

# sha256 of a release asset without downloading it (to skip unchanged ffmpeg builds).
remote_sha() { curl -fsSL -m 60 "$1" | awk -v n="$2" '$2 == n || $2 == "*"n {print $1; exit}'; }

# version_gt A B — true if A > B (sort -V semantics).
version_gt() { [[ $1 != "$2" && $(printf '%s\n%s\n' "$1" "$2" | sort -V | tail -n1) == "$1" ]]; }

install_uv() {
  local tag have=""
  tag=$(gh_api repos/astral-sh/uv/releases/latest | jq -r .tag_name) || { log "cannot query uv release"; return 0; }
  command -v uv >/dev/null && have=$(uv --version | awk '{print $2}')
  if [[ $have == "$tag" ]]; then return 0; fi
  log "installing uv $tag (had ${have:-none})"
  local tmp; tmp=$(mktemp -d)
  fetch_verified "https://github.com/astral-sh/uv/releases/download/$tag/$UV_ASSET" \
    "https://github.com/astral-sh/uv/releases/download/$tag/$UV_ASSET.sha256" "$UV_ASSET" "$tmp/uv.tgz" >/dev/null
  tar -xzf "$tmp/uv.tgz" -C "$tmp"
  install -m 0755 "$tmp"/uv-*/uv "$tmp"/uv-*/uvx /usr/local/bin/
  rm -rf "$tmp"
}
