import { useEffect, useState } from "react";
import { Platform, View } from "react-native";
import { router } from "expo-router";
import type { EngineVersions } from "@viddown/core";

import { Button } from "@/components/Button";
import { Field, Segmented, SectionTitle, SettingRow, Toggle } from "@/components/Controls";
import { Screen } from "@/components/Screen";
import { Text } from "@/components/Text";
import { toast } from "@/components/Toast";
import { canPickFolder, getAndroidEngine, getEngine, pickOutputFolder } from "@/engine";
import { settingsStore, useSettings } from "@/state/settings";
import { useLayout } from "@/theme/theme";
import { space } from "@/theme/tokens";

export default function SettingsScreen() {
  const s = useSettings();
  const set = settingsStore.set;
  const { wide, width } = useLayout();
  const narrow = width < 520;

  const [versions, setVersions] = useState<EngineVersions | null>(null);
  const [updating, setUpdating] = useState(false);
  const [defaultDir, setDefaultDir] = useState("");

  useEffect(() => {
    void getEngine().versions().then(setVersions).catch(() => setVersions(null));
    void getEngine().defaultOutputDir().then(setDefaultDir);
  }, []);

  const update = async () => {
    setUpdating(true);
    try {
      const before = versions?.ytdlp;
      const after = await getEngine().selfUpdate();
      setVersions(after);
      toast(after.ytdlp === before ? "The engine is already up to date" : `Updated yt-dlp to ${after.ytdlp}`);
    } catch {
      toast("Couldn't reach the update server. Try again later.");
    } finally {
      setUpdating(false);
    }
  };

  return (
    <Screen title="Settings">
      <SectionTitle>Saving</SectionTitle>
      <SettingRow
        stacked
        title="Save downloads to"
        description="Files go straight into this folder. Nothing is uploaded anywhere."
        control={canPickFolder() ? <FolderPicker defaultDir={defaultDir} /> : (
          <Field
            value={s.outputDir}
            onChangeText={(t) => set({ outputDir: t, outputLabel: t })}
            placeholder={defaultDir}
            accessibilityLabel="Download folder"
            autoCapitalize="none"
            autoCorrect={false}
          />
        )}
      />

      <SectionTitle>Downloads</SectionTitle>
      <SettingRow
        stacked={narrow}
        title="At the same time"
        description="More runs faster on good connections. Fewer is kinder to phones and slow networks."
        control={
          <Segmented
            label="Downloads at the same time"
            value={String(s.concurrency) as "1" | "2" | "3" | "4"}
            onChange={(v) => set({ concurrency: Number(v) })}
            options={["1", "2", "3", "4"].map((n) => ({ value: n as "1" | "2" | "3" | "4", label: n }))}
          />
        }
      />
      {Platform.OS === "android" ? (
        <SettingRow
          title="Wi-Fi only"
          description="Wait for Wi-Fi before downloading, so mobile data isn't used."
          control={<Toggle label="Wi-Fi only" value={s.wifiOnly} onChange={(v) => set({ wifiOnly: v })} />}
        />
      ) : null}
      <SettingRow
        title="Use the viddown server when a site blocks this device"
        description="Some sites refuse requests from certain networks or countries. When that happens, viddown can retry through its server. Files stream straight to you and are never stored there."
        control={
          <Toggle
            label="Use the viddown server when blocked"
            value={s.cloudFallback}
            onChange={(v) => set({ cloudFallback: v })}
          />
        }
      />

      <SectionTitle>Engine</SectionTitle>
      <SettingRow
        stacked={narrow}
        title="Download engine"
        description="Sites change often. Updating the engine is the first thing to try when a link stops working."
        control={
          <View style={{ gap: space.sm, alignItems: narrow ? "flex-start" : "flex-end" }}>
            <View style={{ flexDirection: "row", gap: space.lg }}>
              <Text variant="small" tone="muted">
                yt-dlp <Text variant="figures">{versions?.ytdlp ?? "—"}</Text>
              </Text>
              <Text variant="small" tone="muted">
                ffmpeg <Text variant="figures">{versions?.ffmpeg ?? "—"}</Text>
              </Text>
            </View>
            <Button
              kind="secondary"
              icon="retry"
              label={updating ? "Checking" : "Check for updates"}
              busy={updating}
              onPress={() => void update()}
            />
          </View>
        }
      />
      <SettingRow
        title="Update automatically"
        description="Check once a day when the app opens. Updates are tested on the viddown server first."
        control={
          <Toggle
            label="Update the engine automatically"
            value={s.autoUpdateEngine}
            onChange={(v) => set({ autoUpdateEngine: v })}
          />
        }
      />

      <SectionTitle>Appearance</SectionTitle>
      <SettingRow
        stacked={narrow}
        title="Theme"
        control={
          <Segmented
            label="Theme"
            value={s.appearance}
            onChange={(v) => set({ appearance: v })}
            options={[
              { value: "system", label: "Match system" },
              { value: "light", label: "Light" },
              { value: "dark", label: "Dark" },
            ]}
          />
        }
      />

      {!wide ? (
        <>
          <SectionTitle>About</SectionTitle>
          <SettingRow
            title="About viddown"
            description="Credits, source code, and the studio that made it."
            control={<Button kind="secondary" label="Open" onPress={() => router.navigate("/about")} />}
          />
        </>
      ) : null}
    </Screen>
  );
}

/** Native folder chooser (desktop + Android). Empty = the engine's default folder. */
function FolderPicker({ defaultDir }: { defaultDir: string }) {
  const s = useSettings();
  const android = getAndroidEngine();
  // Android can revoke folder access (e.g. the folder was deleted or the app reinstalled).
  const lostAccess =
    !!android && s.outputDir.startsWith("content://") && !android.hasFolderAccess(s.outputDir);

  const choose = async () => {
    const picked = await pickOutputFolder(s.outputDir);
    if (picked) settingsStore.set({ outputDir: picked.value, outputLabel: picked.label });
  };

  return (
    <View style={{ gap: space.sm }}>
      <Text variant="figures" numberOfLines={2}>
        {s.outputDir ? s.outputLabel || s.outputDir : defaultDir || "Downloads/viddown"}
      </Text>
      {lostAccess ? (
        <Text variant="small" tone="danger">
          viddown can't reach this folder any more. Choose it again, or use the default.
        </Text>
      ) : null}
      <View style={{ flexDirection: "row", gap: space.sm, flexWrap: "wrap" }}>
        <Button kind="secondary" icon="folder" label="Choose folder" onPress={() => void choose()} />
        {s.outputDir ? (
          <Button
            kind="quiet"
            label="Use default"
            onPress={() => settingsStore.set({ outputDir: "", outputLabel: "" })}
          />
        ) : null}
      </View>
    </View>
  );
}
