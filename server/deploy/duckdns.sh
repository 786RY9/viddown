#!/usr/bin/env bash
# Point <name>.duckdns.org at this machine. DuckDNS uses the caller's IP when ip= is empty.
set -Eeuo pipefail
HERE=$(dirname "$(readlink -f "$0")")
# shellcheck source=common.sh
. "$HERE/common.sh"
load_config
[[ -n ${DUCKDNS_TOKEN:-} && -n ${VIDDOWN_DOMAIN:-} ]] || { log "DuckDNS not configured"; exit 0; }
sub=${VIDDOWN_DOMAIN%.duckdns.org}
resp=$(curl -fsS -m 30 "https://www.duckdns.org/update?domains=${sub}&token=${DUCKDNS_TOKEN}&ip=")
[[ $resp == OK ]] || die "DuckDNS update failed: $resp"
log "DuckDNS $VIDDOWN_DOMAIN updated"
