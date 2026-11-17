import {
  EngineError,
  ProgressTracker,
  classifyError,
  filterVideoFormats,
  jobArgs,
  parseLine,
  probeArgs,
  type DownloadEngine,
  type EngineVersions,
  type JobCallbacks,
  type JobResult,
  type JobSpec,
  type MediaInfo,
  type RunningJob,
  type YtFormat,
} from "@viddown/core";

/**
 * Desktop engine: yt-dlp + ffmpeg run by the Tauri shell (apps/desktop).
 *
 * All yt-dlp argv is built here with @viddown/core (`probeArgs`, `jobArgs`);
 * Rust validates it, adds `--ffmpeg-location` for the bundled ffmpeg and
 * streams every stdout/stderr line back over a Tauri channel. Parsing,
 * progress and error classification are core's `parseLine`,
 * `ProgressTracker` and `classifyError`.
 *
 * The page talks to Rust through `window.__TAURI__` (the shell sets
 * `app.withGlobalTauri`), so this file needs no `@tauri-apps/api` dependency.
 */

// ── Minimal typing of the injected Tauri global ────────────────────────────

interface TauriChannel<T> {
  onmessage: (msg: T) => void;
}

interface TauriGlobal {
  core: {
    invoke<T>(cmd: string, args?: Record<string, unknown>): Promise<T>;
    Channel: new <T>() => TauriChannel<T>;
  };
}

type JobEvent =
  | { type: "line"; stream: "stdout" | "stderr"; line: string }
  | { type: "exit"; code: number | null; canceled: boolean };

/** Shape of `engine_info` (paths and limits; useful for a diagnostics view). */
export interface DesktopEngineInfo {
  ytdlpPath: string;
  ytdlpInstalled: boolean;
  ffmpegPath: string;
  ffmpegPresent: boolean;
  maxJobs: number;
  os: string;
  arch: string;
}

export interface AppUpdateInfo {
  version: string;
  currentVersion: string;
  notes?: string | null;
  date?: string | null;
}

function tauri(): TauriGlobal | null {
  const w = globalThis as { __TAURI__?: TauriGlobal };
  return w.__TAURI__?.core ? w.__TAURI__ : null;
}

/** True when running inside the viddown desktop shell. */
export function isTauri(): boolean {
  return tauri() !== null;
}

function invoke<T>(cmd: string, args?: Record<string, unknown>): Promise<T> {
  const t = tauri();
  if (!t) return Promise.reject(new EngineError("unknown", "Not running inside the desktop app."));
  return t.core.invoke<T>(cmd, args);
}

/** Rust commands reject with a plain string; turn it into a JobError. */
function toEngineError(e: unknown): EngineError {
  if (e instanceof EngineError) return e;
  const msg = typeof e === "string" ? e : e instanceof Error ? e.message : String(e);
  const { kind, message } = classifyError(msg);
  return new EngineError(kind, message);
}

let seq = 0;
const uid = (prefix: string) => `${prefix}-${Date.now().toString(36)}-${(seq++).toString(36)}`;

/** Lines kept for error classification when yt-dlp exits non-zero. */
const TAIL_LINES = 40;

export interface TauriEngineOptions {
  /** `--concurrent-fragments` per job (desktop default 8). */
  concurrentFragments?: number;
}

export class TauriEngine implements DownloadEngine {
  readonly name = "desktop" as const;

  /** Settles when the previous process for an id has fully exited. */
  private settled = new Map<string, Promise<unknown>>();
  private outputDir: Promise<string> | null = null;

  constructor(private readonly opts: TauriEngineOptions = {}) {}

  async probe(url: string, signal?: AbortSignal): Promise<MediaInfo> {
    if (signal?.aborted) throw new EngineError("canceled", "Canceled");
    const id = uid("probe");
    let onAbort = () => {};
    // Settle immediately on abort; Rust kills the process in the background.
    const aborted = new Promise<never>((_, reject) => {
      onAbort = () => {
        void invoke("cancel_job", { id }).catch(() => {});
        reject(new EngineError("canceled", "Canceled"));
      };
    });
    aborted.catch(() => {});
    signal?.addEventListener("abort", onAbort, { once: true });
    let json: string;
    try {
      json = await Promise.race([invoke<string>("probe", { id, args: probeArgs(url) }), aborted]);
    } catch (e) {
      if (signal?.aborted) throw new EngineError("canceled", "Canceled");
      throw toEngineError(e);
    } finally {
      signal?.removeEventListener("abort", onAbort);
    }
    return toMediaInfo(url, JSON.parse(json) as Record<string, unknown>);
  }

  start(spec: JobSpec, cb: JobCallbacks): RunningJob {
    let canceled = false;
    const previous = this.settled.get(spec.id) ?? Promise.resolve();

    const done = new Promise<JobResult>((resolve, reject) => {
      const t = tauri();
      if (!t) return reject(new EngineError("unknown", "Not running inside the desktop app."));

      const tracker = ProgressTracker.forJob(spec);
      const tail: string[] = [];
      let filePath: string | undefined;

      const channel = new t.core.Channel<JobEvent>();
      channel.onmessage = (ev) => {
        if (ev.type === "line") {
          const parsed = parseLine(ev.line);
          if (!parsed) return;
          if (parsed.type === "file") filePath = parsed.path;
          if (parsed.type === "log" || parsed.type === "error") {
            tail.push(ev.line);
            if (tail.length > TAIL_LINES) tail.shift();
            cb.onLog?.(ev.line);
          }
          const p = tracker.push(parsed);
          if (p) cb.onProgress(p);
          return;
        }
        // exit
        if (canceled || ev.canceled) return reject(new EngineError("canceled", "Canceled"));
        if (ev.code === 0) {
          cb.onProgress(tracker.finish());
          return resolve({ filePath });
        }
        const err = classifyError(tail.join("\n") || `yt-dlp exited with code ${ev.code ?? "?"}`);
        reject(new EngineError(err.kind, err.message));
      };

      previous
        .then(() => {
          if (canceled) throw new EngineError("canceled", "Canceled");
          return t.core.invoke<void>("start_job", {
            id: spec.id,
            args: jobArgs(spec, {
              concurrentFragments: this.opts.concurrentFragments ?? 8,
              sep: spec.outputDir.includes("\\") ? "\\" : "/",
            }),
            onEvent: channel,
          });
        })
        .then(() => {
          // Canceled while start_job was in flight (e.g. first-run yt-dlp
          // install): the job is registered now, so cancel it for real.
          if (canceled) void invoke("cancel_job", { id: spec.id }).catch(() => {});
        })
        .catch((e) => reject(canceled ? new EngineError("canceled", "Canceled") : toEngineError(e)));
    });

    const settled = done.then(
      () => undefined,
      () => undefined,
    );
    this.settled.set(spec.id, settled);
    void settled.then(() => {
      if (this.settled.get(spec.id) === settled) this.settled.delete(spec.id);
    });

    return {
      done,
      cancel: () => {
        canceled = true;
        void invoke("cancel_job", { id: spec.id }).catch(() => {});
      },
    };
  }

  versions(): Promise<EngineVersions> {
    return invoke<EngineVersions>("versions").catch(() => ({ ytdlp: null, ffmpeg: null }));
  }

  /** Installs yt-dlp if needed, then `yt-dlp --update-to stable[@version]`. */
  async selfUpdate(version?: string): Promise<EngineVersions> {
    try {
      return await invoke<EngineVersions>("self_update", { version: version ?? null });
    } catch (e) {
      throw toEngineError(e);
    }
  }

  defaultOutputDir(): Promise<string> {
    this.outputDir ??= invoke<string>("default_output_dir").catch((e) => {
      this.outputDir = null;
      throw toEngineError(e);
    });
    return this.outputDir;
  }

  async reveal(path: string): Promise<void> {
    await invoke("reveal", { path });
  }

  // ── Desktop-only extras (not part of DownloadEngine) ───────────────────

  /** Native folder picker; resolves to null when the user cancels. */
  pickFolder(start?: string): Promise<string | null> {
    return invoke<string | null>("pick_folder", { start: start ?? null });
  }

  /** Opens an http(s)/mailto link in the system browser. */
  async openUrl(url: string): Promise<void> {
    await invoke("open_url", { url });
  }

  engineInfo(): Promise<DesktopEngineInfo> {
    return invoke<DesktopEngineInfo>("engine_info");
  }

  /** Checks GitHub Releases (latest.json) for a signed app update. */
  checkAppUpdate(): Promise<AppUpdateInfo | null> {
    return invoke<AppUpdateInfo | null>("app_update_check");
  }

  /** Downloads, verifies and installs the update, then restarts the app. */
  async installAppUpdate(): Promise<void> {
    await invoke("app_update_install");
  }
}

// ── yt-dlp JSON → MediaInfo ─────────────────────────────────────────────────

const str = (v: unknown): string | null => (typeof v === "string" && v ? v : null);
const numOrNull = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);

function toMediaInfo(url: string, raw: Record<string, unknown>): MediaInfo {
  // --no-playlist usually yields a single video; if a playlist slips through
  // (e.g. a bare playlist URL), describe its first entry.
  const entries = raw._type === "playlist" && Array.isArray(raw.entries) ? raw.entries : null;
  const d = (entries?.find((e) => e && typeof e === "object") as Record<string, unknown> | undefined) ?? raw;

  const formats = (Array.isArray(d.formats) ? d.formats : []) as YtFormat[];
  return {
    url: str(d.webpage_url) ?? url,
    id: str(d.id) ?? url,
    title: str(d.title) ?? str(d.id) ?? url,
    uploader: str(d.uploader) ?? str(d.channel) ?? null,
    extractor: str(d.extractor_key) ?? str(d.extractor) ?? "generic",
    durationSec: numOrNull(d.duration),
    thumbnail: str(d.thumbnail),
    formats,
    videoFormats: filterVideoFormats(formats),
  };
}
