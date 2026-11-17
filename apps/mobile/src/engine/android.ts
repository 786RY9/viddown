import {
  EngineError,
  ProgressTracker,
  classifyError,
  filterVideoFormats,
  formatDuration,
  formatSpeed,
  jobArgs,
  parseLine,
  probeArgs,
  type DownloadEngine,
  type EngineVersions,
  type JobCallbacks,
  type JobPhase,
  type JobProgress,
  type JobResult,
  type JobSpec,
  type MediaInfo,
  type RunningJob,
  type YtFormat,
} from "@viddown/core";
import type { EventSubscription } from "expo";

import ViddownEngine, {
  addSharedUrlListener,
  takeSharedUrl,
  type Conditions,
  type PickedFolder,
  type UpdateChannel,
  type ViddownEngineNative,
} from "../../modules/viddown-engine";

/**
 * Android engine: youtubedl-android (Python + yt-dlp + ffmpeg) via the local
 * `viddown-engine` Expo module.
 *
 * argv is built here with @viddown/core (`probeArgs`, `jobArgs`); Kotlin runs it
 * inside a dataSync foreground service, streams every stdout/stderr line back as
 * `onOutput` events, and copies the finished file from app-specific staging into
 * the user's SAF folder (or Download/viddown). Parsing, progress and error
 * classification are core's `parseLine`, `ProgressTracker` and `classifyError`.
 *
 * The native module is optional: on web/iOS (or Expo Go) it is null and every
 * call rejects, so importing this file is always safe.
 */

export { addSharedUrlListener, takeSharedUrl };
export type { PickedFolder };

/** True when the native engine is linked into this build. */
export function isAndroidEngineAvailable(): boolean {
  return ViddownEngine !== null;
}

function native(): ViddownEngineNative {
  if (!ViddownEngine) throw new EngineError("unknown", "The Android download engine isn't available in this build.");
  return ViddownEngine;
}

const canceledError = () => new EngineError("canceled", "Canceled");

/** Native rejections carry `code` (see EngineErrorCode); turn them into JobErrors. */
function toEngineError(e: unknown): EngineError {
  if (e instanceof EngineError) return e;
  const code = typeof e === "object" && e !== null && "code" in e ? String((e as { code: unknown }).code) : "";
  const msg = e instanceof Error ? e.message : typeof e === "string" ? e : String(e);
  if (code === "E_CANCELED") return canceledError();
  // yt-dlp output (E_YTDLP) gets the shared classifier; storage / permission
  // problems keep their own wording and stay "unknown" (cheap to retry: the
  // download is already in staging, only the copy runs again).
  if (code === "E_YTDLP" || code === "") {
    const { kind, message } = classifyError(msg);
    return new EngineError(kind, message);
  }
  return new EngineError("unknown", msg);
}

let seq = 0;
const uid = (prefix: string) => `${prefix}-${Date.now().toString(36)}-${(seq++).toString(36)}`;

/** Lines kept for error classification. */
const TAIL_LINES = 40;
/** Notification refresh per job (the native side throttles too). */
const NOTIFY_EVERY_MS = 1000;

const PHASE_LABEL: Record<JobPhase, string> = {
  starting: "Starting",
  video: "Video",
  audio: "Audio",
  merging: "Merging",
  converting: "Converting",
  finishing: "Saving",
};

function notificationText(p: JobProgress): string {
  const parts = [PHASE_LABEL[p.phase], `${Math.floor(p.fraction * 100)}%`];
  if (p.speed && (p.phase === "video" || p.phase === "audio")) parts.push(formatSpeed(p.speed));
  if (p.etaSec != null && p.etaSec > 0 && p.etaSec < 360_000) parts.push(`${formatDuration(p.etaSec)} left`);
  return parts.join(" · ");
}

// ── Output routing: one native subscription, fanned out by job id ───────────

type LineHandler = (lines: string[]) => void;
const handlers = new Map<string, LineHandler>();
let outputSub: EventSubscription | null = null;

function route(jobId: string, handler: LineHandler): () => void {
  outputSub ??= native().addListener("onOutput", ({ jobId: id, lines }) => handlers.get(id)?.(lines));
  handlers.set(jobId, handler);
  return () => {
    if (handlers.get(jobId) === handler) handlers.delete(jobId);
  };
}

// ── Engine ──────────────────────────────────────────────────────────────────

export interface AndroidEngineOptions {
  /**
   * Fixed `--concurrent-fragments`. When unset it adapts per job: 4, or 2 on a
   * metered network / battery saver / low battery.
   */
  concurrentFragments?: number;
  /** yt-dlp update channel for selfUpdate (default "stable"). */
  updateChannel?: UpdateChannel;
}

export class AndroidEngine implements DownloadEngine {
  readonly name = "android" as const;

  /** Settles when the previous native run for an id has fully finished. */
  private settled = new Map<string, Promise<unknown>>();
  private permissionsAsked: Promise<unknown> | null = null;

  constructor(private opts: AndroidEngineOptions = {}) {}

  /** Override (number) or restore adaptive behaviour (null). Applies to jobs started afterwards. */
  setConcurrentFragments(n: number | null): void {
    this.opts = { ...this.opts, concurrentFragments: n == null ? undefined : Math.max(1, Math.floor(n)) };
  }

  /** Network / battery signals behind the adaptive fragment count. */
  conditions(): Conditions | null {
    try {
      return native().getConditions();
    } catch {
      return null;
    }
  }

  concurrentFragments(): number {
    if (this.opts.concurrentFragments) return this.opts.concurrentFragments;
    const c = this.conditions();
    if (!c) return 4;
    const lowBattery = c.batteryLevel != null && c.batteryLevel <= 20 && !c.charging;
    return c.metered || c.powerSave || lowBattery ? 2 : 4;
  }

  async probe(url: string, signal?: AbortSignal): Promise<MediaInfo> {
    if (signal?.aborted) throw canceledError();
    const id = uid("probe");
    const onAbort = () => void ViddownEngine?.cancel(id).catch(() => {});
    signal?.addEventListener("abort", onAbort, { once: true });
    let json: string;
    try {
      json = await native().probe(id, probeArgs(url));
    } catch (e) {
      if (signal?.aborted) throw canceledError();
      throw toEngineError(e);
    } finally {
      signal?.removeEventListener("abort", onAbort);
    }
    let raw: Record<string, unknown>;
    try {
      raw = JSON.parse(json) as Record<string, unknown>;
    } catch {
      throw new EngineError("unknown", "yt-dlp returned unreadable metadata.");
    }
    return toMediaInfo(url, raw);
  }

  start(spec: JobSpec, cb: JobCallbacks): RunningJob {
    let canceled = false;
    const previous = this.settled.get(spec.id) ?? Promise.resolve();

    const done = (async (): Promise<JobResult> => {
      await previous;
      if (canceled) throw canceledError();
      const mod = native();

      // Notification permission (13+) / legacy storage (≤9): asked once, in
      // parallel with the download; denial only hides the notification.
      this.permissionsAsked ??= mod.requestPermissions().catch(() => null);

      // forJob passes the clip length so ffmpeg time= lines (clips) become progress.
      const tracker = ProgressTracker.forJob(spec);
      const tail: string[] = [];
      let lastNotify = 0;
      let lastPhase: JobPhase | null = null;

      const unroute = route(spec.id, (lines) => {
        for (const line of lines) {
          const ev = parseLine(line);
          if (!ev) continue;
          if (ev.type === "log" || ev.type === "error") {
            tail.push(line);
            if (tail.length > TAIL_LINES) tail.shift();
            cb.onLog?.(line);
          }
          const p = tracker.push(ev);
          if (!p) continue;
          cb.onProgress(p);
          const now = Date.now();
          if (p.phase !== lastPhase || now - lastNotify >= NOTIFY_EVERY_MS) {
            lastPhase = p.phase;
            lastNotify = now;
            mod.setJobProgress(spec.id, p.fraction, notificationText(p));
          }
        }
      });

      try {
        // yt-dlp writes into app-specific staging; Kotlin copies the result to
        // spec.outputDir (a SAF tree URI) or Download/viddown when it isn't one.
        const staged: JobSpec = { ...spec, outputDir: mod.stagingDirFor(spec.id) };
        const args = jobArgs(staged, { concurrentFragments: this.concurrentFragments(), sep: "/" });
        const destination = spec.outputDir.startsWith("content://") ? spec.outputDir : null;
        const result = await mod.start(spec.id, args, spec.title ?? spec.url, destination);
        if (canceled) throw canceledError();
        cb.onProgress(tracker.finish());
        return { filePath: result.files[0]?.uri };
      } catch (e) {
        if (canceled) throw canceledError();
        const err = toEngineError(e);
        // Prefer the streamed tail when the native message is empty.
        if (err.kind === "unknown" && !err.message && tail.length) {
          const c = classifyError(tail.join("\n"));
          throw new EngineError(c.kind, c.message);
        }
        throw err;
      } finally {
        unroute();
      }
    })().catch((e: unknown) => {
      throw canceled ? canceledError() : toEngineError(e);
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
        void ViddownEngine?.cancel(spec.id).catch(() => {});
      },
    };
  }

  versions(): Promise<EngineVersions> {
    if (!ViddownEngine) return Promise.resolve({ ytdlp: null, ffmpeg: null });
    return ViddownEngine.versions().catch(() => ({ ytdlp: null, ffmpeg: null }));
  }

  /** `version` pins to an exact yt-dlp tag (the manifest's server-validated one). */
  async selfUpdate(version?: string): Promise<EngineVersions> {
    try {
      const r = await native().selfUpdate(this.opts.updateChannel ?? "stable", version ?? null);
      return { ytdlp: r.ytdlp, ffmpeg: r.ffmpeg };
    } catch (e) {
      throw toEngineError(e);
    }
  }

  /** Label for "no folder picked": files go to Download/viddown via MediaStore. */
  async defaultOutputDir(): Promise<string> {
    return ViddownEngine?.defaultFolderLabel ?? "Download/viddown";
  }

  /** Opens a finished file (content:// URI from JobResult.filePath) in another app. */
  async reveal(path: string): Promise<void> {
    try {
      await native().openFile(path);
    } catch (e) {
      throw toEngineError(e);
    }
  }

  // ── Android-only extras (not part of DownloadEngine) ──────────────────────

  /**
   * System folder picker (Storage Access Framework). Resolves { uri, name } with
   * a persisted write grant, or null if the user backs out. Store `uri` as
   * settings.outputDir; show `name`.
   */
  async pickFolder(current?: string | null): Promise<PickedFolder | null> {
    const mod = native();
    const picked = await mod.pickFolder(current?.startsWith("content://") ? current : null);
    if (picked && current && current !== picked.uri && current.startsWith("content://")) {
      mod.releaseFolder(current);
    }
    return picked;
  }

  /** False when a previously picked folder's grant is gone (re-pick needed). */
  hasFolderAccess(uri: string): boolean {
    if (!uri.startsWith("content://")) return true;
    return ViddownEngine?.hasFolderAccess(uri) ?? false;
  }

  /**
   * Frees partial downloads that will never resume. Pass the ids of jobs that
   * may still resume (queued / paused / failed); call after hydrating the queue
   * and when the user clears canceled jobs.
   */
  cleanStaging(keepIds: string[]): Promise<number> {
    return ViddownEngine?.cleanStaging(keepIds).catch(() => 0) ?? Promise.resolve(0);
  }

  /** Ids still running natively, e.g. after a JS reload (start() re-attaches). */
  runningJobs(): string[] {
    return ViddownEngine?.runningJobs() ?? [];
  }

  /** Unpacks Python/ffmpeg ahead of the first download (first launch takes a few seconds). */
  warmUp(): Promise<void> {
    return ViddownEngine?.init().catch(() => {}) ?? Promise.resolve();
  }
}

// ── yt-dlp JSON → MediaInfo ─────────────────────────────────────────────────

const str = (v: unknown): string | null => (typeof v === "string" && v ? v : null);
const numOrNull = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);

function toMediaInfo(url: string, raw: Record<string, unknown>): MediaInfo {
  // --no-playlist usually yields a single video; if a playlist slips through,
  // describe its first entry.
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
