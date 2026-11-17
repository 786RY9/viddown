//! Where the engine binaries live.
//!
//! * yt-dlp is downloaded into the app's local data dir so it can update itself
//!   (`yt-dlp --update-to ...`) without an app release.
//! * ffmpeg / ffprobe ship inside the bundle as Tauri sidecars named
//!   `viddown-ffmpeg` / `viddown-ffprobe`. Tauri places sidecars next to the
//!   main executable (install dir on Windows, `Contents/MacOS` on macOS,
//!   `/usr/bin` for .deb, `usr/bin` inside an AppImage; `target/<profile>` in dev).

use std::ffi::OsString;
use std::path::{Path, PathBuf};

use tauri::{AppHandle, Manager, Runtime};

pub const EXE_SUFFIX: &str = std::env::consts::EXE_SUFFIX;

/// Directory holding the main executable and therefore the sidecars.
fn exe_dir() -> Result<PathBuf, String> {
    let exe = std::env::current_exe().map_err(|e| format!("current_exe: {e}"))?;
    // Resolve symlinks (e.g. AppImage / Homebrew-style links) to find siblings.
    let exe = std::fs::canonicalize(&exe).unwrap_or(exe);
    exe.parent()
        .map(|p| p.to_path_buf())
        .ok_or_else(|| "executable has no parent directory".into())
}

/// Absolute path of the bundled ffmpeg. Passed to yt-dlp as `--ffmpeg-location`;
/// yt-dlp then finds `viddown-ffprobe` beside it.
pub fn ffmpeg_path() -> Result<PathBuf, String> {
    Ok(exe_dir()?.join(format!("viddown-ffmpeg{EXE_SUFFIX}")))
}

/// `<app local data>/bin`, where the self-updating yt-dlp binary lives.
pub fn bin_dir<R: Runtime>(app: &AppHandle<R>) -> Result<PathBuf, String> {
    let dir = app
        .path()
        .app_local_data_dir()
        .map_err(|e| format!("app data dir: {e}"))?
        .join("bin");
    Ok(dir)
}

pub fn ytdlp_path<R: Runtime>(app: &AppHandle<R>) -> Result<PathBuf, String> {
    Ok(bin_dir(app)?.join(format!("yt-dlp{EXE_SUFFIX}")))
}

/// `~/Downloads/viddown`, created on demand.
pub fn default_output_dir<R: Runtime>(app: &AppHandle<R>) -> Result<PathBuf, String> {
    let base = app
        .path()
        .download_dir()
        .or_else(|_| app.path().home_dir().map(|h| h.join("Downloads")))
        .map_err(|e| format!("downloads dir: {e}"))?;
    let dir = base.join("viddown");
    std::fs::create_dir_all(&dir).map_err(|e| format!("create {}: {e}", dir.display()))?;
    Ok(dir)
}

/// Private temp dir for engine processes: `<app local data>/tmp`, a sibling
/// of the yt-dlp `bin` dir.
///
/// The standalone yt-dlp is a PyInstaller one-file build that unpacks itself
/// into `$TEMP/_MEI*`. Killing its process tree on cancel skips that cleanup,
/// so the leftovers go here and `sweep_tmp` clears them on the next launch.
pub fn tmp_dir_for(ytdlp: &Path) -> Option<PathBuf> {
    Some(ytdlp.parent()?.parent()?.join("tmp"))
}

/// Environment for every yt-dlp / ffmpeg run.
pub fn engine_env(ytdlp: &Path) -> Vec<(&'static str, OsString)> {
    // Force UTF-8 output regardless of the Windows code page.
    let mut env = vec![("PYTHONIOENCODING", "utf-8".into()), ("PYTHONUTF8", "1".into())];
    if let Some(tmp) = tmp_dir_for(ytdlp).filter(|t| std::fs::create_dir_all(t).is_ok()) {
        for key in ["TMP", "TEMP", "TMPDIR"] {
            env.push((key, tmp.clone().into_os_string()));
        }
    }
    env
}

/// Removes leftovers from killed runs. Called once at startup, before any
/// engine process exists (single-instance guarantees no other copy runs).
pub fn sweep_tmp<R: Runtime>(app: &AppHandle<R>) {
    if let Some(tmp) = ytdlp_path(app).ok().and_then(|y| tmp_dir_for(&y)) {
        let _ = std::fs::remove_dir_all(&tmp);
    }
}
