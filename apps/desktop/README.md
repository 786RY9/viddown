# viddown desktop (Tauri 2)

The desktop app is a thin Tauri 2 shell around the Expo app's **web export**
(`apps/mobile`, rendered with react-native-web). All UI lives in
`apps/mobile`. This package contains only the native side:

- `src-tauri/`: the Rust shell. It handles process management, binaries, the updater, dialogs and reveal-in-folder.
- `scripts/fetch-sidecars.mjs`: downloads and verifies the ffmpeg/ffprobe binaries bundled with the app.
- The frontend engine is `apps/mobile/src/engine/tauri.ts` (`TauriEngine`).

```
 Expo web UI (apps/mobile/dist or http://localhost:8081)
   │  TauriEngine → window.__TAURI__.core.invoke(...)
   ▼
 Rust commands ── spawn ──► yt-dlp  (app data dir, self-updating)
   ▲  Channel<JobEvent>          └─► viddown-ffmpeg / viddown-ffprobe (bundled sidecars)
   └── every stdout/stderr line, then {type:"exit"}
```

## Commands

| Script | What it does |
|---|---|
| `pnpm --filter @viddown/desktop dev` | Fetches sidecars, then `tauri dev`. Tauri starts the Expo dev server (`expo start --port 8081` in `apps/mobile`) and opens the window on it with hot reload. |
| `pnpm --filter @viddown/desktop build` | Fetches sidecars, runs `expo export --platform web` (→ `apps/mobile/dist`), then `tauri build` (NSIS `.exe` + `.msi` / `.dmg` + `.app` / `.deb` + `.AppImage` + `.rpm`). |
| `pnpm --filter @viddown/desktop build:release` | Same as `build`, plus signed updater artifacts and `latest.json` inputs (see [Updater](#updater)). Needs the signing key. |
| `pnpm --filter @viddown/desktop fetch-sidecars` | Downloads ffmpeg + ffprobe for the host (or `--target <triple>`). Add `--force` to refetch. |
| `pnpm --filter @viddown/desktop check` | `cargo check`. |
| `pnpm --filter @viddown/desktop test:rust` | Rust unit tests (argument allow-listing, checksum parsing). |

### Prerequisites

- Node 20+ and pnpm.
- Rust stable (`rustup`, MSVC toolchain on Windows).
- **Windows:** Visual Studio 2022+ Build Tools with "Desktop development with C++" and a Windows 10/11 SDK. WebView2 is preinstalled on Windows 10 (since 2021) and 11. The installer bootstraps it if it is missing.
- **macOS:** Xcode Command Line Tools.
- **Linux:** the packages in the Tauri prerequisites guide, e.g. `libwebkit2gtk-4.1-dev build-essential curl wget file libxdo-dev libssl-dev libayatana-appindicator3-dev librsvg2-dev`.

## Engine binaries

### yt-dlp (downloaded at runtime, self-updating)

yt-dlp is **not** bundled. On the first probe or download, the app does the following:

1. Downloads the official standalone build for the OS/arch from `github.com/yt-dlp/yt-dlp/releases/latest`:
   - `yt-dlp.exe` or `yt-dlp_arm64.exe` on Windows
   - `yt-dlp_macos` (universal) on macOS
   - `yt-dlp_linux` or `yt-dlp_linux_aarch64` on Linux
2. Verifies it against that release's `SHA2-256SUMS`.
3. Installs it atomically to `<app local data>/bin/yt-dlp[.exe]`. On Windows that is `%LOCALAPPDATA%\dev.ry9.viddown\bin\yt-dlp.exe`.

Because the binary sits in a writable location, `DownloadEngine.selfUpdate(version)` can run `yt-dlp --update-to stable@<version>`. The version comes from the server manifest (milestone 6), so clients pin to the version the server just validated. yt-dlp verifies its own update. The Rust side refuses to update while downloads are running, so a running job never sees a half-replaced binary.

### ffmpeg + ffprobe (bundled sidecars)

ffmpeg and ffprobe are bundled as Tauri `externalBin` sidecars named
**`viddown-ffmpeg` / `viddown-ffprobe`**. The prefix stops the Linux `.deb`,
which installs sidecars into `/usr/bin`, from colliding with the distro's
ffmpeg package. Rust passes `--ffmpeg-location <exe dir>/viddown-ffmpeg[.exe]`, and
yt-dlp then finds `viddown-ffprobe` next to it. ffmpeg updates ship with app
releases.

`scripts/fetch-sidecars.mjs` writes them to `src-tauri/binaries/` with Tauri's
target-triple suffix, for example `viddown-ffmpeg-x86_64-pc-windows-msvc.exe`.

| Target | Source | Verification |
|---|---|---|
| Windows x64 / arm64 | [yt-dlp/FFmpeg-Builds](https://github.com/yt-dlp/FFmpeg-Builds) `win64-gpl-shared` / `winarm64-gpl-shared` | release `checksums.sha256` |
| Linux x64 / arm64 | yt-dlp/FFmpeg-Builds `linux64-gpl` / `linuxarm64-gpl` (static) | release `checksums.sha256` |
| macOS arm64 / x64 | [eugeneware/ffmpeg-static](https://github.com/eugeneware/ffmpeg-static) release `b6.1.1`: static GPL builds (x64 = evermeet.cx FFmpeg 8.0) | SHA-256s pinned in the script (the project publishes no checksum file) |

- Windows uses the **shared** build. `ffmpeg.exe` and `ffprobe.exe` are under 1 MB each and share one set of DLLs (about 185 MB unpacked, about 50 MB in the LZMA-compressed NSIS installer). Two static exes would be roughly twice that. The DLLs are bundled through `tauri.windows.conf.json` → `bundle.resources` with target `""`, which on Windows is the install dir, next to the exes.
- FFmpeg-Builds defaults to the rolling `latest` release. For reproducible CI, pin a dated release with `FFMPEG_BUILDS_TAG=autobuild-YYYY-MM-DD-HH-MM`.
- These are GPL builds, which libx264 needs for `--force-keyframes-at-cuts` clip re-encoding. `FFMPEG-LICENSE.txt` is shipped in the bundle resources.
- To refresh the macOS pins, download the new `ffmpeg-darwin-*.gz` / `ffprobe-darwin-*.gz`, run `sha256sum`, and update `MAC_PINNED`.

**Installer size** (measured on Windows x64):

| Artifact | Size |
|---|---|
| `viddown.exe` (Rust shell, release profile with LTO, opt-level `s` and stripped symbols) | 6.7 MB |
| NSIS `viddown_0.1.0_x64-setup.exe` | 57 MB |
| MSI | 77 MB |

ffmpeg dominates the installer. If size ever matters more than offline-ready installs, ffmpeg could be downloaded on first run the same way as yt-dlp.

**Temp files.** yt-dlp is a PyInstaller one-file build that unpacks itself to `$TEMP/_MEI*`. A hard kill on cancel skips its cleanup. To contain this, every engine process gets `TMP`/`TEMP`/`TMPDIR` set to `<app local data>/tmp`, and that directory is wiped at startup, before any engine process exists.

**JavaScript runtime (open item).** Current yt-dlp warns that YouTube extraction without a JS runtime is deprecated ("only deno is enabled by default"). Extraction still works today; the Big Buck Bunny probe returned 53 formats up to 2160p. Long term, the options are:
- bundle a small runtime (QuickJS) or deno;
- pass `--js-runtimes node` when Node is present.

## Rust commands

All commands are app commands invoked by `TauriEngine`. Plugin permissions are not
exposed to the webview: dialog, opener and updater are called from Rust.

| Command | Purpose |
|---|---|
| `probe { id, args }` | Runs yt-dlp with `probeArgs(url)` from `@viddown/core` and returns the raw JSON string. Cancel with `cancel_job(id)`. |
| `start_job { id, args, onEvent: Channel }` | Spawns yt-dlp with `jobArgs(spec)`. It streams `{type:"line", stream, line}` for every stdout/stderr line, then `{type:"exit", code, canceled}`. |
| `cancel_job { id }` | Kills the whole process tree. Partial `.part` / `.ytdl` files are kept so `--continue` resumes. |
| `versions` | `{ ytdlp, ffmpeg }` versions (`null` if yt-dlp isn't installed yet). |
| `self_update { version? }` | Installs yt-dlp if missing, else runs `yt-dlp --update-to stable[@version]`. |
| `default_output_dir` | `~/Downloads/viddown` (created on demand). |
| `reveal { path }` | Opens Explorer, Finder or the file manager with the file selected. |
| `pick_folder { start? }` | Native folder dialog. Returns `null` on cancel. |
| `open_url { url }` | Opens an http(s)/mailto link in the browser. |
| `engine_info` | Binary paths and limits, for diagnostics. |
| `app_update_check` / `app_update_install` | Tauri updater. |

**Safety rails.**
- Argument lists are validated before spawn. They must end in `-- <url>`, and options that execute commands or load code, configs or files are rejected (`--exec`, `--use-postprocessor`, `--netrc-cmd`, `--config-location`, `--batch-file`, `--downloader`, `--update*`, `--ffmpeg-location`, short-option clusters and others).
- Every run gets `--ignore-config --no-plugin-dirs`, so a user's own yt-dlp config cannot change the output format the parser relies on.

**Concurrency.** The TS `DownloadQueue` decides how many jobs run (3 by default on desktop). Rust adds hard caps of **6 jobs** and **4 probes**. Jobs beyond the cap wait for a slot and can be canceled while waiting.

**Killing process trees.**
- On Windows, each yt-dlp process is assigned to a Job Object with `KILL_ON_JOB_CLOSE`. Cancel calls `TerminateJobObject`, which kills the PyInstaller bootloader, Python and ffmpeg together. If viddown itself crashes, the OS kills the tree. `taskkill /T /F` is the fallback.
- On macOS and Linux, each child leads its own process group. Cancel sends `SIGTERM`, then `SIGKILL` 2 s later, and all groups are killed on app exit.

## Wiring the engine in the UI

`apps/mobile/src/engine/tauri.ts` exports `TauriEngine` and `isTauri()`. It
uses the global `window.__TAURI__` (`app.withGlobalTauri: true`), so the Expo
app needs no `@tauri-apps/*` dependency:

```ts
import { isTauri, TauriEngine } from "./tauri";
engine ??= isTauri() ? new TauriEngine() : new MockEngine();
```

`TauriEngine` also offers desktop-only extras: `pickFolder()`, `openUrl()`,
`engineInfo()`, `checkAppUpdate()` and `installAppUpdate()`.

## Updater

The app checks a signed `latest.json` on GitHub Releases. Before the first release:

1. **Set the repo.** In `src-tauri/tauri.conf.json` → `plugins.updater.endpoints`, replace `OWNER/REPO`. This is the only place the repo is named.
2. **Generate the signing key** (free, minisign-based):
   ```sh
   pnpm --filter @viddown/desktop tauri signer generate -w ~/.tauri/viddown.key
   ```
   Paste the contents of `~/.tauri/viddown.key.pub` into `plugins.updater.pubkey` (replace `REPLACE_WITH_CONTENTS_OF_viddown.key.pub`).
   **Back up the private key.** If you lose it, existing installs can never update again.
3. **Add the CI secrets** `TAURI_SIGNING_PRIVATE_KEY` (key content or path) and `TAURI_SIGNING_PRIVATE_KEY_PASSWORD`. `.env` files are *not* read for these.
4. **Build releases** with `build:release`. It merges `tauri.release.conf.json`, which sets `bundle.createUpdaterArtifacts: true`. Local `build` leaves it off, so you can build without the key.
5. **Publish.** `tauri-apps/tauri-action` uploads the bundles, the `.sig` files and `latest.json` to the GitHub release. `releases/latest/download/latest.json` always points at the newest release.

On Windows the update installs in `passive` mode, showing a progress bar with no prompts. The NSIS installer is per-user (`currentUser`), so no UAC prompt appears either.

## Code signing

- **Windows:** unsigned until SignPath Foundation (free for OSS) is set up. Until then SmartScreen shows "More info → Run anyway".
- **macOS:** ad-hoc signed. Users right-click → Open on first launch.
