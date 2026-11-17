import { useMemo } from "react";
import { View } from "react-native";
import type { JobState } from "@viddown/core";

import { Button } from "@/components/Button";
import { SectionTitle } from "@/components/Controls";
import { JobRow } from "@/components/JobRow";
import { Screen } from "@/components/Screen";
import { Text } from "@/components/Text";
import { queue, useHistory } from "@/state/downloads";
import { space } from "@/theme/tokens";

const DAY = 86_400_000;

function groupByDay(jobs: readonly JobState[]) {
  const startOfToday = new Date().setHours(0, 0, 0, 0);
  const groups: { title: string; jobs: JobState[] }[] = [
    { title: "Today", jobs: [] },
    { title: "Yesterday", jobs: [] },
    { title: "Earlier", jobs: [] },
  ];
  for (const j of jobs) {
    const t = j.finishedAt ?? j.spec.createdAt;
    const g = t >= startOfToday ? 0 : t >= startOfToday - DAY ? 1 : 2;
    groups[g]!.jobs.push(j);
  }
  return groups.filter((g) => g.jobs.length);
}

export default function HistoryScreen() {
  const history = useHistory();
  const groups = useMemo(() => groupByDay(history), [history]);
  const saved = history.filter((j) => j.status === "done").length;

  return (
    <Screen
      title="History"
      subtitle={history.length ? "Kept on this device only." : undefined}
      aside={
        saved ? (
          <Button kind="quiet" label="Clear list" onPress={() => queue.clearFinished()} />
        ) : undefined
      }
    >
      {groups.length === 0 ? (
        <View style={{ gap: space.sm }}>
          <Text tone="muted">
            Nothing saved yet. Finished downloads are listed here so you can find them again.
          </Text>
        </View>
      ) : null}

      {groups.map((g, i) => (
        <View key={g.title} style={i === 0 ? { marginTop: -space.xxl } : undefined}>
          <SectionTitle>{g.title}</SectionTitle>
          {g.jobs.map((j) => (
            <JobRow key={j.spec.id} job={j} compact />
          ))}
        </View>
      ))}
    </Screen>
  );
}
