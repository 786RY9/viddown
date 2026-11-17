import { StyleSheet, View } from "react-native";
import {
  explainError,
  formatBytes,
  formatDuration,
  formatSpeed,
  toSeconds,
  type JobState,
} from "@viddown/core";

import { getEngine } from "@/engine";
import { queue } from "@/state/downloads";
import { useTheme } from "@/theme/theme";
import { space } from "@/theme/tokens";
import { IconButton } from "./Button";
import { FrameBar, jobSegments } from "./FrameBar";
import { Text } from "./Text";

const PHASE_TEXT = {
  starting: "Starting",
  video: "Downloading video",
  audio: "Downloading audio",
  merging: "Merging video and audio",
  converting: "Converting to MP3",
  finishing: "Finishing",
} as const;

function statusText(job: JobState): string {
  switch (job.status) {
    case "queued":
      return job.error ? `Retrying soon (attempt ${job.attempts + 1})` : "Waiting";
    case "paused":
      return "Paused";
    case "canceled":
      return "Canceled";
    case "failed":
      return job.error ? explainError(job.error) : "Failed";
    case "done":
      return "Saved";
    default:
      return job.engine === "cloud"
        ? `${PHASE_TEXT[job.progress.phase]} via viddown cloud`
        : PHASE_TEXT[job.progress.phase];
  }
}

export function describeSpec(job: JobState): string[] {
  const s = job.spec;
  const parts = [s.kind === "audio" ? "MP3 audio" : (s.qualityLabel ?? "Best quality")];
  if (s.clip) parts.push(`${formatDuration(toSeconds(s.clip.start))} to ${formatDuration(toSeconds(s.clip.end))}`);
  return parts;
}

export function JobRow({ job, compact }: { job: JobState; compact?: boolean }) {
  const { c } = useTheme();
  const { status, progress } = job;
  const id = job.spec.id;
  const live = status === "running";
  const pct = Math.round(progress.fraction * 100);

  const figures: string[] = [];
  if (live || status === "paused") figures.push(`${pct}%`);
  if (live && progress.speed) figures.push(formatSpeed(progress.speed));
  if (live && progress.etaSec != null) figures.push(`${formatDuration(progress.etaSec)} left`);
  if (status === "done" && progress.totalBytes) figures.push(formatBytes(progress.totalBytes));

  return (
    <View style={[styles.row, { borderTopColor: c.rule }]}>
      <View style={styles.top}>
        <View style={styles.titleCol}>
          <Text variant="bodyStrong" numberOfLines={1}>
            {job.spec.title ?? job.spec.url}
          </Text>
          <View style={styles.meta}>
            {describeSpec(job).map((p) => (
              <Text key={p} variant="small" tone="muted" numberOfLines={1}>
                {p}
              </Text>
            ))}
          </View>
        </View>
        <View style={styles.actions}>
          {status === "running" || status === "queued" ? (
            <IconButton icon="pause" label="Pause" onPress={() => queue.pause(id)} />
          ) : null}
          {status === "paused" ? (
            <IconButton icon="play" label="Resume" onPress={() => queue.resume(id)} />
          ) : null}
          {status === "failed" || status === "canceled" ? (
            <IconButton icon="retry" label="Try again" onPress={() => queue.retry(id)} />
          ) : null}
          {status === "done" && job.filePath && getEngine().reveal ? (
            <IconButton
              icon="folder"
              label="Show in folder"
              onPress={() => void getEngine().reveal?.(job.filePath!)}
            />
          ) : null}
          {status === "done" || status === "failed" || status === "canceled" ? (
            <IconButton icon="close" label="Remove from list" onPress={() => queue.remove(id)} />
          ) : (
            <IconButton icon="close" label="Cancel download" onPress={() => queue.cancel(id)} />
          )}
        </View>
      </View>

      {status !== "done" && status !== "canceled" && (!compact || status !== "failed") ? (
        <FrameBar
          fraction={live && progress.phase === "starting" ? null : progress.fraction}
          segments={jobSegments(job.spec.kind)}
          muted={status !== "running"}
          accessibilityLabel={`${job.spec.title ?? "Download"} progress`}
        />
      ) : null}

      <View style={styles.bottom}>
        <Text
          variant="small"
          tone={status === "failed" ? "danger" : status === "done" ? "success" : "muted"}
          numberOfLines={2}
          style={{ flexShrink: 1 }}
        >
          {statusText(job)}
        </Text>
        {figures.length ? (
          <View style={styles.figures}>
            {figures.map((f) => (
              <Text key={f} variant="figures">
                {f}
              </Text>
            ))}
          </View>
        ) : null}
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  row: { paddingVertical: space.lg, gap: space.sm, borderTopWidth: StyleSheet.hairlineWidth * 2 },
  top: { flexDirection: "row", alignItems: "flex-start", gap: space.md },
  titleCol: { flex: 1, minWidth: 0, gap: 2 },
  meta: { flexDirection: "row", gap: space.md, flexWrap: "wrap" },
  actions: { flexDirection: "row", gap: 2, marginTop: -6, marginRight: -8 },
  bottom: { flexDirection: "row", justifyContent: "space-between", alignItems: "flex-start", gap: space.md },
  figures: { flexDirection: "row", gap: space.md, flexShrink: 0 },
});
