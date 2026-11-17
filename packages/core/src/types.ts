/** Subset of a yt-dlp format dict that viddown relies on. */
export interface YtFormat {
  format_id: string;
  ext?: string;
  vcodec?: string | null;
  acodec?: string | null;
  height?: number | null;
  width?: number | null;
  fps?: number | null;
  tbr?: number | null;
  abr?: number | null;
  protocol?: string | null;
  filesize?: number | null;
  filesize_approx?: number | null;
}

export interface MediaInfo {
  url: string;
  id: string;
  title: string;
  uploader?: string | null;
  extractor: string;
  durationSec?: number | null;
  thumbnail?: string | null;
  formats: YtFormat[];
  /** filterVideoFormats(formats), best first. */
  videoFormats: YtFormat[];
}

export interface ClipRange {
  /** "HH:MM:SS" */
  start: string;
  end: string;
}

export type JobKind = "video" | "audio";

export interface JobSpec {
  id: string;
  url: string;
  title?: string;
  thumbnail?: string | null;
  kind: JobKind;
  /** yt-dlp format id for video jobs; "bestvideo" when unspecified. */
  formatId?: string;
  /** e.g. "1080p60" — for display only. */
  qualityLabel?: string;
  clip?: ClipRange | null;
  outputDir: string;
  durationSec?: number | null;
  createdAt: number;
}

export type JobStatus = "queued" | "running" | "paused" | "done" | "failed" | "canceled";

export type JobPhase = "starting" | "video" | "audio" | "merging" | "converting" | "finishing";

export interface JobProgress {
  phase: JobPhase;
  /** 0..1 across the whole job (all phases). */
  fraction: number;
  downloadedBytes?: number;
  totalBytes?: number;
  speed?: number | null;
  etaSec?: number | null;
  fragment?: { index: number; count: number } | null;
}

export type ErrorKind =
  | "network" // transient; retry locally
  | "blocked" // bot-check / 403 / geo — may succeed via the other engine
  | "unavailable" // private, removed, members-only, DRM — do not retry
  | "unsupported" // URL not supported
  | "canceled"
  | "unknown";

export interface JobError {
  kind: ErrorKind;
  message: string;
}

export interface JobState {
  spec: JobSpec;
  status: JobStatus;
  progress: JobProgress;
  attempts: number;
  error?: JobError;
  filePath?: string;
  finishedAt?: number;
  /** Engine that ran (or is running) the job. */
  engine?: string;
}
