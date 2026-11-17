#!/usr/bin/env node
/**
 * Downloads the ffmpeg + ffprobe binaries bundled with the desktop app and
 * places them where Tauri expects sidecars:
 *
 *   src-tauri/binaries/viddown-ffmpeg-<target-triple>[.exe]
 *   src-tauri/binaries/viddown-ffprobe-<target-triple>[.exe]
 *   src-tauri/binaries/ffmpeg-dlls/*.dll          (Windows only, shared build)
 *   src-tauri/binaries/FFMPEG-LICENSE.txt
 *
 * The sidecars are prefixed "viddown-" so the Linux .deb (which installs
 * sidecars into /usr/bin) never collides with the distro's own ffmpeg package.
 * yt-dlp still finds both: `--ffmpeg-location .../viddown-ffmpeg` makes it
 * look for `viddown-ffprobe` next to it.
 *
 * Sources (every download is SHA-256 verified before use):
 *   Windows / Linux: https://github.com/yt-dlp/FFmpeg-Builds  (GPL builds,
 *     verified against that release's checksums.sha256). Windows uses the
 *     "shared" variant: ffmpeg.exe and ffprobe.exe share one set of DLLs,
 *     which roughly halves the installer compared with two static exes.
 *   macOS: https://github.com/eugeneware/ffmpeg-static release b6.1.1 (static
 *     GPL builds; the x64 binaries are evermeet.cx FFmpeg builds). That
 *     project publishes no checksum file, so the SHA-256s are pinned below.
 *
 * Usage:
 *   node scripts/fetch-sidecars.mjs [--target <triple>] [--force]
 *   FFMPEG_BUILDS_TAG=autobuild-2026-11-16-23-35 node scripts/fetch-sidecars.mjs
 *
 * The target defaults to $TAURI_ENV_TARGET_TRIPLE, then `rustc --print host-tuple`.
 */
import { createHash } from "node:crypto";
import { execFileSync } from "node:child_process";
import { createReadStream, createWriteStream, existsSync } from "node:fs";
import { chmod, copyFile, mkdir, readFile, readdir, rm, writeFile } from "node:fs/promises";
import { dirname, join, resolve } from "node:path";
import { Readable } from "node:stream";
import { pipeline } from "node:stream/promises";
import { fileURLToPath } from "node:url";
import { createGunzip } from "node:zlib";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const BIN_DIR = join(ROOT, "src-tauri", "binaries");
const DLL_DIR = join(BIN_DIR, "ffmpeg-dlls");
const STAMP = join(BIN_DIR, ".sidecars.json");

const FFMPEG_BUILDS = "https://github.com/yt-dlp/FFmpeg-Builds/releases/download";
const FFMPEG_BUILDS_TAG = process.env.FFMPEG_BUILDS_TAG || "latest";

const MAC_RELEASE = "https://github.com/eugeneware/ffmpeg-static/releases/download/b6.1.1";
const MAC_PINNED = {
  "aarch64-apple-darwin": {
    ffmpeg: ["ffmpeg-darwin-arm64.gz", "8923876afa8db5585022d7860ec7e589af192f441c56793971276d450ed3bbfa"],
    ffprobe: ["ffprobe-darwin-arm64.gz", "d986a8ec7b030899fe66a8a288ed809a3543338705a3ce178cfb85869c5d80be"],
    license: "darwin-arm64.LICENSE",
  },
  "x86_64-apple-darwin": {
    ffmpeg: ["ffmpeg-darwin-x64.gz", "929b375c1182d956c51f7ac25e0b2b0411fb01f6f407aa15c9758efeb4242106"],
    ffprobe: ["ffprobe-darwin-x64.gz", "d4da574d6e2e197bd259b47d69cf262df9e312af24ad960444f6d806d3d4c186"],
    license: "darwin-x64.LICENSE",
  },
};

/** FFmpeg-Builds platform suffix for each Rust target triple. */
const FFB_PLATFORM = {
  "x86_64-pc-windows-msvc": "win64-gpl-shared.zip",
  "aarch64-pc-windows-msvc": "winarm64-gpl-shared.zip",
  "x86_64-unknown-linux-gnu": "linux64-gpl.tar.xz",
  "aarch64-unknown-linux-gnu": "linuxarm64-gpl.tar.xz",
};

function parseArgs() {
  const argv = process.argv.slice(2);
  const out = { target: null, force: false };
  for (let i = 0; i < argv.length; i++) {
    if (argv[i] === "--force") out.force = true;
    else if (argv[i] === "--target") out.target = argv[++i];
    else throw new Error(`Unknown argument: ${argv[i]}`);
  }
  return out;
}

function hostTriple() {
  try {
    return execFileSync("rustc", ["--print", "host-tuple"], { encoding: "utf8" }).trim();
  } catch {
    const vv = execFileSync("rustc", ["-Vv"], { encoding: "utf8" });
    const m = /^host:\s*(\S+)/m.exec(vv);
    if (!m) throw new Error("Could not determine the Rust host target triple.");
    return m[1];
  }
}

async function download(url, dest) {
  process.stdout.write(`  ↓ ${url}\n`);
  const res = await fetch(url, { redirect: "follow" });
  if (!res.ok || !res.body) throw new Error(`GET ${url} → HTTP ${res.status}`);
  await pipeline(Readable.fromWeb(res.body), createWriteStream(dest));
}

async function fetchText(url) {
  const res = await fetch(url, { redirect: "follow" });
  if (!res.ok) throw new Error(`GET ${url} → HTTP ${res.status}`);
  return res.text();
}

async function sha256(file) {
  const h = createHash("sha256");
  await pipeline(createReadStream(file), h);
  return h.digest("hex");
}

async function verify(file, expected) {
  const got = await sha256(file);
  if (got.toLowerCase() !== expected.toLowerCase()) {
    await rm(file, { force: true });
    throw new Error(`SHA-256 mismatch for ${file}\n  expected ${expected}\n  got      ${got}`);
  }
  process.stdout.write(`  ✓ sha256 ${got}\n`);
}

/** Recursively finds the first file whose name matches `name`. */
async function findFile(dir, name) {
  for (const ent of await readdir(dir, { withFileTypes: true })) {
    const p = join(dir, ent.name);
    if (ent.isDirectory()) {
      const hit = await findFile(p, name);
      if (hit) return hit;
    } else if (ent.name.toLowerCase() === name.toLowerCase()) return p;
  }
  return null;
}

function extract(archive, into) {
  // Windows 10+ ships bsdtar as System32\tar.exe, which also reads .zip.
  // (A Git-for-Windows GNU tar earlier on PATH cannot, so call it explicitly.)
  const tar =
    process.platform === "win32" ? join(process.env.SystemRoot ?? "C:\\Windows", "System32", "tar.exe") : "tar";
  execFileSync(tar, ["-xf", archive, "-C", into], { stdio: "inherit" });
}

async function fetchFfmpegBuilds(triple, work) {
  const suffix = FFB_PLATFORM[triple];
  const base = `${FFMPEG_BUILDS}/${FFMPEG_BUILDS_TAG}`;
  const sums = await fetchText(`${base}/checksums.sha256`);
  const line = sums
    .split(/\r?\n/)
    .map((l) => l.trim().split(/\s+/))
    .find(([, name]) => name && name.replace(/^\*/, "").endsWith(`-${suffix}`) && !name.includes("lgpl"));
  if (!line) throw new Error(`No *-${suffix} in ${base}/checksums.sha256`);
  const [hash, rawName] = line;
  const name = rawName.replace(/^\*/, "");

  const archive = join(work, name);
  await download(`${base}/${name}`, archive);
  await verify(archive, hash);

  const out = join(work, "x");
  await mkdir(out, { recursive: true });
  extract(archive, out);

  const win = triple.includes("windows");
  const exe = win ? ".exe" : "";
  for (const tool of ["ffmpeg", "ffprobe"]) {
    const src = await findFile(out, `${tool}${exe}`);
    if (!src) throw new Error(`${tool}${exe} not found in ${name}`);
    const dest = join(BIN_DIR, `viddown-${tool}-${triple}${exe}`);
    await copyFile(src, dest);
    if (!win) await chmod(dest, 0o755);
  }
  if (win) {
    const binDir = dirname(await findFile(out, "ffmpeg.exe"));
    await mkdir(DLL_DIR, { recursive: true });
    for (const f of await readdir(binDir)) {
      if (f.toLowerCase().endsWith(".dll")) await copyFile(join(binDir, f), join(DLL_DIR, f));
    }
  }
  const license = await findFile(out, "LICENSE.txt");
  if (license) await copyFile(license, join(BIN_DIR, "FFMPEG-LICENSE.txt"));
  return { source: `${base}/${name}`, sha256: hash };
}

async function fetchMac(triple, work) {
  const pin = MAC_PINNED[triple];
  for (const tool of ["ffmpeg", "ffprobe"]) {
    const [file, hash] = pin[tool];
    const gz = join(work, file);
    await download(`${MAC_RELEASE}/${file}`, gz);
    await verify(gz, hash);
    const dest = join(BIN_DIR, `viddown-${tool}-${triple}`);
    await pipeline(createReadStream(gz), createGunzip(), createWriteStream(dest));
    await chmod(dest, 0o755);
  }
  await download(`${MAC_RELEASE}/${pin.license}`, join(BIN_DIR, "FFMPEG-LICENSE.txt"));
  return { source: MAC_RELEASE, sha256: [pin.ffmpeg[1], pin.ffprobe[1]].join(",") };
}

async function main() {
  const args = parseArgs();
  const triple = args.target || process.env.TAURI_ENV_TARGET_TRIPLE || hostTriple();
  const isMac = triple in MAC_PINNED;
  if (!isMac && !(triple in FFB_PLATFORM)) {
    throw new Error(`No ffmpeg source configured for target ${triple}`);
  }
  const exe = triple.includes("windows") ? ".exe" : "";
  const wanted = ["ffmpeg", "ffprobe"].map((t) => join(BIN_DIR, `viddown-${t}-${triple}${exe}`));

  await mkdir(BIN_DIR, { recursive: true });
  if (!args.force && wanted.every((f) => existsSync(f)) && existsSync(STAMP)) {
    const stamp = JSON.parse(await readFile(STAMP, "utf8"));
    if (stamp.triple === triple) {
      console.log(`sidecars for ${triple} already present (${stamp.source}); use --force to refetch`);
      return;
    }
  }

  console.log(`Fetching ffmpeg + ffprobe for ${triple}`);
  // Remove other triples' leftovers so the DLL resource dir matches the target.
  await rm(DLL_DIR, { recursive: true, force: true });
  const work = join(BIN_DIR, ".work");
  await rm(work, { recursive: true, force: true });
  await mkdir(work, { recursive: true });
  try {
    const info = isMac ? await fetchMac(triple, work) : await fetchFfmpegBuilds(triple, work);
    if (!triple.includes("windows")) {
      // tauri.windows.conf.json is the only config that bundles this dir, but
      // keep it absent elsewhere so nothing stale is shipped by accident.
      await rm(DLL_DIR, { recursive: true, force: true });
    }
    await writeFile(STAMP, JSON.stringify({ triple, ...info, fetchedAt: new Date().toISOString() }, null, 2));
    for (const f of wanted) console.log(`  → ${f}`);
  } finally {
    await rm(work, { recursive: true, force: true });
  }
}

main().catch((e) => {
  console.error(`fetch-sidecars: ${e.message}`);
  process.exit(1);
});
