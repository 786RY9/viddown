#!/usr/bin/env bash
# One-shot (and re-runnable) host setup for Ubuntu 24.04 aarch64 on Oracle Ampere A1.
# cloud-init runs this; you can also run it by hand:   sudo bash server/deploy/install.sh
#
# Reads /etc/viddown/deploy.env (VIDDOWN_DOMAIN, DUCKDNS_TOKEN, ACME_EMAIL, NTFY_TOPIC, ...).
# Idempotent: re-running only fixes drift.
set -Eeuo pipefail
HERE=$(dirname "$(readlink -f "$0")")
SERVER_DIR=$(dirname "$HERE")
# shellcheck source=common.sh
. "$HERE/common.sh"
[[ $EUID -eq 0 ]] || die "run as root (sudo)"
[[ -f $DEPLOY_ENV ]] || die "create $DEPLOY_ENV first (see server/README.md)"
load_config
: "${VIDDOWN_DOMAIN:?set VIDDOWN_DOMAIN in $DEPLOY_ENV}"
: "${ACME_EMAIL:?set ACME_EMAIL in $DEPLOY_ENV}"
export DEBIAN_FRONTEND=noninteractive

# ── packages ─────────────────────────────────────────────────────────────────
log "installing packages"
apt-get update -q
apt-get install -y -q curl jq xz-utils unzip sqlite3 git gnupg ca-certificates \
  python3.12 python3.12-venv unattended-upgrades iptables-persistent netfilter-persistent >/dev/null
if ! command -v caddy >/dev/null; then
  log "adding the Caddy apt repository"
  curl -fsSL https://dl.cloudsmith.io/public/caddy/stable/gpg.key |
    gpg --batch --yes --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -fsSL https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt >/etc/apt/sources.list.d/caddy-stable.list
  apt-get update -q && apt-get install -y -q caddy >/dev/null
fi
install_uv

# ── service user and directories ─────────────────────────────────────────────
id viddown >/dev/null 2>&1 ||
  useradd --system --home-dir /var/lib/viddown --no-create-home --shell /usr/sbin/nologin viddown
install -d -m 0755 "$ROOT" "$ROOT/slots"
install -d -m 0750 -o root -g viddown "$ETC"
install -d -m 0750 -o viddown -g viddown "$STATE" /var/cache/viddown
install -d -m 0700 /var/cache/viddown-uv

# Service environment: generated once, then owned by you (edit + `systemctl restart viddown`).
if [[ ! -f $ETC/viddown.env ]]; then
  log "writing $ETC/viddown.env with a fresh secret"
  sed -e "s|^VIDDOWN_SECRET=.*|VIDDOWN_SECRET=$(head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n')|" \
      -e "s|^VIDDOWN_PUBLIC_BASE_URL=.*|VIDDOWN_PUBLIC_BASE_URL=https://$VIDDOWN_DOMAIN|" \
      -e "s|^VIDDOWN_NTFY_TOPIC=.*|VIDDOWN_NTFY_TOPIC=${NTFY_TOPIC:-}|" \
      -e "s|^VIDDOWN_MANIFEST_BASE=.*|VIDDOWN_MANIFEST_BASE=$ETC/manifest.json|" \
      "$SERVER_DIR/.env.example" | grep -v '^#' | grep -v '^$' >"$ETC/viddown.env"
fi
chown root:viddown "$ETC/viddown.env" && chmod 0640 "$ETC/viddown.env"
[[ -f $ETC/manifest.json ]] || install -m 0644 "$HERE/manifest.example.json" "$ETC/manifest.json"
printf 'VIDDOWN_DOMAIN=%s\nACME_EMAIL=%s\n' "$VIDDOWN_DOMAIN" "$ACME_EMAIL" >"$ETC/caddy.env"
chmod 0644 "$ETC/caddy.env"
chmod 0600 "$DEPLOY_ENV"

# ── firewall: the Oracle Ubuntu gotcha ───────────────────────────────────────
# Oracle's Ubuntu images ship iptables rules ending in a blanket REJECT. Opening
# ports in the VCN security list is NOT enough: they must be accepted here too,
# *before* that REJECT rule, and saved so they survive reboots.
open_port() {  # proto port
  iptables -C INPUT -p "$1" --dport "$2" -m state --state NEW -j ACCEPT 2>/dev/null && return 0
  local pos
  pos=$(iptables -L INPUT --line-numbers -n | awk '$2 == "REJECT" {print $1; exit}')
  if [[ -n $pos ]]; then
    iptables -I INPUT "$pos" -p "$1" --dport "$2" -m state --state NEW -j ACCEPT
  else
    iptables -A INPUT -p "$1" --dport "$2" -m state --state NEW -j ACCEPT
  fi
  log "iptables: opened $1/$2"
}
open_port tcp 80
open_port tcp 443
open_port udp 443  # HTTP/3
netfilter-persistent save >/dev/null

# ── unattended security upgrades, reboot window 05:00 (after the 04:00 update) ─
cat >/etc/apt/apt.conf.d/20auto-upgrades <<'EOF'
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
EOF
cat >/etc/apt/apt.conf.d/52viddown-unattended-upgrades <<'EOF'
Unattended-Upgrade::Automatic-Reboot "true";
Unattended-Upgrade::Automatic-Reboot-Time "05:00";
Unattended-Upgrade::Remove-Unused-Kernel-Packages "true";
Unattended-Upgrade::Remove-Unused-Dependencies "true";
EOF

# ── systemd units, Caddy ─────────────────────────────────────────────────────
log "installing systemd units and Caddyfile"
install -m 0644 "$HERE"/systemd/*.service "$HERE"/systemd/*.timer /etc/systemd/system/
install -d /etc/systemd/system/viddown.service.d /etc/systemd/system/caddy.service.d
# CPU quota sized to this machine: leave ~10% of total CPU for sshd/Caddy/the OS.
printf '[Service]\nCPUQuota=%d%%\n' "$(($(nproc) * 90))" >/etc/systemd/system/viddown.service.d/10-cpu.conf
install -m 0644 "$HERE/systemd/caddy-viddown.conf" /etc/systemd/system/caddy.service.d/viddown.conf
install -m 0644 "$HERE/Caddyfile" /etc/caddy/Caddyfile
systemctl daemon-reload

# ── first slot (code + venv + ffmpeg + deno), smoke-tested, then live ────────
if [[ ! -e $ROOT/current ]]; then
  log "bootstrapping the first slot (SOURCE=$SOURCE)"
  if [[ $SOURCE == git ]]; then
    "$HERE/update.sh" --bootstrap --from-dir "$(dirname "$SERVER_DIR")"
  else
    "$HERE/update.sh" --bootstrap
  fi
fi

systemctl enable --now viddown.service viddown-update.timer
[[ -n ${DUCKDNS_TOKEN:-} ]] && systemctl enable --now duckdns.timer && systemctl start duckdns.service
[[ -n ${BACKUP_PAR_URL:-} ]] && systemctl enable --now viddown-backup.timer
systemctl enable caddy >/dev/null && systemctl restart caddy

log "done. Check: curl -s https://$VIDDOWN_DOMAIN/v1/manifest | jq"
log "logs: journalctl -u viddown -f    updates: journalctl -u viddown-update"
