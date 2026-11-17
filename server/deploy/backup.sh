#!/usr/bin/env bash
# Nightly SQLite backup to OCI Object Storage (Always-Free: 20 GB) via a
# pre-authenticated request (PAR) URL, so no OCI credentials live on the box.
#
# BACKUP_PAR_URL in /etc/viddown/deploy.env must be a *bucket* PAR with
# "Permit object writes", ending in /o/  e.g.
#   https://objectstorage.<region>.oraclecloud.com/p/<secret>/n/<namespace>/b/viddown-backups/o/
# Old backups are pruned by an Object Storage lifecycle rule (see README).
set -Eeuo pipefail
HERE=$(dirname "$(readlink -f "$0")")
# shellcheck source=common.sh
. "$HERE/common.sh"
load_config

[[ -n ${BACKUP_PAR_URL:-} ]] || { log "BACKUP_PAR_URL not set; skipping backup"; exit 0; }
DB=$STATE/viddown.db
[[ -f $DB ]] || { log "no database yet"; exit 0; }

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
# .backup takes a consistent snapshot even while the service is writing (WAL mode).
sqlite3 "$DB" ".backup '$TMP/viddown.db'"
sqlite3 "$TMP/viddown.db" "PRAGMA integrity_check;" | grep -qx ok || die "backup failed integrity check"
gzip -9 "$TMP/viddown.db"
name="$(hostname -s)/viddown-$(date -u +%Y%m%dT%H%M%SZ).db.gz"
if curl -fsS -m 300 --retry 3 -X PUT --data-binary "@$TMP/viddown.db.gz" "${BACKUP_PAR_URL%/}/$name" >/dev/null; then
  log "uploaded $name ($(stat -c %s "$TMP/viddown.db.gz") bytes)"
else
  ntfy "viddown backup FAILED on $(hostname)" "Could not upload $name to Object Storage" high warning
  exit 1
fi
