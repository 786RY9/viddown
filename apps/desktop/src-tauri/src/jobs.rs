//! Running yt-dlp processes: probes and download jobs.
//!
//! Argument lists are built in TypeScript by `@viddown/core` (`probeArgs`,
//! `jobArgs`) so desktop, Android and the server share one implementation.
//! This side only validates them, injects the bundled ffmpeg location and
//! streams output lines back over a Tauri channel. Concurrency is scheduled
//! by the TS queue; the semaphores here are a backstop.

use std::collections::HashMap;
use std::future::Future;
use std::path::PathBuf;
use std::process::Stdio;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};

use serde::Serialize;
use tauri::ipc::Channel;
use tokio::io::{AsyncRead, AsyncReadExt};
use tokio::process::Command;
use tokio::sync::{Notify, RwLock, Semaphore};

use crate::paths;
use crate::proc_tree::{self, ProcTree};

/// Hard caps independent of the user's queue setting.
pub const MAX_JOBS: usize = 6;
pub const MAX_PROBES: usize = 4;
/// Upper bound on a single output line (probe JSON is read separately).
const MAX_LINE: usize = 64 * 1024;
/// Upper bound on a probe's JSON output.
const MAX_PROBE_BYTES: usize = 64 * 1024 * 1024;

#[derive(Clone, Serialize)]
#[serde(tag = "type", rename_all = "camelCase")]
pub enum JobEvent {
    #[serde(rename_all = "camelCase")]
    Line { stream: &'static str, line: String },
    #[serde(rename_all = "camelCase")]
    Exit { code: Option<i32>, canceled: bool },
}

struct Entry {
    canceled: AtomicBool,
    wake: Notify,
    proc: Mutex<Option<ProcTree>>,
}

impl Entry {
    fn new() -> Arc<Self> {
        Arc::new(Self { canceled: AtomicBool::new(false), wake: Notify::new(), proc: Mutex::new(None) })
    }
    fn cancel(&self) {
        self.canceled.store(true, Ordering::SeqCst);
        self.wake.notify_waiters();
        self.wake.notify_one();
        if let Some(p) = self.proc.lock().unwrap().as_ref() {
            p.kill();
        }
    }
    fn is_canceled(&self) -> bool {
        self.canceled.load(Ordering::SeqCst)
    }
}

pub struct Jobs {
    entries: Mutex<HashMap<String, Arc<Entry>>>,
    job_slots: Arc<Semaphore>,
    probe_slots: Arc<Semaphore>,
    /// Readers: running yt-dlp processes. Writer: install / self-update.
    pub binary: Arc<RwLock<()>>,
}

impl Default for Jobs {
    fn default() -> Self {
        Self {
            entries: Mutex::new(HashMap::new()),
            job_slots: Arc::new(Semaphore::new(MAX_JOBS)),
            probe_slots: Arc::new(Semaphore::new(MAX_PROBES)),
            binary: Arc::new(RwLock::new(())),
        }
    }
}

impl Jobs {
    fn register(&self, id: &str) -> Result<Arc<Entry>, String> {
        validate_id(id)?;
        let mut map = self.entries.lock().unwrap();
        if map.contains_key(id) {
            return Err(format!("job {id} is already running"));
        }
        let e = Entry::new();
        map.insert(id.to_string(), e.clone());
        Ok(e)
    }

    fn unregister(&self, id: &str) {
        self.entries.lock().unwrap().remove(id);
    }

    /// Cancels a probe or job. Unknown ids are a no-op (it already finished).
    pub fn cancel(&self, id: &str) -> bool {
        let entry = self.entries.lock().unwrap().get(id).cloned();
        match entry {
            Some(e) => {
                e.cancel();
                true
            }
            None => false,
        }
    }

    /// Kills everything; called on app exit (Unix process groups outlive us).
    pub fn cancel_all(&self) {
        for e in self.entries.lock().unwrap().values() {
            e.cancel();
        }
    }
}

fn validate_id(id: &str) -> Result<(), String> {
    if !id.is_empty() && id.len() <= 128 && id.chars().all(|c| c.is_ascii_alphanumeric() || "-_.:".contains(c)) {
        Ok(())
    } else {
        Err("invalid job id".into())
    }
}

/// Options the webview may never pass: they run commands, load code or
/// configs, read arbitrary files, or replace the binary. The URL after `--` is
/// never inspected.
const DENY_LONG: &[&str] = &[
    "--exec",
    "--exec-before-download",
    "--no-exec",
    "--netrc-cmd",
    "--netrc-location",
    "--downloader",
    "--external-downloader",
    "--downloader-args",
    "--external-downloader-args",
    "--use-postprocessor",
    "--config-location",
    "--config-locations",
    "--plugin-dirs",
    "--batch-file",
    "--load-info-json",
    "--cookies-from-browser",
    "--update",
    "--update-to",
    "--ffmpeg-location",
    "--paths",
];
const DENY_SHORT: &[&str] = &["-a", "-U", "-P"];

pub fn validate_args(args: &[String]) -> Result<(), String> {
    let mut saw_sep = false;
    for a in args {
        if a == "--" {
            saw_sep = true;
            break;
        }
        if let Some(rest) = a.strip_prefix("--") {
            let name = rest.split('=').next().unwrap_or(rest);
            if DENY_LONG.contains(&format!("--{name}").as_str()) {
                return Err(format!("option not allowed: --{name}"));
            }
        } else if a.starts_with('-') && a.len() > 1 && !a[1..].starts_with(|c: char| c.is_ascii_digit()) {
            // Only plain two-character short options; no clusters like "-xa".
            if a.chars().count() != 2 || DENY_SHORT.contains(&a.as_str()) {
                return Err(format!("option not allowed: {a}"));
            }
        }
    }
    if !saw_sep {
        return Err("arguments must end with `-- <url>`".into());
    }
    Ok(())
}

/// Options prepended to every run: ignore user yt-dlp configs and plugins so
/// a stray `~/.config/yt-dlp/config` cannot change output parsing.
fn fixed_args(with_ffmpeg: bool) -> Result<Vec<String>, String> {
    let mut v = vec!["--ignore-config".to_string(), "--no-plugin-dirs".to_string()];
    if with_ffmpeg {
        let ff = paths::ffmpeg_path()?;
        if ff.exists() {
            v.push("--ffmpeg-location".into());
            v.push(ff.to_string_lossy().into_owned());
        }
    }
    Ok(v)
}

fn command(exe: &PathBuf, args: Vec<String>) -> Command {
    let mut cmd = Command::new(exe);
    cmd.args(args)
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .envs(paths::engine_env(exe));
    proc_tree::prepare(&mut cmd);
    cmd
}

/// Reads a pipe and forwards each `\n`- or `\r`-terminated line.
async fn pump<R: AsyncRead + Unpin>(mut r: R, stream: &'static str, ch: Channel<JobEvent>) {
    let mut buf = vec![0u8; 16 * 1024];
    let mut line: Vec<u8> = Vec::with_capacity(512);
    let emit = |line: &mut Vec<u8>| {
        if !line.is_empty() {
            let text = String::from_utf8_lossy(line).trim_end().to_string();
            if !text.is_empty() {
                let _ = ch.send(JobEvent::Line { stream, line: text });
            }
            line.clear();
        }
    };
    loop {
        let n = match r.read(&mut buf).await {
            Ok(0) | Err(_) => break,
            Ok(n) => n,
        };
        for &b in &buf[..n] {
            if b == b'\n' || b == b'\r' {
                emit(&mut line);
            } else if line.len() < MAX_LINE {
                line.push(b);
            }
        }
    }
    emit(&mut line);
}

impl Jobs {
    /// Starts a download in the background; output and the final exit arrive on `ch`.
    pub async fn start<F>(
        self: &Arc<Self>,
        id: String,
        args: Vec<String>,
        ch: Channel<JobEvent>,
        exe: F,
    ) -> Result<(), String>
    where
        F: Future<Output = Result<PathBuf, String>> + Send + 'static,
    {
        validate_args(&args)?;
        let mut argv = fixed_args(true)?;
        argv.extend(args);
        // Register before resolving yt-dlp so a cancel during the first-run
        // install is not lost.
        let entry = self.register(&id)?;
        let jobs = self.clone();

        tauri::async_runtime::spawn(async move {
            let exit = async {
                let exe = tokio::select! {
                    r = exe => r?,
                    _ = entry.wake.notified() => return Ok(None),
                };
                jobs.run_job(&entry, exe, argv, ch.clone()).await
            }
            .await;
            jobs.unregister(&id);
            let (code, canceled) = match exit {
                Ok(code) => (code, entry.is_canceled()),
                Err(msg) => {
                    let _ = ch.send(JobEvent::Line { stream: "stderr", line: format!("ERROR: {msg}") });
                    (None, entry.is_canceled())
                }
            };
            let _ = ch.send(JobEvent::Exit { code, canceled });
        });
        Ok(())
    }

    async fn run_job(
        &self,
        entry: &Arc<Entry>,
        exe: PathBuf,
        argv: Vec<String>,
        ch: Channel<JobEvent>,
    ) -> Result<Option<i32>, String> {
        // Wait for a slot (or cancellation while queued).
        let _slot = tokio::select! {
            p = self.job_slots.clone().acquire_owned() => p.map_err(|e| e.to_string())?,
            _ = entry.wake.notified() => return Ok(None),
        };
        if entry.is_canceled() {
            return Ok(None);
        }
        let _bin = self.binary.clone().read_owned().await;

        let mut child = command(&exe, argv).spawn().map_err(|e| format!("could not start yt-dlp: {e}"))?;
        *entry.proc.lock().unwrap() = ProcTree::attach(&child);
        if entry.is_canceled() {
            if let Some(p) = entry.proc.lock().unwrap().as_ref() {
                p.kill();
            }
        }

        let out = tokio::spawn(pump(child.stdout.take().unwrap(), "stdout", ch.clone()));
        let err = tokio::spawn(pump(child.stderr.take().unwrap(), "stderr", ch));
        let status = child.wait().await.map_err(|e| e.to_string())?;
        let _ = out.await;
        let _ = err.await;
        // Drop the job object now: kills any straggler left in the tree.
        entry.proc.lock().unwrap().take();
        Ok(status.code())
    }

    /// Runs a probe and returns yt-dlp's JSON. Errors carry stderr for
    /// classification in TypeScript.
    pub async fn probe<F>(&self, id: String, args: Vec<String>, exe: F) -> Result<String, String>
    where
        F: Future<Output = Result<PathBuf, String>>,
    {
        validate_args(&args)?;
        let mut argv = fixed_args(false)?;
        argv.extend(args);
        let entry = self.register(&id)?;
        let res = async {
            let exe = tokio::select! {
                r = exe => r?,
                _ = entry.wake.notified() => return Err("canceled".to_string()),
            };
            self.run_probe(&entry, exe, argv).await
        }
        .await;
        self.unregister(&id);
        if entry.is_canceled() {
            return Err("canceled".into());
        }
        res
    }

    async fn run_probe(&self, entry: &Arc<Entry>, exe: PathBuf, argv: Vec<String>) -> Result<String, String> {
        let _slot = tokio::select! {
            p = self.probe_slots.clone().acquire_owned() => p.map_err(|e| e.to_string())?,
            _ = entry.wake.notified() => return Err("canceled".into()),
        };
        let _bin = self.binary.clone().read_owned().await;
        let mut child = command(&exe, argv).spawn().map_err(|e| format!("could not start yt-dlp: {e}"))?;
        *entry.proc.lock().unwrap() = ProcTree::attach(&child);

        let mut stdout = child.stdout.take().unwrap();
        let mut stderr = child.stderr.take().unwrap();
        let read_out = async {
            let mut v = Vec::new();
            let mut chunk = vec![0u8; 64 * 1024];
            loop {
                match stdout.read(&mut chunk).await {
                    Ok(0) | Err(_) => break,
                    Ok(n) if v.len() + n <= MAX_PROBE_BYTES => v.extend_from_slice(&chunk[..n]),
                    Ok(_) => {}
                }
            }
            v
        };
        let read_err = async {
            let mut v = Vec::new();
            let _ = stderr.read_to_end(&mut v).await;
            v
        };
        let (out, err) = tokio::join!(read_out, read_err);
        let status = child.wait().await.map_err(|e| e.to_string())?;
        entry.proc.lock().unwrap().take();

        if status.success() {
            String::from_utf8(out).map_err(|_| "yt-dlp returned invalid UTF-8".into())
        } else {
            let err = String::from_utf8_lossy(&err);
            // Keep the tail; classifyError() looks at the last ERROR: line.
            let tail: String = err.chars().rev().take(8000).collect::<Vec<_>>().into_iter().rev().collect();
            Err(if tail.trim().is_empty() { format!("yt-dlp exited with {status}") } else { tail })
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn v(a: &[&str]) -> Vec<String> {
        a.iter().map(|s| s.to_string()).collect()
    }

    #[test]
    fn accepts_core_job_args() {
        let args = v(&[
            "-f", "137+bestaudio/best", "--merge-output-format", "mp4", "--continue",
            "--concurrent-fragments", "8", "-o", "C:\\x\\%(title)s.%(ext)s",
            "--download-sections", "*00:00:05-00:00:15", "--progress-template",
            "download:[vd:dl] %(progress)j", "--print", "after_move:[vd:file] %(filepath)s",
            "--", "https://www.youtube.com/watch?v=aqz-KE-bpKQ",
        ]);
        assert!(validate_args(&args).is_ok());
    }

    #[test]
    fn rejects_dangerous() {
        for bad in [
            v(&["--exec", "calc", "--", "u"]),
            v(&["--exec=calc", "--", "u"]),
            v(&["--use-postprocessor", "Exec:exec=calc", "--", "u"]),
            v(&["-xa", "list.txt", "--", "u"]),
            v(&["-U", "--", "u"]),
            v(&["https://x"]), // missing separator
        ] {
            assert!(validate_args(&bad).is_err(), "{bad:?}");
        }
        // Anything after `--` is the URL, even if it looks like an option.
        assert!(validate_args(&v(&["--", "--exec"])).is_ok());
    }
}
