import { classifyError, isRetryable } from "./errors";
import { isJobError, type DownloadEngine, type RunningJob } from "./engine";
import type { JobError, JobSpec, JobState } from "./types";

export interface QueueStorage {
  load(): Promise<JobState[]>;
  save(jobs: JobState[]): Promise<void>;
}

export interface QueueOptions {
  engine: DownloadEngine;
  /** Max simultaneous downloads. */
  concurrency: number;
  /** Total attempts for transient failures (network / unknown). */
  maxAttempts?: number;
  /** Delay before retry n (1-based). */
  backoffMs?: (retry: number) => number;
  storage?: QueueStorage;
  /** Second engine tried once when the primary is blocked (e.g. cloud). */
  fallback?: DownloadEngine | null;
  canFallback?: (spec: JobSpec, err: JobError) => boolean;
  /** Progress notifications are coalesced to at most one per this many ms. */
  notifyEveryMs?: number;
}

type Listener = (jobs: readonly JobState[]) => void;

const DEFAULT_BACKOFF = (n: number) => [2_000, 8_000, 30_000][n - 1] ?? 60_000;
const TERMINAL = new Set(["done", "failed", "canceled"]);

/**
 * Client-side download scheduler.
 *
 * - At most `concurrency` jobs run; the rest wait FIFO.
 * - Transient errors are retried with backoff (yt-dlp --continue resumes partial files).
 * - "blocked" errors are retried once on the fallback engine, if allowed.
 * - Running jobs are re-queued after an app restart (hydrate).
 */
export class DownloadQueue {
  private jobs: JobState[] = [];
  private running = new Map<string, RunningJob>();
  private timers = new Map<string, ReturnType<typeof setTimeout>>();
  private listeners = new Set<Listener>();
  private notifyTimer: ReturnType<typeof setTimeout> | null = null;
  private saveTimer: ReturnType<typeof setTimeout> | null = null;
  private opts: Required<Omit<QueueOptions, "storage" | "fallback">> &
    Pick<QueueOptions, "storage" | "fallback">;

  constructor(opts: QueueOptions) {
    this.opts = {
      maxAttempts: 3,
      backoffMs: DEFAULT_BACKOFF,
      canFallback: () => true,
      notifyEveryMs: 100,
      ...opts,
    };
  }

  // ── Public API ──────────────────────────────────────────────────────────

  get snapshot(): readonly JobState[] {
    return this.jobs;
  }

  get concurrency(): number {
    return this.opts.concurrency;
  }

  subscribe(fn: Listener): () => void {
    this.listeners.add(fn);
    fn(this.jobs);
    return () => {
      this.listeners.delete(fn);
    };
  }

  async hydrate(): Promise<void> {
    const saved = (await this.opts.storage?.load()) ?? [];
    const known = new Set(this.jobs.map((j) => j.spec.id));
    const restored = saved
      .filter((j) => !known.has(j.spec.id))
      .map((j): JobState =>
        j.status === "running"
          ? { ...j, status: "queued", progress: { ...j.progress, speed: null, etaSec: null } }
          : j,
      );
    this.jobs = [...restored, ...this.jobs];
    this.changed(true);
    this.pump();
  }

  add(spec: JobSpec): void {
    if (this.find(spec.id)) return;
    this.jobs = [
      ...this.jobs,
      { spec, status: "queued", attempts: 0, progress: { phase: "starting", fraction: 0 } },
    ];
    this.changed(true);
    this.pump();
  }

  cancel(id: string): void {
    this.stop(id, "canceled");
  }

  /** Stop now; resume() continues from the partial file. */
  pause(id: string): void {
    this.stop(id, "paused");
  }

  resume(id: string): void {
    const j = this.find(id);
    if (!j || !(j.status === "paused" || j.status === "failed" || j.status === "canceled")) return;
    this.update(id, {
      status: "queued",
      error: undefined,
      attempts: j.status === "paused" ? j.attempts : 0,
    });
    this.pump();
  }

  retry(id: string): void {
    this.resume(id);
  }

  remove(id: string): void {
    this.stop(id, "canceled");
    this.jobs = this.jobs.filter((j) => j.spec.id !== id);
    this.changed(true);
  }

  clearFinished(): void {
    this.jobs = this.jobs.filter((j) => j.status !== "done");
    this.changed(true);
  }

  setConcurrency(n: number): void {
    this.opts.concurrency = Math.max(1, Math.floor(n));
    this.pump();
  }

  setEngine(engine: DownloadEngine, fallback?: DownloadEngine | null): void {
    this.opts.engine = engine;
    if (fallback !== undefined) this.opts.fallback = fallback;
  }

  // ── Scheduling ──────────────────────────────────────────────────────────

  private pump(): void {
    let free = this.opts.concurrency - this.running.size;
    for (const job of this.jobs) {
      if (free <= 0) break;
      const id = job.spec.id;
      if (job.status !== "queued" || this.timers.has(id) || this.running.has(id)) continue;
      this.launch(job, this.opts.engine);
      free--;
    }
  }

  private launch(job: JobState, engine: DownloadEngine): void {
    const id = job.spec.id;
    this.update(id, {
      status: "running",
      attempts: job.attempts + 1,
      error: undefined,
      engine: engine.name,
    });

    let run: RunningJob;
    try {
      run = engine.start(job.spec, {
        onProgress: (progress) => this.update(id, { progress }, false),
      });
    } catch (e) {
      this.onFailure(id, engine, toJobError(e));
      return;
    }
    this.running.set(id, run);

    run.done.then(
      (res) => {
        if (this.running.get(id) !== run) return; // already stopped
        this.running.delete(id);
        const prev = this.find(id)?.progress;
        this.update(id, {
          status: "done",
          filePath: res.filePath,
          finishedAt: Date.now(),
          // Keep byte counts for display; drop live-only figures.
          progress: { ...prev, phase: "finishing", fraction: 1, speed: null, etaSec: null },
        });
        this.pump();
      },
      (e: unknown) => {
        if (this.running.get(id) !== run) return; // stopped by pause/cancel
        this.running.delete(id);
        this.onFailure(id, engine, toJobError(e));
        this.pump();
      },
    );
  }

  private onFailure(id: string, engine: DownloadEngine, error: JobError): void {
    const job = this.find(id);
    if (!job) return;
    const { fallback, canFallback, maxAttempts, backoffMs } = this.opts;

    if (error.kind === "blocked" && fallback && engine !== fallback && canFallback(job.spec, error)) {
      this.launch(job, fallback);
      return;
    }

    if (isRetryable(error) && job.attempts < maxAttempts) {
      this.update(id, { status: "queued", error });
      const t = setTimeout(() => {
        this.timers.delete(id);
        this.pump();
      }, backoffMs(job.attempts));
      this.timers.set(id, t);
      return;
    }

    this.update(id, {
      status: "failed",
      error,
      progress: { ...job.progress, speed: null, etaSec: null },
    });
  }

  private stop(id: string, status: "paused" | "canceled"): void {
    const job = this.find(id);
    if (!job || TERMINAL.has(job.status)) return;
    const t = this.timers.get(id);
    if (t) clearTimeout(t);
    this.timers.delete(id);
    const run = this.running.get(id);
    this.running.delete(id);
    run?.cancel();
    this.update(id, { status, progress: { ...job.progress, speed: null, etaSec: null } });
    this.pump();
  }

  // ── State plumbing ──────────────────────────────────────────────────────

  private find(id: string): JobState | undefined {
    return this.jobs.find((j) => j.spec.id === id);
  }

  private update(id: string, patch: Partial<JobState>, important = true): void {
    this.jobs = this.jobs.map((j) => (j.spec.id === id ? { ...j, ...patch } : j));
    this.changed(important);
  }

  private changed(important: boolean): void {
    if (important) {
      this.flushNotify();
      this.scheduleSave();
      return;
    }
    this.notifyTimer ??= setTimeout(() => this.flushNotify(), this.opts.notifyEveryMs);
  }

  private flushNotify(): void {
    if (this.notifyTimer) clearTimeout(this.notifyTimer);
    this.notifyTimer = null;
    for (const fn of this.listeners) fn(this.jobs);
  }

  private scheduleSave(): void {
    const storage = this.opts.storage;
    if (!storage || this.saveTimer) return;
    this.saveTimer = setTimeout(() => {
      this.saveTimer = null;
      void storage.save(this.jobs).catch(() => {});
    }, 500);
  }
}

function toJobError(e: unknown): JobError {
  if (isJobError(e)) return { kind: e.kind, message: e.message };
  return classifyError(e instanceof Error ? e.message : String(e));
}
