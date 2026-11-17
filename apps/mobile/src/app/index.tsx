import { useEffect, useMemo, useRef, useState } from "react";
import { Platform, ScrollView, StyleSheet, View, type TextInputInstance } from "react-native";
import * as Clipboard from "expo-clipboard";
import { Image } from "expo-image";
import { router, useLocalSearchParams } from "expo-router";
import {
  codecLabel,
  estimateSize,
  explainError,
  formatBytes,
  formatClock,
  formatDuration,
  isJobError,
  qualityLabel,
  type MediaInfo,
  type YtFormat,
} from "@viddown/core";

import { Button } from "@/components/Button";
import { ClipPicker, type Range } from "@/components/ClipPicker";
import { Field, Segmented, SectionTitle } from "@/components/Controls";
import { Icon } from "@/components/Icon";
import { JobRow } from "@/components/JobRow";
import { Pressable } from "@/components/Pressable";
import { Text } from "@/components/Text";
import { toast } from "@/components/Toast";
import { getEngine } from "@/engine";
import { enqueue, useHistory, useQueueSummary } from "@/state/downloads";
import { useSettings } from "@/state/settings";
import { useLayout, useTheme } from "@/theme/theme";
import { fonts, layout, radius, space } from "@/theme/tokens";
import { useSafeAreaInsets } from "react-native-safe-area-context";

type Choice = { kind: "video"; format: YtFormat } | { kind: "audio" };

const URL_RE = /^https?:\/\/\S+$/i;
const MP3_KBPS = 192;

export default function DownloadScreen() {
  const { c } = useTheme();
  const { wide, split } = useLayout();
  const insets = useSafeAreaInsets();
  const settings = useSettings();

  const [url, setUrl] = useState("");
  const [info, setInfo] = useState<MediaInfo | null>(null);
  const [probing, setProbing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [choice, setChoice] = useState<Choice | null>(null);
  const [partial, setPartial] = useState<"whole" | "part">("whole");
  const [range, setRange] = useState<Range>({ start: 0, end: 0 });
  const abort = useRef<AbortController | null>(null);
  const input = useRef<TextInputInstance>(null);

  useEffect(() => () => abort.current?.abort(), []);

  // A link shared from another app arrives as ?url=… (see ShareIntentHandler).
  const { url: sharedUrl } = useLocalSearchParams<{ url?: string }>();
  useEffect(() => {
    if (!sharedUrl) return;
    setUrl(sharedUrl);
    void fetchInfo(sharedUrl);
    router.setParams({ url: undefined });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sharedUrl]);

  const fetchInfo = async (raw = url) => {
    const link = raw.trim();
    if (!URL_RE.test(link)) {
      setError("That doesn't look like a link. Copy it from the address bar or the share menu.");
      return;
    }
    abort.current?.abort();
    const ctrl = new AbortController();
    abort.current = ctrl;
    setProbing(true);
    setError(null);
    setInfo(null);
    try {
      const media = await getEngine().probe(link, ctrl.signal);
      if (ctrl.signal.aborted) return;
      setInfo(media);
      setChoice(defaultChoice(media));
      setPartial("whole");
      setRange({ start: 0, end: Math.max(1, Math.round(media.durationSec ?? 1)) });
    } catch (e) {
      if (ctrl.signal.aborted) return;
      setError(isJobError(e) ? explainError(e) : String(e));
    } finally {
      if (abort.current === ctrl) setProbing(false);
    }
  };

  const paste = async () => {
    const text = (await Clipboard.getStringAsync()).trim();
    if (!text) {
      setError("The clipboard is empty. Copy a video link first.");
      return;
    }
    setUrl(text);
    void fetchInfo(text);
  };

  const reset = () => {
    abort.current?.abort();
    setUrl("");
    setInfo(null);
    setError(null);
    setProbing(false);
    input.current?.focus();
  };

  const start = async () => {
    if (!info || !choice) return;
    const clip =
      partial === "part" ? { start: formatClock(range.start), end: formatClock(range.end) } : null;
    enqueue({
      url: info.url,
      title: info.title,
      thumbnail: info.thumbnail,
      kind: choice.kind,
      formatId: choice.kind === "video" ? choice.format.format_id : undefined,
      qualityLabel: choice.kind === "video" ? qualityLabel(choice.format) : "MP3",
      clip,
      durationSec: info.durationSec,
      outputDir: settings.outputDir || (await getEngine().defaultOutputDir()),
    });
    toast("Added to the queue", { label: "View", onPress: () => router.navigate("/queue") });
    reset();
  };

  const pad = wide ? space.xxl : space.lg;

  return (
    <View style={[styles.root, { backgroundColor: c.bg }]}>
      <ScrollView
        style={{ flex: 1 }}
        contentContainerStyle={{
          paddingHorizontal: pad,
          paddingTop: (wide ? space.xxxl : space.xl) + (wide ? 0 : insets.top),
          paddingBottom: space.xxxl,
        }}
        keyboardShouldPersistTaps="handled"
      >
        <View style={styles.column}>
          <Text variant="display" accessibilityRole="header" style={styles.headline}>
            Save a video to this device.
          </Text>

          <Field
            ref={input}
            size="lg"
            value={url}
            onChangeText={(t) => {
              setUrl(t);
              if (error) setError(null);
            }}
            onSubmitEditing={() => void fetchInfo()}
            placeholder="Paste a link"
            accessibilityLabel="Video link"
            autoCapitalize="none"
            autoCorrect={false}
            inputMode="url"
            keyboardType="url"
            returnKeyType="go"
            autoFocus={Platform.OS === "web"}
            error={error}
            hint={
              info || probing
                ? undefined
                : "Works with YouTube, Instagram, TikTok, X, Reddit, Vimeo and about 1,800 other sites."
            }
            trailing={
              url.trim() ? (
                <Button
                  kind="primary"
                  label={probing ? "Looking" : "Look up"}
                  busy={probing}
                  onPress={() => void fetchInfo()}
                />
              ) : (
                <Button kind="secondary" icon="clipboard" label="Paste" onPress={() => void paste()} />
              )
            }
          />

          {probing ? <ProbeSkeleton /> : null}

          {info && choice ? (
            <MediaPanel
              info={info}
              choice={choice}
              onChoice={setChoice}
              partial={partial}
              onPartial={setPartial}
              range={range}
              onRange={setRange}
              onStart={() => void start()}
              onClear={reset}
            />
          ) : null}

          {!info && !probing ? <Recent /> : null}
        </View>
      </ScrollView>

      {split ? <QueuePanel /> : null}
    </View>
  );
}

function defaultChoice(info: MediaInfo): Choice {
  // Best quality up to 1080p is the sensible default: sharp, and not 4K-sized.
  const pick = info.videoFormats.find((f) => (f.height ?? 0) <= 1080) ?? info.videoFormats[0];
  return pick ? { kind: "video", format: pick } : { kind: "audio" };
}

function MediaPanel({
  info,
  choice,
  onChoice,
  partial,
  onPartial,
  range,
  onRange,
  onStart,
  onClear,
}: {
  info: MediaInfo;
  choice: Choice;
  onChoice: (c: Choice) => void;
  partial: "whole" | "part";
  onPartial: (p: "whole" | "part") => void;
  range: Range;
  onRange: (r: Range) => void;
  onStart: () => void;
  onClear: () => void;
}) {
  const { c } = useTheme();
  const { width } = useLayout();
  const duration = info.durationSec ?? 0;
  const share = partial === "part" && duration ? (range.end - range.start) / duration : 1;
  const stacked = width < 560;

  const audioBytes = duration ? ((MP3_KBPS * 1000) / 8) * duration : null;
  const action =
    choice.kind === "audio"
      ? partial === "part"
        ? "Download audio clip"
        : "Download MP3"
      : partial === "part"
        ? `Download ${qualityLabel(choice.format)} clip`
        : `Download ${qualityLabel(choice.format)}`;

  return (
    <View style={{ marginTop: space.xxl }}>
      <View style={[styles.media, stacked && { flexDirection: "column" }]}>
        <View style={[styles.thumb, stacked && styles.thumbStacked, { backgroundColor: c.sunken }]}>
          {info.thumbnail ? (
            <Image
              source={info.thumbnail}
              style={StyleSheet.absoluteFill}
              contentFit="cover"
              accessibilityLabel={`Thumbnail for ${info.title}`}
            />
          ) : null}
          <View style={[styles.durationTag, { backgroundColor: c.ink }]}>
            <Text variant="figures" style={{ color: c.bg, fontSize: 12, lineHeight: 16 }}>
              {formatDuration(info.durationSec)}
            </Text>
          </View>
        </View>
        <View style={styles.mediaText}>
          <Text variant="heading" numberOfLines={3}>
            {info.title}
          </Text>
          <View style={styles.metaRow}>
            {info.uploader ? (
              <Text variant="small" tone="muted">
                {info.uploader}
              </Text>
            ) : null}
            <Text variant="small" tone="muted">
              {info.extractor}
            </Text>
          </View>
          <Pressable accessibilityRole="button" onPress={onClear} style={styles.clear}>
            <Text variant="label" tone="muted" style={{ textDecorationLine: "underline" }}>
              Use a different link
            </Text>
          </Pressable>
        </View>
      </View>

      <SectionTitle>Quality</SectionTitle>
      <View accessibilityRole="radiogroup" accessibilityLabel="Quality">
        {info.videoFormats.map((f) => {
          const bytes = estimateSize(f, info.durationSec);
          return (
            <QualityRow
              key={f.format_id}
              selected={choice.kind === "video" && choice.format.format_id === f.format_id}
              onPress={() => onChoice({ kind: "video", format: f })}
              primary={qualityLabel(f)}
              secondary={`${codecLabel(f.vcodec)} video with audio`}
              size={bytes ? `~${formatBytes(bytes * share)}` : "Size unknown"}
            />
          );
        })}
        <QualityRow
          selected={choice.kind === "audio"}
          onPress={() => onChoice({ kind: "audio" })}
          primary="Audio only"
          secondary="MP3, best quality"
          size={audioBytes ? `~${formatBytes(audioBytes * share)}` : "Size unknown"}
          icon="music"
        />
      </View>

      {duration > 1 ? (
        <>
          <SectionTitle>Length</SectionTitle>
          <Segmented
            label="How much to download"
            value={partial}
            onChange={onPartial}
            options={[
              { value: "whole", label: "Whole video" },
              { value: "part", label: "Only a part" },
            ]}
          />
          {partial === "part" ? (
            <View style={{ marginTop: space.lg }}>
              <ClipPicker duration={duration} value={range} onChange={onRange} />
            </View>
          ) : null}
        </>
      ) : null}

      <Button
        kind="primary"
        size="lg"
        icon="download"
        label={action}
        onPress={onStart}
        style={{ marginTop: space.xxl, alignSelf: stacked ? "stretch" : "flex-start" }}
      />
    </View>
  );
}

function QualityRow({
  selected,
  onPress,
  primary,
  secondary,
  size,
  icon,
}: {
  selected: boolean;
  onPress: () => void;
  primary: string;
  secondary: string;
  size: string;
  icon?: "music";
}) {
  const { c } = useTheme();
  return (
    <Pressable
      accessibilityRole="radio"
      accessibilityState={{ checked: selected }}
      accessibilityLabel={`${primary}, ${secondary}, ${size}`}
      onPress={onPress}
      style={({ hovered, pressed }) => [
        styles.qRow,
        { borderTopColor: c.rule },
        (hovered || pressed) && { backgroundColor: c.sunken },
      ]}
    >
      <View style={[styles.qMarker, { backgroundColor: selected ? c.accent : "transparent" }]} />
      <View style={styles.qPrimary}>
        {icon ? <Icon name={icon} size={16} color={selected ? c.ink : c.inkMuted} /> : null}
        {icon ? (
          <Text variant={selected ? "bodyStrong" : "body"}>{primary}</Text>
        ) : (
          <Text variant="figuresLarge" style={!selected && { fontFamily: fonts.figures }}>
            {primary}
          </Text>
        )}
      </View>
      <Text variant="small" tone="muted" style={{ flex: 1 }} numberOfLines={1}>
        {secondary}
      </Text>
      <Text variant="figures" tone={selected ? "ink" : "muted"}>
        {size}
      </Text>
    </Pressable>
  );
}

function ProbeSkeleton() {
  const { c } = useTheme();
  return (
    <View style={{ marginTop: space.xxl, gap: space.md }} accessibilityLabel="Looking up the video">
      <View style={styles.media}>
        <View style={[styles.thumb, { backgroundColor: c.sunken }]} />
        <View style={[styles.mediaText, { gap: space.sm }]}>
          <View style={[styles.bone, { width: "80%", backgroundColor: c.sunken }]} />
          <View style={[styles.bone, { width: "50%", backgroundColor: c.sunken }]} />
        </View>
      </View>
    </View>
  );
}

function Recent() {
  const done = useHistory()
    .filter((j) => j.status === "done")
    .slice(0, 5);
  if (!done.length) return null;
  return (
    <View style={{ marginTop: space.xxl }}>
      <SectionTitle
        aside={
          <Pressable accessibilityRole="link" onPress={() => router.navigate("/history")}>
            <Text variant="label" tone="muted" style={{ textDecorationLine: "underline" }}>
              All history
            </Text>
          </Pressable>
        }
      >
        Recently saved
      </SectionTitle>
      {done.map((j) => (
        <JobRow key={j.spec.id} job={j} compact />
      ))}
    </View>
  );
}

function QueuePanel() {
  const { c } = useTheme();
  const { active, failed } = useQueueSummary();
  const jobs = [...active, ...failed];
  return (
    <View style={[styles.side, { borderLeftColor: c.rule, backgroundColor: c.bg }]}>
      <ScrollView contentContainerStyle={{ padding: space.xl, paddingTop: space.xxxl }}>
        <Text variant="heading" accessibilityRole="header">
          Queue
        </Text>
        {jobs.length ? (
          <View style={{ marginTop: space.md }}>
            {jobs.map((j) => (
              <JobRow key={j.spec.id} job={j} compact />
            ))}
          </View>
        ) : (
          <Text variant="small" tone="muted" style={{ marginTop: space.sm }}>
            Downloads you start appear here, with their progress.
          </Text>
        )}
      </ScrollView>
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, flexDirection: "row" },
  column: { width: "100%", maxWidth: layout.contentMax },
  headline: { marginBottom: space.xl, maxWidth: 520 },
  media: { flexDirection: "row", gap: space.lg, alignItems: "flex-start" },
  thumb: {
    width: 220,
    aspectRatio: 16 / 9,
    borderRadius: radius.control,
    overflow: "hidden",
    justifyContent: "flex-end",
    alignItems: "flex-end",
  },
  thumbStacked: { width: "100%" },
  durationTag: { margin: space.sm, paddingHorizontal: 6, borderRadius: 4 },
  mediaText: { flex: 1, gap: space.xs, minWidth: 0 },
  metaRow: { flexDirection: "row", gap: space.md, flexWrap: "wrap" },
  clear: { alignSelf: "flex-start", marginTop: space.sm, paddingVertical: space.xs },
  qRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: space.lg,
    minHeight: 52,
    paddingRight: space.md,
    borderTopWidth: StyleSheet.hairlineWidth * 2,
  },
  qMarker: { width: 4, alignSelf: "stretch", marginVertical: 10, borderRadius: 1 },
  qPrimary: { width: 104, flexDirection: "row", alignItems: "center", gap: space.sm },
  bone: { height: 16, borderRadius: 4 },
  side: { width: layout.sidePanel, borderLeftWidth: StyleSheet.hairlineWidth * 2 },
});
