# viddown server

The viddown apps download **on your own device**. This server is a small
**coordinator plus fallback** that runs for $0 on Oracle Cloud's Always-Free tier:

| Endpoint | What it does |
|---|---|
| `GET /v1/manifest` | Latest app versions, the **yt-dlp / ffmpeg versions this server validated today**, notices, kill switches, and whether cloud mode is on. |
| `POST /v1/probe` | Title, thumbnail and formats for a link (allow-listed sites only), plus signed stream links. |
| `GET /v1/stream/{token}` | Cloud-fallback download, streamed straight from yt-dlp through ffmpeg to you. **Nothing is written to disk.** |
| `POST /v1/contact` | "Hire me" form: stored in SQLite and pushed to your phone via ntfy.sh. |
| `POST /v1/ping` | Anonymous counters (installs, downloads). **No IP address is ever stored.** |
| `GET /v1/stats` | Public totals for the website badge. |
| `GET /healthz` | Liveness. From the server itself it also shows the budget and load. |

This folder is a self-contained deploy unit: copy `server/` (or the release
tarball) to a machine and it has everything it needs.

---

## Contents

1. [How it works](#how-it-works)
2. [Set up Oracle Always-Free, step by step](#set-up-oracle-always-free-step-by-step)
3. [How automatic updates work](#how-automatic-updates-work)
4. [Day-to-day: logs, status, backups](#day-to-day-logs-status-backups)
5. [Development and tests](#development-and-tests)
6. [Load testing](#load-testing)
7. [Configuration reference](#configuration-reference)

---

## How it works

### Zero-disk streaming

```
yt-dlp -f <video> -o - ──► /run/viddown/job-x/in0.fifo ─┐  (named pipes in RAM)
yt-dlp -f <audio> -o - ──► /run/viddown/job-x/in1.fifo ─┼─► ffmpeg -c copy -f mp4 (fragmented) ─► you
```

* ffmpeg only **remuxes** (`-c copy`), so a stream costs little CPU and roughly
  60–150 MB of RAM.
* If you read slowly, the 64 KB pipes fill up and everything upstream simply
  waits, so memory stays flat no matter how slow the client is.
* Disconnect, 60 s without progress, 2 GB or 3 hours: every process of the job
  is killed (each runs in its own process group) and the job directory is deleted.
* Clips use yt-dlp's `--download-sections`, so only the requested part is
  fetched. "Fast" clips cut on keyframes. "Precise" clips re-encode and are
  admitted as the more expensive *transcode* class.
* Audio comes out as MP3.

### Admission control (`viddown_server/resources.py`)

At start-up the service reads RAM (`/proc/meminfo`), its cgroup limits
(`memory.high`/`memory.max`, `cpu.max`) and the CPU count, then sizes itself:

```
job budget = min(RAM − 1500 MB reserved for the OS/Caddy/SSH, cgroup memory.high) − 200 MB
extract    = CPUs                       (pre-warmed yt-dlp worker processes)
transcode  = CPUs − 1
stream     = min(memory left ÷ 180 MB, CPUs × 15, 40)
```

| VM shape | extract workers | stream slots | transcode slots | what limits streams |
|---|---|---|---|---|
| 2 OCPU / 12 GB | 2 | 30 | 1 | CPU (2 × 15) |
| 4 OCPU / 24 GB | 4 | 40 | 3 | network cap (40) |

(With `MemoryHigh=80%` the memory budget is about 9.3 GB or 18.9 GB. That is
far more than 30–40 streams need, so memory is never the first limit.)

When a class is full, up to 32 requests wait for up to 20 s. Anything beyond
that gets `503` with a `Retry-After` header, and the app quietly uses its local
engine instead. Per client IP there is a token bucket (20 probes/minute) and a
maximum of 2 concurrent streams. Every 2 s a guard reads memory pressure
(`/proc/pressure/memory`, `MemAvailable`). Under pressure it stops admitting new
work. When memory is critical it cancels the newest transcode job first, then
the newest stream.

**Monthly egress budget.** Oracle's Always-Free tier includes 10 TB of outbound
data per month (verify the current figure in your console). The server counts
the bytes it streams. At 8 TB it sets `cloud.enabled=false` in the manifest and
refuses streams until the next month, so the free allowance is never exceeded.

### Security

* **SSRF guard:** only `http`/`https` URLs. The host is resolved and every
  address must be public (no private, loopback, link-local, CGNAT or metadata
  addresses).
* **Site allow-list:** yt-dlp may only use the listed extractors (YouTube,
  Instagram, TikTok, X/Twitter, Reddit, Vimeo, …). The generic "any web page"
  extractor is always disabled.
* **Signed stream links:** `/probe` returns HMAC tokens bound to the URL,
  format, clip, expiry (10 min) and your IP. The IP is stored only as a keyed
  hash inside the token.
* **Least privilege:** the service runs as the unprivileged `viddown` user with
  systemd sandboxing (`ProtectSystem=strict`, `NoNewPrivileges`, `PrivateTmp`, …),
  behind Caddy with automatic HTTPS. Caddy writes no access logs, so client IPs
  never reach disk.

---

## Set up Oracle Always-Free, step by step

You need about 45 minutes, an e-mail address and a payment card. Oracle uses the
card to verify your identity. An Always-Free account **cannot be charged**.

### 1. Create the account

1. Go to <https://www.oracle.com/cloud/free/> and click **Start for free**.
2. **Home region:** pick it carefully, because you **cannot change it later**,
   and Always-Free Arm machines can only be created in your home region. Choose
   one near your users. Some popular regions often have no free Arm capacity; a
   less busy region nearby is often easier.
3. Finish sign-up. Oracle may place a small temporary authorization on the card.
4. **Stay on the "Always Free" account type.** Do **not** click "Upgrade to Pay
   As You Go" unless you have decided you want to. Pay-As-You-Go removes the
   idle-reclaim risk below, but it means a card that can be charged. If you
   upgrade later, set a budget alert at $1 first (Billing → Budgets).

> **Idle reclaim:** Oracle may reclaim Always-Free instances that look idle
> (very low CPU, network and memory use over 7 days; check Oracle's current
> wording). That is why everything here is reproducible: cloud-init rebuilds the
> whole machine in about 10 minutes, and the database is backed up nightly.

### 2. Create a network that lets web traffic in

1. Console menu → **Networking → Virtual cloud networks → Start VCN Wizard →
   "Create VCN with Internet Connectivity"**. Accept the defaults.
2. Open the new VCN → **Security Lists → Default Security List → Add Ingress Rules**,
   with **Source CIDR** `0.0.0.0/0` for each of these:
   * TCP, destination port `80` (needed for the HTTPS certificate)
   * TCP, destination port `443`
   * UDP, destination port `443` (HTTP/3, optional)
   Port 22 (SSH) is already open by default.

> **The Oracle Ubuntu gotcha:** Oracle's Ubuntu image *also* has its own firewall
> (iptables) that rejects everything except SSH. Opening ports in the security
> list alone is **not enough**. `install.sh` handles this for you by inserting
> ACCEPT rules before the REJECT rule and saving them. If you ever set up a
> machine by hand, remember both places.

### 3. Get a free domain name (DuckDNS)

1. Sign in at <https://www.duckdns.org> (GitHub or Google login).
2. Create a sub-domain, for example `myviddown` → `myviddown.duckdns.org`.
3. Copy your **token** from the top of the page.

You'll fill in the IP after the machine exists. The machine also updates DuckDNS
itself every 30 minutes.

### 4. Create the machine

1. Console menu → **Compute → Instances → Create instance**.
2. **Image:** *Change image* → **Canonical Ubuntu 24.04**. The aarch64 build is
   chosen automatically with an Arm shape.
3. **Shape:** *Change shape* → **Ampere** → `VM.Standard.A1.Flex`. Pick
   **2 OCPU / 12 GB** or **4 OCPU / 24 GB**. Check the current Always-Free limits
   in your console; the free allowance is shared by all your A1 machines. The
   server sizes itself, so either works.
4. **Networking:** select the VCN from step 2 and its *public* subnet. Make sure
   **Assign a public IPv4 address** is on.
5. **SSH keys:** upload your public key, or let Oracle generate a pair and
   **download the private key now**. You can't download it later.
6. **Boot volume:** the default (~47 GB) is fine. The Always-Free total for
   block storage is about 200 GB; verify in console.
7. **Show advanced options → Management → Paste cloud-init script:** paste
   [`deploy/cloud-init.yaml`](deploy/cloud-init.yaml) and **edit the
   `/etc/viddown/deploy.env` block first**:
   * `VIDDOWN_DOMAIN`: your `name.duckdns.org`
   * `DUCKDNS_TOKEN`: the token from step 3
   * `ACME_EMAIL`: your e-mail (Let's Encrypt expiry notices)
   * `NTFY_TOPIC`: a long random name such as `viddown-k3j9x2q8w7`. Install the
     **ntfy** app on your phone and subscribe to the same topic to get contact
     messages and update alerts.
8. Click **Create**.

> **"Out of host capacity"?** This is common for free Arm machines. It isn't
> an error on your side. Try again later (early morning in your region often
> works), try another *availability domain* in the same dialog, or start with
> 1 OCPU / 6 GB and resize later (Instance → Edit → shape). Some people retry
> every few minutes for a day or two.

### 5. Point DuckDNS at the machine

Copy the instance's **Public IP address** from its details page. Paste it into
the `current ip` box on duckdns.org and click **update ip**. Within a minute
`ping myviddown.duckdns.org` should show that IP.

### 6. Wait for the first deploy and check it

cloud-init installs everything: Caddy, uv, Python packages, ffmpeg, deno, the
systemd units and the firewall rules. It then builds the first slot and
smoke-tests it. This takes about 10 minutes. To watch:

```bash
ssh -i your-key ubuntu@<public-ip>
sudo tail -f /var/log/cloud-init-output.log      # Ctrl+C to stop watching
```

When it prints `viddown cloud-init finished`:

```bash
curl -s https://myviddown.duckdns.org/v1/manifest | jq
curl -s http://127.0.0.1:8000/healthz | jq        # on the server: budget + load
```

The first HTTPS request can take ~30 s while Caddy gets the certificate. If it
fails, check that ports 80/443 are open in the security list (step 2) and that
DuckDNS points at the right IP (step 5).

**Without cloud-init** (an existing machine): create `/etc/viddown/deploy.env`
with the same values, then
`git clone https://github.com/786RY9/viddown /opt/viddown/src && sudo bash /opt/viddown/src/server/deploy/install.sh`.

### 7. Optional: nightly database backups (free Object Storage)

1. Console → **Storage → Buckets → Create bucket**, named `viddown-backups`
   (Standard tier, private).
2. Bucket → **Pre-Authenticated Requests → Create**: target *Bucket*, access
   **Permit object writes**, and an expiry far in the future. Copy the URL. It
   ends in `/o/`.
3. Bucket → **Lifecycle Policy Rules → Create**: delete objects older than 30 days.
4. On the server: `sudo nano /etc/viddown/deploy.env`, set `BACKUP_PAR_URL=<url>`, then
   `sudo systemctl enable --now viddown-backup.timer`. Test it now:
   `sudo systemctl start viddown-backup && journalctl -u viddown-backup -n 5`.

The upload URL can only *write* objects, so the server holds no credentials that
could read or delete your backups. To restore, download the newest `.db.gz` in
the console, then
`gunzip` → `sudo systemctl stop viddown` → copy it to `/var/lib/viddown/viddown.db`
→ `sudo chown viddown: /var/lib/viddown/viddown.db` → `sudo systemctl start viddown`.

---

## How automatic updates work

Sites change all the time, so yt-dlp needs frequent updates. The server updates
itself **every day at 04:00 UTC** (`viddown-update.timer`) with a blue/green
scheme, so a bad update never goes live:

```
/opt/viddown/slots/a/   app/ (server code) venv/ (Python + yt-dlp) bin/ (ffmpeg, ffprobe, deno)
/opt/viddown/slots/b/   ← the idle slot is rebuilt here
/opt/viddown/current -> slots/a        (what systemd runs)
```

1. **Server code:** if a newer GitHub Release tagged `server-v*` exists, download
   it and check its `.sha256`. Otherwise copy the live code. With `SOURCE=git`
   it builds from the `GIT_REF` branch instead; the version in
   `viddown_server/__init__.py` must go up for a change to deploy.
2. **yt-dlp:** a fresh venv with the **latest** yt-dlp.
3. **ffmpeg:** the latest `yt-dlp/FFmpeg-Builds` linuxarm64 build, checked
   against its published SHA-256 (downloaded only when the build changed).
4. **deno:** yt-dlp needs a JavaScript runtime for YouTube. Also checked
   against its published SHA-256.
5. **Smoke test** (`deploy/smoke.sh`): starts the new slot on a spare port,
   probes Creative-Commons test videos (Blender's *Big Buck Bunny* on YouTube and
   Vimeo by default), streams 5 s of each, and checks for a valid MP4 and no
   leftover job files.
6. **Pass:** flip `current` to the new slot. The validated versions go into
   `/var/lib/viddown/validated.json`, which `/v1/manifest` serves, so **the apps
   pin their own yt-dlp to the version the server just proved works**. Then the
   service restarts gracefully: it stops taking new work and lets running
   streams finish (up to 5 min). If the new slot fails its health check, it
   rolls back automatically.
   **Fail:** the live slot keeps running and you get an ntfy alert with the log.

If nothing changed, the timer finishes without restarting anything. Ubuntu
security patches come from `unattended-upgrades`, which reboots at 05:00 if
needed.

Run an update by hand: `sudo systemctl start viddown-update && journalctl -u viddown-update -f`.
Force a rebuild: `sudo /opt/viddown/current/app/deploy/update.sh --force`.
Roll back by hand: `sudo ln -sfn slots/b /opt/viddown/current && sudo systemctl restart viddown`
(use whichever slot is *not* current).

> **YouTube and datacenter IPs:** YouTube often blocks cloud IP ranges ("Sign in
> to confirm you're not a bot"). That's expected, and it's why the apps download
> on-device first. The smoke test passes if **at least one** test URL works
> (`SMOKE_MIN_PASS=1`), so a YouTube block alone doesn't freeze updates.

---

## Day-to-day: logs, status, backups

```bash
journalctl -u viddown -f                    # live service log (no client IPs are logged)
journalctl -u viddown-update --since today  # what the 04:00 update did
systemctl list-timers 'viddown*' 'duckdns*' # when things run next
curl -s http://127.0.0.1:8000/healthz | jq  # budget, active/queued jobs, memory, egress this month
cat /var/lib/viddown/validated.json         # versions currently live
sudo systemctl restart viddown              # graceful restart (drains streams first)
sudo sqlite3 /var/lib/viddown/viddown.db 'select datetime(created_at,"unixepoch"),name,email,message from contact order by id desc limit 10'
```

* **Turn cloud mode off** (manifest `cloud.enabled=false`): set
  `"killSwitches": {"cloud": true}` in `/etc/viddown/manifest.json`. This takes
  effect immediately, with no restart.
* **Show a notice in the apps:** add to `"notices"` in the same file.
* **Change limits:** edit `/etc/viddown/viddown.env` (see `.env.example`), then
  `sudo systemctl restart viddown`.

---

## Development and tests

```bash
cd server
python -m venv .venv && .venv/bin/pip install -r requirements.txt pytest pytest-asyncio httpx   # Windows: .venv\Scripts\pip
python scripts/vendor_core.py      # copy ../cli/viddown_core into server/ (needed for bundles/images;
                                   # in a checkout the code falls back to ../cli automatically)
.venv/bin/python -m pytest         # unit + API tests: run on Windows/macOS/Linux
```

The `/proc` and cgroup readings are faked in the tests, so admission sizing,
the pressure guard, rate limiting, the SSRF guard, token signing,
single-flight/LRU and the egress cutoff are all tested on any OS.

The streaming tests need Linux (named pipes and process groups), so they run
in Docker:

```bash
python scripts/vendor_core.py
docker build -f Dockerfile.test -t viddown-server-test .
docker run --rm viddown-server-test                                # offline: local DASH fixtures
docker run --rm -e VIDDOWN_NETWORK_TESTS=1 viddown-server-test     # + real Big Buck Bunny from YouTube
```

The offline tests generate test videos with ffmpeg and serve them locally.
They cover full video (two FIFOs), MP3, fast and precise clips, client
disconnect, a stalled reader, the byte cap and saturation (503). After each
test they check that no `ffmpeg`/`yt-dlp` process and no job directory is left.

Run the server locally (Linux/macOS/WSL for streaming; Windows can run
everything except `/v1/stream`):

```bash
VIDDOWN_SECRET=dev-secret-dev-secret-dev-secret .venv/bin/python -m viddown_server
```

**Release bundle:** `python scripts/build_bundle.py` writes
`dist/viddown-server-<version>.tar.gz` and its `.sha256`. CI uploads both to a
GitHub Release tagged `server-v<version>`, which `update.sh` installs.

---

## Load testing

`loadtest/loadtest.py` runs many virtual clients that probe and stream. Some read
slowly (backpressure and the stall timer) and some hang up early (cleanup). On
`127.0.0.1` each virtual client sends its own `X-Forwarded-For`, so per-IP limits
apply per virtual client.

**Self-contained, in Docker** (no internet; a tiny capacity so saturation is reached):

```bash
docker run --rm --memory 2g --cpus 2 viddown-server-test bash loadtest/docker-loadtest.sh
```

It passes when overload returns only `503` with `Retry-After` (never 500s or
hangs), `/healthz` shows 0 pipelines afterwards, no `ffmpeg`/`yt-dlp` is left
running, no job directory remains (zero-disk), and the state directory only
grew by SQLite bookkeeping. It also prints the peak RSS of the server and all
its child processes.

**Against the real server** (from the server itself, so `/healthz` shows detail):

```bash
/opt/viddown/current/venv/bin/pip install httpx   # one-off, in a throwaway venv preferably
python loadtest/loadtest.py --base http://127.0.0.1:8000 \
  --url https://vimeo.com/1084537 --clients 40 --duration 120
pgrep -fa 'ffmpeg|yt_dlp'; ls /run/viddown       # afterwards: both should be empty
```

Mind the egress: 40 clients streaming 480p for 2 minutes is a few GB.

---

## Configuration reference

All settings are environment variables with safe defaults. Each one is
documented in [`.env.example`](.env.example). The deploy scripts read
`/etc/viddown/deploy.env` (see [`deploy/cloud-init.yaml`](deploy/cloud-init.yaml)).

| File on the server | Purpose |
|---|---|
| `/etc/viddown/viddown.env` | Service settings, including the token secret (generated at install) |
| `/etc/viddown/deploy.env` | Domain, DuckDNS token, ntfy topic, update source, backup URL, smoke URLs |
| `/etc/viddown/manifest.json` | App versions, notices and kill switches served in `/v1/manifest` |
| `/var/lib/viddown/viddown.db` | SQLite: contact messages, daily counters, egress totals |
| `/var/lib/viddown/validated.json` | Versions proven by the last successful update |
| `/run/viddown/` | Per-stream FIFOs (tmpfs, RAM only) |
