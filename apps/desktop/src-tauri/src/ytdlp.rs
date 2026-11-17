//! Installing, versioning and updating the standalone yt-dlp binary.
//!
//! First use downloads the official standalone build for this OS/arch from
//! GitHub Releases into the app data dir and verifies it against the release's
//! `SHA2-256SUMS`. After that, yt-dlp updates itself with
//! `yt-dlp --update-to stable@<version>` (it verifies its own download).

use std::path::{Path, PathBuf};

use futures_util::StreamExt;
use sha2::{Digest, Sha256};
use tauri::{AppHandle, Runtime};
use tokio::io::AsyncWriteExt;

use crate::paths;
use crate::proc_tree::quiet_std_command;

const RELEASES: &str = "https://github.com/yt-dlp/yt-dlp/releases";

/// Name of the standalone release asset for the running platform.
pub fn asset_name() -> Result<&'static str, String> {
    use std::env::consts::{ARCH, OS};
    Ok(match (OS, ARCH) {
        ("windows", "x86_64") => "yt-dlp.exe",
        ("windows", "aarch64") => "yt-dlp_arm64.exe",
        ("windows", "x86") => "yt-dlp_x86.exe",
        // Universal2 build: Intel + Apple Silicon.
        ("macos", _) => "yt-dlp_macos",
        ("linux", "x86_64") => "yt-dlp_linux",
        ("linux", "aarch64") => "yt-dlp_linux_aarch64",
        (os, arch) => return Err(format!("no yt-dlp build for {os}/{arch}")),
    })
}

/// `2025.10.22`-style release tag, or `None` for latest.
pub fn validate_version(v: &str) -> Result<(), String> {
    if !v.is_empty() && v.len() <= 32 && v.chars().all(|c| c.is_ascii_digit() || c == '.') {
        Ok(())
    } else {
        Err(format!("invalid yt-dlp version: {v:?}"))
    }
}

fn client() -> Result<reqwest::Client, String> {
    // reqwest is built without a bundled crypto provider (as in the updater
    // plugin); install ring once for the process.
    if rustls::crypto::CryptoProvider::get_default().is_none() {
        let _ = rustls::crypto::ring::default_provider().install_default();
    }
    reqwest::Client::builder()
        .user_agent(concat!("viddown/", env!("CARGO_PKG_VERSION")))
        .connect_timeout(std::time::Duration::from_secs(20))
        .build()
        .map_err(|e| e.to_string())
}

fn release_base(version: Option<&str>) -> String {
    match version {
        Some(v) => format!("{RELEASES}/download/{v}"),
        None => format!("{RELEASES}/latest/download"),
    }
}

/// Parses a `SHA2-256SUMS` file (`<hex>  <name>` per line).
fn find_sum(sums: &str, asset: &str) -> Option<String> {
    sums.lines().find_map(|l| {
        let mut it = l.split_whitespace();
        let hash = it.next()?;
        let name = it.next()?.trim_start_matches('*');
        (name == asset && hash.len() == 64).then(|| hash.to_ascii_lowercase())
    })
}

/// Downloads and verifies yt-dlp into `dest` (atomic rename on success).
pub async fn install(dest: &Path, version: Option<&str>) -> Result<(), String> {
    if let Some(v) = version {
        validate_version(v)?;
    }
    let asset = asset_name()?;
    let base = release_base(version);
    let http = client()?;

    let sums = http
        .get(format!("{base}/SHA2-256SUMS"))
        .send()
        .await
        .and_then(|r| r.error_for_status())
        .map_err(|e| format!("Couldn't reach GitHub to install yt-dlp: {e}"))?
        .text()
        .await
        .map_err(|e| e.to_string())?;
    let expected = find_sum(&sums, asset).ok_or_else(|| format!("{asset} missing from SHA2-256SUMS"))?;

    let dir = dest.parent().ok_or("bad yt-dlp path")?;
    tokio::fs::create_dir_all(dir).await.map_err(|e| format!("create {}: {e}", dir.display()))?;
    let tmp: PathBuf = dest.with_extension("download");

    let resp = http
        .get(format!("{base}/{asset}"))
        .send()
        .await
        .and_then(|r| r.error_for_status())
        .map_err(|e| format!("Couldn't download yt-dlp: {e}"))?;
    let mut file = tokio::fs::File::create(&tmp).await.map_err(|e| e.to_string())?;
    let mut hasher = Sha256::new();
    let mut body = resp.bytes_stream();
    while let Some(chunk) = body.next().await {
        let chunk = chunk.map_err(|e| format!("yt-dlp download interrupted: {e}"))?;
        hasher.update(&chunk);
        file.write_all(&chunk).await.map_err(|e| e.to_string())?;
    }
    file.flush().await.map_err(|e| e.to_string())?;
    drop(file);

    let got = hex::encode(hasher.finalize());
    if got != expected {
        let _ = tokio::fs::remove_file(&tmp).await;
        return Err(format!("yt-dlp checksum mismatch (expected {expected}, got {got})"));
    }

    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        tokio::fs::set_permissions(&tmp, std::fs::Permissions::from_mode(0o755))
            .await
            .map_err(|e| e.to_string())?;
    }
    tokio::fs::rename(&tmp, dest).await.map_err(|e| format!("install yt-dlp: {e}"))?;
    Ok(())
}

/// Path of an installed, runnable yt-dlp, downloading it first if needed.
pub async fn ensure<R: Runtime>(app: &AppHandle<R>) -> Result<PathBuf, String> {
    let path = paths::ytdlp_path(app)?;
    if !path.exists() {
        install(&path, None).await?;
    }
    Ok(path)
}

/// Runs `<exe> <args>` and returns trimmed stdout on success.
async fn run_capture(exe: PathBuf, args: Vec<String>, env: Vec<(&'static str, std::ffi::OsString)>) -> Result<String, String> {
    tokio::task::spawn_blocking(move || {
        let out = quiet_std_command(&exe)
            .args(&args)
            .envs(env)
            .output()
            .map_err(|e| format!("{}: {e}", exe.display()))?;
        let stdout = String::from_utf8_lossy(&out.stdout).trim().to_string();
        if out.status.success() {
            Ok(stdout)
        } else {
            let stderr = String::from_utf8_lossy(&out.stderr).trim().to_string();
            Err(if stderr.is_empty() { stdout } else { stderr })
        }
    })
    .await
    .map_err(|e| e.to_string())?
}

pub async fn ytdlp_version(exe: &Path) -> Option<String> {
    if !exe.exists() {
        return None;
    }
    run_capture(exe.to_path_buf(), vec!["--version".into()], paths::engine_env(exe)).await.ok()
}

pub async fn ffmpeg_version() -> Option<String> {
    let exe = paths::ffmpeg_path().ok()?;
    let out = run_capture(exe, vec!["-hide_banner".into(), "-version".into()], vec![]).await.ok()?;
    // "ffmpeg version N-128970-g11337618b2-20261116 Copyright (c) ..."
    out.lines()
        .next()?
        .strip_prefix("ffmpeg version ")?
        .split_whitespace()
        .next()
        .map(str::to_string)
}

/// `yt-dlp --update-to stable[@version]`; returns yt-dlp's output.
pub async fn self_update(exe: &Path, version: Option<&str>) -> Result<String, String> {
    let target = match version {
        Some(v) => {
            validate_version(v)?;
            format!("stable@{v}")
        }
        None => "stable".to_string(),
    };
    run_capture(exe.to_path_buf(), vec!["--update-to".into(), target], paths::engine_env(exe)).await
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_sums() {
        let s = "aaaa  yt-dlp\n3f1f2d0c7b2e4a8c9d0e1f2a3b4c5d6e7f8091a2b3c4d5e6f708192a3b4c5d6e  yt-dlp.exe\n";
        assert_eq!(
            find_sum(s, "yt-dlp.exe").as_deref(),
            Some("3f1f2d0c7b2e4a8c9d0e1f2a3b4c5d6e7f8091a2b3c4d5e6f708192a3b4c5d6e")
        );
        assert_eq!(find_sum(s, "yt-dlp"), None); // short hash rejected
    }

    #[test]
    fn versions() {
        assert!(validate_version("2025.10.22").is_ok());
        assert!(validate_version("nightly").is_err());
        assert!(validate_version("1; rm -rf /").is_err());
    }
}
