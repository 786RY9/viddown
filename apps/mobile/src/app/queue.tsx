import { View } from "react-native";
import { router } from "expo-router";

import { Button } from "@/components/Button";
import { SectionTitle } from "@/components/Controls";
import { JobRow } from "@/components/JobRow";
import { Screen } from "@/components/Screen";
import { Text } from "@/components/Text";
import { queue, useQueueSummary } from "@/state/downloads";
import { useSettings } from "@/state/settings";
import { space } from "@/theme/tokens";

export default function QueueScreen() {
  const { active, failed, running, waiting } = useQueueSummary();
  const { concurrency } = useSettings();
  const paused = active.length - running - waiting;

  const summary = [
    running && `${running} downloading`,
    waiting && `${waiting} waiting`,
    paused && `${paused} paused`,
  ].filter(Boolean) as string[];

  return (
    <Screen
      title="Queue"
      subtitle={
        summary.length
          ? `${summary.join(", ")}. Up to ${concurrency} run at once.`
          : undefined
      }
      aside={
        running + waiting > 1 ? (
          <Button
            kind="secondary"
            icon="pause"
            label="Pause all"
            onPress={() => active.forEach((j) => queue.pause(j.spec.id))}
          />
        ) : undefined
      }
    >
      {active.length === 0 && failed.length === 0 ? (
        <View style={{ gap: space.lg, alignItems: "flex-start" }}>
          <Text tone="muted">
            Nothing in the queue. Paste a link on the Download tab and it will show up here.
          </Text>
          <Button kind="primary" icon="download" label="Paste a link" onPress={() => router.navigate("/")} />
        </View>
      ) : null}

      {active.map((j) => (
        <JobRow key={j.spec.id} job={j} />
      ))}

      {failed.length ? (
        <>
          <SectionTitle>Didn't finish</SectionTitle>
          {failed.map((j) => (
            <JobRow key={j.spec.id} job={j} />
          ))}
        </>
      ) : null}
    </Screen>
  );
}
