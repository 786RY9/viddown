import { useEffect, useMemo, useState } from "react";
import { StyleSheet, View, type LayoutChangeEvent } from "react-native";
import { useReducedMotion } from "react-native-reanimated";
import { phaseSegments } from "@viddown/core";

import { useTheme } from "@/theme/theme";
import { radius } from "@/theme/tokens";

export interface FrameSegment {
  /** Relative share of the bar. */
  weight: number;
  label: string;
}

interface Props {
  /** 0..1, or null for "working, amount unknown". */
  fraction: number | null;
  /** Phases drawn as separate runs of frames, e.g. video / audio / merge. */
  segments?: FrameSegment[];
  size?: "sm" | "lg";
  /** Grey instead of yellow (paused / failed). */
  muted?: boolean;
  accessibilityLabel?: string;
}

const SIZES = {
  sm: { w: 3, gap: 2, h: 12, segGap: 5 },
  lg: { w: 5, gap: 2, h: 22, segGap: 8 },
} as const;

/**
 * viddown's signature element: progress drawn as a strip of film frames.
 * Each phase of a job (video, audio, merge) is its own run of frames, so you
 * can see *where* a download is, not just how far along it is.
 */
export function FrameBar({ fraction, segments, size = "sm", muted, accessibilityLabel }: Props) {
  const { c } = useTheme();
  const reduced = useReducedMotion();
  const [width, setWidth] = useState(0);
  const dims = SIZES[size];
  const segs = segments?.length ? segments : [{ weight: 1, label: "" }];

  const runs = useMemo(() => {
    if (!width) return [];
    const total = segs.reduce((s, x) => s + x.weight, 0) || 1;
    const usable = width - dims.segGap * (segs.length - 1);
    const frames = Math.max(segs.length, Math.floor((usable + dims.gap) / (dims.w + dims.gap)));
    let start = 0;
    return segs.map((s) => {
      const n = Math.max(1, Math.round((frames * s.weight) / total));
      const run = { label: s.label, start, n, weightStart: 0 };
      start += n;
      return run;
    });
  }, [width, segs, dims]);

  const totalFrames = runs.reduce((s, r) => s + r.n, 0);
  const filled = fraction == null ? 0 : Math.min(1, Math.max(0, fraction)) * totalFrames;

  // Indeterminate: a short travelling cluster of frames, unless motion is reduced.
  const [tick, setTick] = useState(0);
  useEffect(() => {
    if (fraction != null || reduced || !totalFrames) return;
    const t = setInterval(() => setTick((x) => (x + 1) % totalFrames), 60);
    return () => clearInterval(t);
  }, [fraction, reduced, totalFrames]);

  const fill = muted ? c.inkMuted : c.accent;

  const onLayout = (e: LayoutChangeEvent) => {
    const w = Math.floor(e.nativeEvent.layout.width);
    if (w !== width) setWidth(w);
  };

  return (
    <View
      onLayout={onLayout}
      style={[styles.bar, { height: dims.h, gap: dims.segGap }]}
      accessibilityRole="progressbar"
      accessibilityLabel={accessibilityLabel}
      accessibilityValue={
        fraction == null ? { text: "Working" } : { min: 0, max: 100, now: Math.round(fraction * 100) }
      }
    >
      {runs.map((run) => (
        <View key={run.label + run.start} style={[styles.run, { gap: dims.gap }]}>
          {Array.from({ length: run.n }, (_, k) => {
            const i = run.start + k;
            let opacity = 0;
            if (fraction == null) {
              const d = (i - tick + totalFrames) % totalFrames;
              opacity = reduced ? 0 : d < 6 ? 1 - d / 6 : 0;
            } else if (i + 1 <= filled) opacity = 1;
            else if (i < filled) opacity = filled - i;
            return (
              <View
                key={k}
                style={{ width: dims.w, height: dims.h, borderRadius: radius.frame, backgroundColor: c.frame }}
              >
                {opacity > 0 && (
                  <View style={[StyleSheet.absoluteFill, { backgroundColor: fill, opacity, borderRadius: radius.frame }]} />
                )}
              </View>
            );
          })}
        </View>
      ))}
    </View>
  );
}

/** Phase runs for a job, aligned with ProgressTracker's fractions. */
export const jobSegments = (kind: "video" | "audio"): FrameSegment[] => phaseSegments(kind);

const styles = StyleSheet.create({
  bar: { flexDirection: "row", width: "100%", overflow: "hidden" },
  run: { flexDirection: "row" },
});
