import { EngineError, type DownloadEngine, type JobCallbacks, type RunningJob } from "./engine";
import { filterVideoFormats } from "./formats";
import { ProgressTracker } from "./progress";
import { toSeconds } from "./time";
import type { JobSpec, MediaInfo, YtFormat } from "./types";

const FORMATS: YtFormat[] = [
  { format_id: "401", vcodec: "av01.0.12M.08", height: 2160, fps: 60, tbr: 18000, protocol: "https", ext: "mp4" },
  { format_id: "400", vcodec: "av01.0.08M.08", height: 1440, fps: 60, tbr: 9000, protocol: "https", ext: "mp4" },
  { format_id: "299", vcodec: "avc1.64002a", height: 1080, fps: 60, tbr: 4800, protocol: "https", ext: "mp4" },
  { format_id: "298", vcodec: "avc1.4d4020", height: 720, fps: 60, tbr: 2600, protocol: "https", ext: "mp4" },
  { format_id: "135", vcodec: "avc1.4d401f", height: 480, fps: 30, tbr: 1100, protocol: "https", ext: "mp4" },
  { format_id: "134", vcodec: "avc1.4d401e", height: 360, fps: 30, tbr: 600, protocol: "https", ext: "mp4" },
  { format_id: "251", vcodec: "none", acodec: "opus", abr: 160, tbr: 160, protocol: "https", ext: "webm" },
];

function delay(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) return reject(new EngineError("canceled", "Canceled"));
    const t = setTimeout(resolve, ms);
    signal?.addEventListener(
      "abort",
      () => {
        clearTimeout(t);
        reject(new EngineError("canceled", "Canceled"));
      },
      { once: true },
    );
  });
}

/** Simulated stream sizes (bytes) for a job: [video, audio] or [audio]. */
export function mockStreams(spec: JobSpec): number[] {
  const secs = spec.clip
    ? toSeconds(spec.clip.end) - toSeconds(spec.clip.start)
    : (spec.durationSec ?? 600);
  const audio = (160_000 / 8) * secs;
  return spec.kind === "audio" ? [audio] : [(2_600_000 / 8) * secs, audio];
}

/**
 * Deterministic fake engine for UI work and tests. It emits the same events a
 * real yt-dlp run would, through the real ProgressTracker.
 *
 * URLs containing "fail-network", "fail-blocked" or "private" exercise the
 * error paths. `speed` scales simulated time (higher = faster).
 */
export class MockEngine implements DownloadEngine {
  readonly name = "mock" as const;

  constructor(
    private readonly speed = 1,
    private readonly bytesPerSec = 6 * 1024 * 1024,
  ) {}

  async probe(url: string, signal?: AbortSignal): Promise<MediaInfo> {
    await delay(700 / this.speed, signal);
    if (!/^https?:\/\//i.test(url)) throw new EngineError("unsupported", "That isn't a link.");
    if (url.includes("private")) throw new EngineError("unavailable", "Private video");
    return {
      url,
      id: "mock" + Math.abs(hash(url)),
      title: "Big Buck Bunny — Official Blender Foundation Short Film",
      uploader: "Blender",
      extractor: new URL(url).hostname.replace(/^www\./, ""),
      durationSec: 634,
      thumbnail: null,
      formats: FORMATS,
      videoFormats: filterVideoFormats(FORMATS),
    };
  }

  start(spec: JobSpec, cb: JobCallbacks): RunningJob {
    const ctrl = new AbortController();
    return { cancel: () => ctrl.abort(), done: this.run(spec, cb, ctrl.signal) };
  }

  private async run(spec: JobSpec, cb: JobCallbacks, signal: AbortSignal) {
    const tracker = ProgressTracker.forJob(spec);
    const streams = mockStreams(spec);
    const tick = 100;

    for (const [i, total] of streams.entries()) {
      const filename = `${spec.id}.f${i}`;
      let done = 0;
      while (done < total) {
        await delay(tick / this.speed, signal);
        done = Math.min(total, done + (this.bytesPerSec * tick) / 1000);
        if (spec.url.includes("fail-network") && i === 0 && done > total / 3) {
          throw new EngineError("network", "Connection reset by peer");
        }
        if (spec.url.includes("fail-blocked")) {
          throw new EngineError("blocked", "Sign in to confirm you're not a bot");
        }
        const p = tracker.push({
          type: "download",
          status: done >= total ? "finished" : "downloading",
          filename,
          downloaded: done,
          total,
          speed: this.bytesPerSec,
          eta: (total - done) / this.bytesPerSec,
        });
        if (p) cb.onProgress(p);
      }
    }

    const pp = spec.kind === "audio" ? "ExtractAudio" : "Merger";
    const p = tracker.push({ type: "postprocess", status: "started", name: pp });
    if (p) cb.onProgress(p);
    await delay(600 / this.speed, signal);
    cb.onProgress(tracker.finish());

    const ext = spec.kind === "audio" ? "mp3" : "mp4";
    return { filePath: `${spec.outputDir}/${spec.title ?? spec.id}.${ext}` };
  }

  async versions() {
    return { ytdlp: "2026.11.10", ffmpeg: "7.1" };
  }

  async selfUpdate() {
    await delay(1200 / this.speed);
    return this.versions();
  }

  async defaultOutputDir() {
    return "~/Downloads/viddown";
  }
}

function hash(s: string): number {
  let h = 0;
  for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) | 0;
  return h;
}
