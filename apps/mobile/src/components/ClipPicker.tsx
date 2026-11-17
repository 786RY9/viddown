import { useEffect, useMemo, useRef, useState } from "react";
import { PanResponder, StyleSheet, View, type LayoutChangeEvent } from "react-native";
import { formatDuration, parseTime, toSeconds } from "@viddown/core";

import { useTheme } from "@/theme/theme";
import { radius, space } from "@/theme/tokens";
import { Field } from "./Controls";
import { Text } from "./Text";

export interface Range {
  start: number;
  end: number;
}

const TICK_STEPS = [1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600];
const FRAME_W = 3;
const FRAME_GAP = 2;
const MIN_LEN = 1;

/**
 * Clip range picker: a timecode ruler over a strip of frames. The kept part
 * of the video lights up, handles drag, and exact times can be typed below.
 */
export function ClipPicker({
  duration,
  value,
  onChange,
}: {
  duration: number;
  value: Range;
  onChange: (r: Range) => void;
}) {
  const { c } = useTheme();
  const [width, setWidth] = useState(0);

  // Pan responders are created once; read fresh props through this ref.
  const latest = useRef({ value, width, duration, onChange });
  latest.current = { value, width, duration, onChange };

  const ticks = useMemo(() => {
    if (!width || !duration) return [];
    const target = Math.max(2, Math.floor(width / 90));
    const step = TICK_STEPS.find((s) => duration / s <= target) ?? 3600;
    const out: number[] = [];
    for (let t = 0; t <= duration; t += step) out.push(t);
    return out;
  }, [width, duration]);

  const frames = width ? Math.floor((width + FRAME_GAP) / (FRAME_W + FRAME_GAP)) : 0;
  const x = (sec: number) => (duration ? (sec / duration) * width : 0);

  const makeResponder = (edge: "start" | "end") => {
    let origin = 0;
    return PanResponder.create({
      onStartShouldSetPanResponder: () => true,
      onMoveShouldSetPanResponder: () => true,
      onPanResponderTerminationRequest: () => false,
      onPanResponderGrant: () => {
        origin = latest.current.value[edge];
      },
      onPanResponderMove: (_, g) => {
        const { value: v, width: w, duration: d, onChange: emit } = latest.current;
        if (!w) return;
        const sec = Math.round(origin + (g.dx / w) * d);
        emit(clampRange(v, edge, sec, d));
      },
    });
  };
  const startPan = useMemo(() => makeResponder("start"), []); // eslint-disable-line react-hooks/exhaustive-deps
  const endPan = useMemo(() => makeResponder("end"), []); // eslint-disable-line react-hooks/exhaustive-deps

  const onLayout = (e: LayoutChangeEvent) => setWidth(Math.floor(e.nativeEvent.layout.width));

  const handle = (edge: "start" | "end") => (
    <View
      {...(edge === "start" ? startPan : endPan).panHandlers}
      accessible
      accessibilityRole="adjustable"
      accessibilityLabel={edge === "start" ? "Clip start" : "Clip end"}
      accessibilityValue={{ text: formatDuration(value[edge]) }}
      accessibilityActions={[{ name: "increment" }, { name: "decrement" }]}
      onAccessibilityAction={(e) => {
        const d = e.nativeEvent.actionName === "increment" ? 1 : -1;
        onChange(clampRange(value, edge, value[edge] + d, duration));
      }}
      style={[
        styles.handleHit,
        { left: x(value[edge]) - 16 },
        // Grab cursor on desktop.
        { cursor: "ew-resize" } as object,
      ]}
    >
      <View style={[styles.handle, { backgroundColor: c.ink }]} />
    </View>
  );

  return (
    <View style={{ gap: space.md }}>
      <View onLayout={onLayout} style={styles.track}>
        <View style={styles.frames}>
          {Array.from({ length: frames }, (_, i) => {
            const t = ((i + 0.5) / frames) * duration;
            const kept = t >= value.start && t <= value.end;
            return (
              <View
                key={i}
                style={{
                  width: FRAME_W,
                  height: 40,
                  borderRadius: radius.frame,
                  backgroundColor: kept ? c.accent : c.frame,
                }}
              />
            );
          })}
        </View>
        {width ? handle("start") : null}
        {width ? handle("end") : null}
      </View>

      <View style={styles.ruler} importantForAccessibility="no-hide-descendants">
        {ticks.map((t) => (
          <Text
            key={t}
            variant="figures"
            tone="muted"
            style={[styles.tick, { left: Math.min(x(t), width - 40) }]}
          >
            {formatDuration(t)}
          </Text>
        ))}
      </View>

      <View style={styles.inputs}>
        <TimeField
          label="Start"
          seconds={value.start}
          onCommit={(s) => onChange(clampRange(value, "start", s, duration))}
        />
        <TimeField
          label="End"
          seconds={value.end}
          onCommit={(s) => onChange(clampRange(value, "end", s, duration))}
        />
        <View style={styles.length}>
          <Text variant="label" tone="muted">
            Length
          </Text>
          <Text variant="figuresLarge">{formatDuration(value.end - value.start)}</Text>
        </View>
      </View>
    </View>
  );
}

function TimeField({
  label,
  seconds,
  onCommit,
}: {
  label: string;
  seconds: number;
  onCommit: (s: number) => void;
}) {
  const [text, setText] = useState(formatDuration(seconds));
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    setText(formatDuration(seconds));
    setError(null);
  }, [seconds]);

  const commit = () => {
    const parsed = parseTime(text);
    if (!parsed) {
      setError("Use 5:23, 1:05:30 or 5m23s");
      return;
    }
    setError(null);
    onCommit(toSeconds(parsed));
  };

  return (
    <Field
      label={label}
      value={text}
      onChangeText={setText}
      onBlur={commit}
      onSubmitEditing={commit}
      error={error}
      figures
      inputMode="text"
      autoCorrect={false}
      autoCapitalize="none"
      containerStyle={{ flex: 1, minWidth: 110 }}
    />
  );
}

function clampRange(v: Range, edge: "start" | "end", sec: number, duration: number): Range {
  if (edge === "start") {
    return { ...v, start: Math.max(0, Math.min(sec, v.end - MIN_LEN)) };
  }
  return { ...v, end: Math.min(duration, Math.max(sec, v.start + MIN_LEN)) };
}

const styles = StyleSheet.create({
  track: { height: 40, justifyContent: "center" },
  frames: { flexDirection: "row", gap: FRAME_GAP, overflow: "hidden" },
  handleHit: {
    position: "absolute",
    top: -6,
    bottom: -6,
    width: 32,
    alignItems: "center",
    justifyContent: "center",
  },
  handle: { width: 4, height: 52, borderRadius: 2 },
  ruler: { height: 20, position: "relative" },
  tick: { position: "absolute", top: 0 },
  inputs: { flexDirection: "row", gap: space.md, alignItems: "flex-start", flexWrap: "wrap" },
  length: { gap: space.xs, paddingTop: 2, minWidth: 80 },
});
