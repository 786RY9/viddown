import { toSeconds } from "./time";
import { MARK } from "./ytdlpArgs";
import type { JobKind, JobPhase, JobProgress, JobSpec } from "./types";

/** One parsed line of yt-dlp output produced with jobArgs(). */
export type EngineEvent =
  | {
      type: "download";
      status: "downloading" | "finished" | "error";
      filename?: string;
      downloaded?: number;
      total?: number;
      speed?: number | null;
      eta?: number | null;
      fragIndex?: number;
      fragCount?: number;
    }
  | { type: "postprocess"; status: string; name: string }
  | { type: "file"; path: string }
  /** ffmpeg position, e.g. while yt-dlp cuts a clip with --download-sections. */
  | { type: "ffmpeg"; seconds: number }
  | { type: "error"; message: string }
  | { type: "log"; line: string };

const num = (v: unknown): number | undefined =>
  typeof v === "number" && Number.isFinite(v) ? v : undefined;

// "frame=  120 fps= 30 ... time=00:01:05.32 bitrate=..." (not "time=N/A")
const FFMPEG_TIME_RE = /(?:^|\s)time=(\d+):(\d{2}):(\d{2}(?:\.\d+)?)/;

export function parseLine(raw: string): EngineEvent | null {
  const line = raw.trim();
  if (!line) return null;

  if (line.startsWith(MARK.progress)) {
    try {
      const p = JSON.parse(line.slice(MARK.progress.length)) as Record<string, unknown>;
      return {
        type: "download",
        status: (p.status as "downloading" | "finished" | "error") ?? "downloading",
        filename: typeof p.filename === "string" ? p.filename : undefined,
        downloaded: num(p.downloaded_bytes),
        total: num(p.total_bytes) ?? num(p.total_bytes_estimate),
        speed: num(p.speed) ?? null,
        eta: num(p.eta) ?? null,
        fragIndex: num(p.fragment_index),
        fragCount: num(p.fragment_count),
      };
    } catch {
      return { type: "log", line };
    }
  }
  if (line.startsWith(MARK.postprocess)) {
    const [status = "", ...rest] = line.slice(MARK.postprocess.length).split(" ");
    return { type: "postprocess", status, name: rest.join(" ") };
  }
  if (line.startsWith(MARK.file)) return { type: "file", path: line.slice(MARK.file.length) };
  if (line.startsWith("ERROR:")) return { type: "error", message: line };
  const t = FFMPEG_TIME_RE.exec(line);
  if (t) return { type: "ffmpeg", seconds: Number(t[1]) * 3600 + Number(t[2]) * 60 + Number(t[3]) };
  return { type: "log", line };
}

/** Postprocessors that do real, visible work (others are bookkeeping). */
const PP_PHASE: Record<string, JobPhase> = {
  Merger: "merging",
  FFmpegMerger: "merging",
  ExtractAudio: "converting",
  FFmpegExtractAudio: "converting",
};

/** Share of the bar reserved for merging / converting after download. */
export const POSTPROCESS_SHARE = 0.04;

/**
 * Relative size of each downloaded stream. Video is typically ~85% of a merged
 * download's bytes; the UI draws its phase runs with the same weights.
 */
export function streamWeights(kind: JobKind): number[] {
  return kind === "video" ? [0.85, 0.15] : [1];
}

/** Phase runs for progress UIs, aligned with ProgressTracker fractions. */
export function phaseSegments(kind: JobKind): { label: string; weight: number }[] {
  const dl = 1 - POSTPROCESS_SHARE;
  const [v = 1, a = 0] = streamWeights(kind);
  return kind === "video"
    ? [
        { label: "Video", weight: v * dl },
        { label: "Audio", weight: a * dl },
        { label: "Merge", weight: POSTPROCESS_SHARE },
      ]
    : [
        { label: "Audio", weight: dl },
        { label: "Convert", weight: POSTPROCESS_SHARE },
      ];
}

/**
 * Folds EngineEvents into a single monotonic JobProgress.
 *
 * A merged video job downloads two streams (video, then audio), weighted by
 * streamWeights(); post-processing takes the final POSTPROCESS_SHARE.
 */
export class ProgressTracker {
  private files: string[] = [];
  private current: JobProgress = { phase: "starting", fraction: 0 };
  private readonly weights: number[];
  private readonly kind: JobKind;
  /** Clip length; enables progress from ffmpeg time= lines. */
  private readonly clipSeconds: number | null;
  private ffStream = 0;
  private ffLast = 0;

  /** Tracker configured for a job (clip length included). Engines should use this. */
  static forJob(spec: Pick<JobSpec, "kind" | "clip">): ProgressTracker {
    const clipSeconds = spec.clip ? toSeconds(spec.clip.end) - toSeconds(spec.clip.start) : null;
    return new ProgressTracker(spec.kind, { clipSeconds });
  }

  constructor(kind: JobKind, opts: { clipSeconds?: number | null } = {}) {
    this.kind = kind;
    this.weights = streamWeights(kind);
    this.clipSeconds = opts.clipSeconds && opts.clipSeconds > 0 ? opts.clipSeconds : null;
  }

  private get streams(): number {
    return this.weights.length;
  }

  get progress(): JobProgress {
    return this.current;
  }

  /** Returns the new progress if the event changed it. */
  push(ev: EngineEvent): JobProgress | null {
    if (ev.type === "download") return this.onDownload(ev);
    if (ev.type === "ffmpeg") return this.onFfmpeg(ev.seconds);
    if (ev.type === "postprocess") {
      const phase = PP_PHASE[ev.name];
      if (!phase || ev.status === "finished") return null;
      return this.set({ phase, fraction: Math.max(this.current.fraction, 1 - POSTPROCESS_SHARE) });
    }
    return null;
  }

  finish(): JobProgress {
    return this.set({ phase: "finishing", fraction: 1 });
  }

  private onDownload(ev: Extract<EngineEvent, { type: "download" }>): JobProgress | null {
    if (ev.filename && !this.files.includes(ev.filename)) this.files.push(ev.filename);
    const idx = Math.max(0, this.files.length - 1);
    const streamIdx = Math.min(idx, this.streams - 1);

    let within: number | null = null;
    if (ev.status === "finished") within = 1;
    else if (ev.total && ev.downloaded != null) within = ev.downloaded / ev.total;
    else if (ev.fragCount && ev.fragIndex != null) within = ev.fragIndex / ev.fragCount;

    return this.set({
      phase: this.phaseFor(streamIdx),
      // Never move backwards; yt-dlp can briefly report smaller estimates.
      fraction: Math.max(this.current.fraction, this.fractionFor(streamIdx, within)),
      downloadedBytes: ev.downloaded,
      totalBytes: ev.total,
      speed: ev.speed ?? null,
      etaSec: ev.eta ?? null,
      fragment: ev.fragCount && ev.fragIndex != null ? { index: ev.fragIndex, count: ev.fragCount } : null,
    });
  }

  /**
   * Clips are cut by ffmpeg, which reports its position instead of bytes.
   * Each stream starts again from 0, so a backwards jump means the next one.
   */
  private onFfmpeg(seconds: number): JobProgress | null {
    if (!this.clipSeconds) return null;
    if (seconds < this.ffLast - 1) this.ffStream++;
    this.ffLast = seconds;
    const streamIdx = Math.min(this.ffStream, this.streams - 1);
    return this.set({
      ...this.current,
      phase: this.phaseFor(streamIdx),
      fraction: Math.max(this.current.fraction, this.fractionFor(streamIdx, seconds / this.clipSeconds)),
      speed: null,
      etaSec: null,
    });
  }

  private phaseFor(streamIdx: number): JobPhase {
    if (this.streams === 2) return streamIdx === 0 ? "video" : "audio";
    return this.kind === "audio" ? "audio" : "video";
  }

  private fractionFor(streamIdx: number, within: number | null): number {
    if (within == null) return this.current.fraction;
    const dl = 1 - POSTPROCESS_SHARE;
    const offset = this.weights.slice(0, streamIdx).reduce((s, w) => s + w, 0) * dl;
    const span = (this.weights[streamIdx] ?? 0) * dl;
    return Math.min(dl, offset + Math.min(1, Math.max(0, within)) * span);
  }

  private set(next: JobProgress): JobProgress {
    this.current = next;
    return next;
  }
}

/** Split a chunked stdout stream into lines (handles \r, \n and \r\n). */
export class LineSplitter {
  private buf = "";
  feed(chunk: string): string[] {
    this.buf += chunk;
    const parts = this.buf.split(/\r\n|\r|\n/);
    this.buf = parts.pop() ?? "";
    return parts.filter((l) => l.trim().length > 0);
  }
  flush(): string[] {
    const rest = this.buf.trim();
    this.buf = "";
    return rest ? [rest] : [];
  }
}
