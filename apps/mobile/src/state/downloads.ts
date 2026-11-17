import { useMemo, useSyncExternalStore } from "react";
import { DownloadQueue, type JobSpec, type JobState, type QueueStorage } from "@viddown/core";

import { getCloudEngine, getEngine } from "@/engine";
import { loadJSON, saveJSON } from "./persist";
import { settingsStore } from "./settings";

const KEY = "viddown.jobs.v1";
/** Keep history bounded so storage never grows without limit. */
const MAX_FINISHED = 300;

const storage: QueueStorage = {
  load: () => loadJSON<JobState[]>(KEY, []),
  save: (jobs) => {
    const active = jobs.filter((j) => j.status !== "done");
    const done = jobs.filter((j) => j.status === "done").slice(-MAX_FINISHED);
    return saveJSON(KEY, [...done, ...active]);
  },
};

export const queue = new DownloadQueue({
  engine: getEngine(),
  fallback: null,
  concurrency: settingsStore.get().concurrency,
  storage,
});

let snapshot: readonly JobState[] = queue.snapshot;
const listeners = new Set<() => void>();
queue.subscribe((jobs) => {
  snapshot = jobs;
  listeners.forEach((fn) => fn());
});

// Keep the scheduler in step with settings.
let lastConcurrency = settingsStore.get().concurrency;
let lastFallback = settingsStore.get().cloudFallback;
settingsStore.subscribe(() => {
  const s = settingsStore.get();
  if (s.concurrency !== lastConcurrency) {
    lastConcurrency = s.concurrency;
    queue.setConcurrency(s.concurrency);
  }
  if (s.cloudFallback !== lastFallback) {
    lastFallback = s.cloudFallback;
    queue.setEngine(getEngine(), s.cloudFallback ? getCloudEngine() : null);
  }
});

const subscribe = (fn: () => void) => {
  listeners.add(fn);
  return () => {
    listeners.delete(fn);
  };
};
const get = () => snapshot;

export function useJobs(): readonly JobState[] {
  return useSyncExternalStore(subscribe, get, get);
}

/** Active = queued, running or paused. Newest last (queue order). */
export function useQueueSummary() {
  const jobs = useJobs();
  return useMemo(() => {
    const active = jobs.filter((j) => j.status === "queued" || j.status === "running" || j.status === "paused");
    const failed = jobs.filter((j) => j.status === "failed");
    return {
      active,
      failed,
      running: active.filter((j) => j.status === "running").length,
      waiting: active.filter((j) => j.status === "queued").length,
    };
  }, [jobs]);
}

/** Finished and failed jobs, newest first. */
export function useHistory() {
  const jobs = useJobs();
  return useMemo(
    () =>
      jobs
        .filter((j) => j.status === "done" || j.status === "failed" || j.status === "canceled")
        .sort((a, b) => (b.finishedAt ?? b.spec.createdAt) - (a.finishedAt ?? a.spec.createdAt)),
    [jobs],
  );
}

export function newJobId(): string {
  return Date.now().toString(36) + Math.random().toString(36).slice(2, 8);
}

export function enqueue(spec: Omit<JobSpec, "id" | "createdAt">): JobSpec {
  const full: JobSpec = { ...spec, id: newJobId(), createdAt: Date.now() };
  queue.add(full);
  return full;
}
