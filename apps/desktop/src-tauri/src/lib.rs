//! viddown desktop shell.
//!
//! The UI is the Expo app's web export (apps/mobile). Its `TauriEngine`
//! (apps/mobile/src/engine/tauri.ts) calls the commands below through
//! `window.__TAURI__.core.invoke`.

mod jobs;
mod paths;
mod proc_tree;
mod ytdlp;

use std::sync::Arc;

use serde::Serialize;
use tauri::ipc::Channel;
use tauri::{AppHandle, Manager, RunEvent, State};

use jobs::{JobEvent, Jobs};

type Shared = Arc<Jobs>;

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct Versions {
    ytdlp: Option<String>,
    ffmpeg: Option<String>,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct EngineInfo {
    ytdlp_path: String,
    ytdlp_installed: bool,
    ffmpeg_path: String,
    ffmpeg_present: bool,
    max_jobs: usize,
    os: &'static str,
    arch: &'static str,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct AppUpdate {
    version: String,
    current_version: String,
    notes: Option<String>,
    date: Option<String>,
}

/// Installs yt-dlp on first use. Holds the binary write-lock so no job sees
/// a half-written file.
async fn ensure_ytdlp(app: &AppHandle, jobs: &Jobs) -> Result<std::path::PathBuf, String> {
    let path = paths::ytdlp_path(app)?;
    if path.exists() {
        return Ok(path);
    }
    let _w = jobs.binary.write().await;
    ytdlp::ensure(app).await
}

#[tauri::command]
async fn probe(app: AppHandle, jobs: State<'_, Shared>, id: String, args: Vec<String>) -> Result<String, String> {
    jobs.probe(id, args, ensure_ytdlp(&app, &jobs)).await
}

#[tauri::command]
async fn start_job(
    app: AppHandle,
    jobs: State<'_, Shared>,
    id: String,
    args: Vec<String>,
    on_event: Channel<JobEvent>,
) -> Result<(), String> {
    let jobs = jobs.inner().clone();
    let exe = {
        let jobs = jobs.clone();
        async move { ensure_ytdlp(&app, &jobs).await }
    };
    jobs.start(id, args, on_event, exe).await
}

#[tauri::command]
fn cancel_job(jobs: State<'_, Shared>, id: String) -> bool {
    jobs.cancel(&id)
}

#[tauri::command]
async fn versions(app: AppHandle) -> Result<Versions, String> {
    let exe = paths::ytdlp_path(&app)?;
    let (ytdlp, ffmpeg) = tokio::join!(ytdlp::ytdlp_version(&exe), ytdlp::ffmpeg_version());
    Ok(Versions { ytdlp, ffmpeg })
}

/// Installs yt-dlp if missing, then `yt-dlp --update-to stable[@version]`.
/// Refuses while downloads are running rather than blocking for minutes.
#[tauri::command]
async fn self_update(app: AppHandle, jobs: State<'_, Shared>, version: Option<String>) -> Result<Versions, String> {
    let version = version.filter(|v| !v.trim().is_empty());
    if let Some(v) = &version {
        ytdlp::validate_version(v)?;
    }
    let exe = paths::ytdlp_path(&app)?;
    {
        let _w = jobs
            .binary
            .try_write()
            .map_err(|_| "Downloads are running. Update the engine when they finish.".to_string())?;
        if !exe.exists() {
            ytdlp::install(&exe, version.as_deref()).await?;
        } else {
            ytdlp::self_update(&exe, version.as_deref()).await?;
        }
    }
    versions(app).await
}

#[tauri::command]
fn engine_info(app: AppHandle) -> Result<EngineInfo, String> {
    let y = paths::ytdlp_path(&app)?;
    let f = paths::ffmpeg_path()?;
    Ok(EngineInfo {
        ytdlp_installed: y.exists(),
        ytdlp_path: y.to_string_lossy().into_owned(),
        ffmpeg_present: f.exists(),
        ffmpeg_path: f.to_string_lossy().into_owned(),
        max_jobs: jobs::MAX_JOBS,
        os: std::env::consts::OS,
        arch: std::env::consts::ARCH,
    })
}

#[tauri::command]
fn default_output_dir(app: AppHandle) -> Result<String, String> {
    paths::default_output_dir(&app).map(|p| p.to_string_lossy().into_owned())
}

/// Opens the file manager with `path` selected (or the folder itself).
#[tauri::command]
fn reveal(path: String) -> Result<(), String> {
    let p = std::path::PathBuf::from(&path);
    if p.is_file() {
        tauri_plugin_opener::reveal_item_in_dir(&p).map_err(|e| e.to_string())
    } else if p.is_dir() {
        tauri_plugin_opener::open_path(&path, None::<&str>).map_err(|e| e.to_string())
    } else if let Some(parent) = p.parent().filter(|d| d.is_dir()) {
        tauri_plugin_opener::open_path(parent, None::<&str>).map_err(|e| e.to_string())
    } else {
        Err(format!("{path} no longer exists"))
    }
}

/// Native folder picker. Resolves to `None` when the user cancels.
#[tauri::command]
async fn pick_folder(app: AppHandle, start: Option<String>) -> Result<Option<String>, String> {
    use tauri_plugin_dialog::DialogExt;
    let (tx, rx) = tokio::sync::oneshot::channel();
    let mut dlg = app.dialog().file().set_title("Save downloads to");
    if let Some(dir) = start.filter(|d| std::path::Path::new(d).is_dir()) {
        dlg = dlg.set_directory(dir);
    }
    dlg.pick_folder(move |p| {
        let _ = tx.send(p);
    });
    let picked = rx.await.map_err(|e| e.to_string())?;
    Ok(picked.and_then(|p| p.into_path().ok()).map(|p| p.to_string_lossy().into_owned()))
}

/// Opens an http(s) link in the default browser (About / Hire me links).
#[tauri::command]
fn open_url(url: String) -> Result<(), String> {
    if !(url.starts_with("https://") || url.starts_with("http://") || url.starts_with("mailto:")) {
        return Err("only http(s) and mailto links can be opened".into());
    }
    tauri_plugin_opener::open_url(url, None::<&str>).map_err(|e| e.to_string())
}

#[cfg(desktop)]
#[tauri::command]
async fn app_update_check(app: AppHandle) -> Result<Option<AppUpdate>, String> {
    use tauri_plugin_updater::UpdaterExt;
    let update = app.updater().map_err(|e| e.to_string())?.check().await.map_err(|e| e.to_string())?;
    Ok(update.map(|u| AppUpdate {
        version: u.version.clone(),
        current_version: u.current_version.clone(),
        notes: u.body.clone(),
        date: u.date.map(|d| d.to_string()),
    }))
}

/// Downloads, verifies (minisign) and installs the update, then restarts.
#[cfg(desktop)]
#[tauri::command]
async fn app_update_install(app: AppHandle, jobs: State<'_, Shared>) -> Result<(), String> {
    use tauri_plugin_updater::UpdaterExt;
    let Some(update) = app.updater().map_err(|e| e.to_string())?.check().await.map_err(|e| e.to_string())? else {
        return Ok(());
    };
    update.download_and_install(|_, _| {}, || {}).await.map_err(|e| e.to_string())?;
    jobs.cancel_all();
    app.restart();
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let jobs: Shared = Arc::new(Jobs::default());

    let mut builder = tauri::Builder::default();
    #[cfg(desktop)]
    {
        // Must be registered first. A second launch focuses the existing window
        // instead of starting a competing set of downloads.
        builder = builder.plugin(tauri_plugin_single_instance::init(|app, _argv, _cwd| {
            if let Some(w) = app.get_webview_window("main") {
                let _ = w.unminimize();
                let _ = w.set_focus();
            }
        }));
        builder = builder.plugin(tauri_plugin_updater::Builder::new().build());
    }

    let app = builder
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_opener::init())
        .manage(jobs)
        .setup(|app| {
            // Synchronous: must finish before the UI can start a probe.
            paths::sweep_tmp(app.handle());
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            probe,
            start_job,
            cancel_job,
            versions,
            self_update,
            engine_info,
            default_output_dir,
            reveal,
            pick_folder,
            open_url,
            #[cfg(desktop)]
            app_update_check,
            #[cfg(desktop)]
            app_update_install,
        ])
        .build(tauri::generate_context!())
        .expect("error while building viddown");

    app.run(|handle, event| {
        if let RunEvent::Exit = event {
            // Windows job objects die with us; Unix process groups would not.
            handle.state::<Shared>().cancel_all();
        }
    });
}
