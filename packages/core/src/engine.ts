import type { JobError, JobProgress, JobSpec, MediaInfo } from "./types";

export interface JobCallbacks {
  onProgress(p: JobProgress): void;
  onLog?(line: string): void;
}

export interface JobResult {
  filePath?: string;
}

export interface RunningJob {
  /** Resolves on success, rejects with a JobError. */
  done: Promise<JobResult>;
  /** Stop the process. Partial files are kept so a later run can resume. */
  cancel(): void;
}

export interface EngineVersions {
  ytdlp: string | null;
  ffmpeg: string | null;
}

/**
 * Where downloads actually run. Implemented by:
 *  - TauriEngine   (desktop: yt-dlp + ffmpeg sidecars)
 *  - AndroidEngine (youtubedl-android via a local Expo module)
 *  - CloudEngine   (server fallback, allow-listed sites only)
 *  - MockEngine    (UI development and tests)
 */
export interface DownloadEngine {
  readonly name: "desktop" | "android" | "cloud" | "mock";
  probe(url: string, signal?: AbortSignal): Promise<MediaInfo>;
  start(spec: JobSpec, cb: JobCallbacks): RunningJob;
  versions(): Promise<EngineVersions>;
  /** Update yt-dlp; `version` pins to a specific release (e.g. the manifest's). */
  selfUpdate(version?: string): Promise<EngineVersions>;
  /** Folder the user picks by default. */
  defaultOutputDir(): Promise<string>;
  /** Show the finished file in Explorer / Finder / Files. */
  reveal?(path: string): Promise<void>;
}

export class EngineError extends Error implements JobError {
  constructor(
    public readonly kind: JobError["kind"],
    message: string,
  ) {
    super(message);
    this.name = "EngineError";
  }
}

export function isJobError(e: unknown): e is JobError {
  return typeof e === "object" && e !== null && "kind" in e && "message" in e;
}
