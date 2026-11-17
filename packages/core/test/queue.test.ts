import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  DownloadQueue,
  EngineError,
  MockEngine,
  type DownloadEngine,
  type JobSpec,
  type JobState,
  type QueueStorage,
} from "../src";

let n = 0;
const spec = (over: Partial<JobSpec> = {}): JobSpec => ({
  id: `job${++n}`,
  url: "https://example.com/v",
  kind: "audio",
  outputDir: "/out",
  durationSec: 10,
  createdAt: n,
  ...over,
});

const byId = (q: DownloadQueue, id: string) => q.snapshot.find((j) => j.spec.id === id)!;

beforeEach(() => vi.useFakeTimers());
afterEach(() => vi.useRealTimers());

describe("DownloadQueue", () => {
  it("never runs more than `concurrency` jobs and drains FIFO", async () => {
    const q = new DownloadQueue({ engine: new MockEngine(10), concurrency: 2 });
    let peak = 0;
    q.subscribe((jobs) => {
      peak = Math.max(peak, jobs.filter((j) => j.status === "running").length);
    });
    const specs = [spec(), spec(), spec(), spec(), spec()];
    specs.forEach((s) => q.add(s));

    expect(q.snapshot.map((j) => j.status)).toEqual(["running", "running", "queued", "queued", "queued"]);
    await vi.runAllTimersAsync();
    expect(peak).toBe(2);
    expect(q.snapshot.every((j) => j.status === "done")).toBe(true);
    expect(byId(q, specs[0]!.id).progress.fraction).toBe(1);
  });

  it("retries transient failures with backoff, then gives up", async () => {
    const q = new DownloadQueue({
      engine: new MockEngine(10),
      concurrency: 1,
      maxAttempts: 3,
      backoffMs: () => 1000,
    });
    const s = spec({ url: "https://example.com/fail-network", kind: "video" });
    q.add(s);
    await vi.runAllTimersAsync();
    const j = byId(q, s.id);
    expect(j.status).toBe("failed");
    expect(j.attempts).toBe(3);
    expect(j.error?.kind).toBe("network");
  });

  it("does not retry permanent failures", async () => {
    class PrivateEngine extends MockEngine {
      override start() {
        return { cancel() {}, done: Promise.reject(new EngineError("unavailable", "Private video")) };
      }
    }
    const engine: DownloadEngine = new PrivateEngine();
    const q = new DownloadQueue({ engine, concurrency: 1 });
    const s = spec();
    q.add(s);
    await vi.runAllTimersAsync();
    expect(byId(q, s.id)).toMatchObject({ status: "failed", attempts: 1 });
  });

  it("falls back to the secondary engine once when blocked", async () => {
    const fallback = new MockEngine(10);
    Object.defineProperty(fallback, "name", { value: "cloud" });
    const q = new DownloadQueue({ engine: new MockEngine(10), fallback, concurrency: 1 });
    const s = spec({ url: "https://example.com/fail-blocked" });
    q.add(s);
    await vi.runAllTimersAsync();
    // The mock fallback is blocked too, so the job fails — but only after trying it.
    expect(byId(q, s.id)).toMatchObject({ status: "failed", engine: "cloud", attempts: 2 });
  });

  it("pauses, resumes and cancels", async () => {
    const q = new DownloadQueue({ engine: new MockEngine(1), concurrency: 1 });
    const a = spec({ durationSec: 600 });
    const b = spec({ durationSec: 600 });
    q.add(a);
    q.add(b);
    await vi.advanceTimersByTimeAsync(300);

    q.pause(a.id);
    expect(byId(q, a.id).status).toBe("paused");
    expect(byId(q, b.id).status).toBe("running"); // freed slot is reused

    q.cancel(b.id);
    q.resume(a.id);
    expect(byId(q, a.id).status).toBe("running");
    await vi.runAllTimersAsync();
    expect(byId(q, a.id).status).toBe("done");
    expect(byId(q, b.id).status).toBe("canceled");
  });

  it("persists and re-queues interrupted jobs on hydrate", async () => {
    let saved: JobState[] = [];
    const storage: QueueStorage = {
      load: async () => saved,
      save: async (jobs) => {
        saved = structuredClone(jobs);
      },
    };
    const q1 = new DownloadQueue({ engine: new MockEngine(1), concurrency: 1, storage });
    const s = spec({ durationSec: 600 });
    q1.add(s);
    await vi.advanceTimersByTimeAsync(600);
    expect(saved[0]?.status).toBe("running");

    const q2 = new DownloadQueue({ engine: new MockEngine(10), concurrency: 1, storage });
    await q2.hydrate();
    expect(byId(q2, s.id).status).toBe("running");
    await vi.runAllTimersAsync();
    expect(byId(q2, s.id).status).toBe("done");
  });

  it("throttles progress notifications", async () => {
    const q = new DownloadQueue({ engine: new MockEngine(1), concurrency: 1, notifyEveryMs: 250 });
    let calls = 0;
    q.subscribe(() => calls++);
    q.add(spec({ durationSec: 600 }));
    calls = 0;
    await vi.advanceTimersByTimeAsync(1000); // ~10 progress events
    expect(calls).toBeLessThanOrEqual(5);
    expect(calls).toBeGreaterThan(0);
  });
});
