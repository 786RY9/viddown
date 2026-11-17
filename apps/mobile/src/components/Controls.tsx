import { forwardRef, type ReactNode } from "react";
import {
  Platform,
  StyleSheet,
  TextInput,
  View,
  type TextInputInstance,
  type TextInputProps,
  type ViewStyle,
} from "react-native";

import { useTheme } from "@/theme/theme";
import { fonts, radius, space, type as typeScale } from "@/theme/tokens";
import { Pressable } from "./Pressable";
import { Text } from "./Text";

// ── Text field ────────────────────────────────────────────────────────────

interface FieldProps extends TextInputProps {
  label?: string;
  hint?: string;
  error?: string | null;
  size?: "md" | "lg";
  trailing?: ReactNode;
  containerStyle?: ViewStyle;
  figures?: boolean;
}

export const Field = forwardRef<TextInputInstance, FieldProps>(function Field(
  { label, hint, error, size = "md", trailing, containerStyle, figures, style, ...rest },
  ref,
) {
  const { c } = useTheme();
  return (
    <View style={[{ gap: space.xs }, containerStyle]}>
      {label ? <Text variant="label">{label}</Text> : null}
      <View
        style={[
          styles.field,
          size === "lg" && styles.fieldLg,
          { backgroundColor: c.surface, borderColor: error ? c.danger : c.rule },
        ]}
      >
        <TextInput
          ref={ref}
          placeholderTextColor={c.inkMuted}
          selectionColor={c.accent}
          cursorColor={c.ink}
          accessibilityLabel={rest.accessibilityLabel ?? label}
          {...rest}
          style={[
            size === "lg" ? styles.inputLg : typeScale.body,
            figures && { fontFamily: fonts.figures, fontVariant: ["tabular-nums"] },
            styles.input,
            { color: c.ink },
            Platform.OS === "web" && ({ outlineStyle: "none" } as object),
            style,
          ]}
        />
        {trailing}
      </View>
      {error ? (
        <Text variant="small" tone="danger" accessibilityLiveRegion="polite">
          {error}
        </Text>
      ) : hint ? (
        <Text variant="small" tone="muted">
          {hint}
        </Text>
      ) : null}
    </View>
  );
});

// ── Segmented control ─────────────────────────────────────────────────────

export function Segmented<T extends string>({
  value,
  options,
  onChange,
  label,
}: {
  value: T;
  options: { value: T; label: string }[];
  onChange: (v: T) => void;
  label: string;
}) {
  const { c } = useTheme();
  return (
    <View
      accessibilityRole="radiogroup"
      accessibilityLabel={label}
      style={[styles.segmented, { backgroundColor: c.sunken }]}
    >
      {options.map((o) => {
        const on = o.value === value;
        return (
          <Pressable
            key={o.value}
            accessibilityRole="radio"
            accessibilityState={{ checked: on }}
            onPress={() => onChange(o.value)}
            style={[styles.segment, on && { backgroundColor: c.surface, borderColor: c.rule }]}
          >
            <Text variant="label" tone={on ? "ink" : "muted"} numberOfLines={1}>
              {o.label}
            </Text>
          </Pressable>
        );
      })}
    </View>
  );
}

// ── Switch ────────────────────────────────────────────────────────────────

/** A drawn switch so it looks the same on Android, Windows, macOS and Linux. */
export function Toggle({
  value,
  onChange,
  label,
}: {
  value: boolean;
  onChange: (v: boolean) => void;
  label: string;
}) {
  const { c } = useTheme();
  return (
    <Pressable
      accessibilityRole="switch"
      accessibilityLabel={label}
      accessibilityState={{ checked: value }}
      onPress={() => onChange(!value)}
      hitSlop={8}
      style={[styles.track, { backgroundColor: value ? c.accent : c.frame }]}
    >
      <View
        style={[
          styles.thumb,
          { backgroundColor: value ? c.onAccent : c.surface, transform: [{ translateX: value ? 18 : 0 }] },
        ]}
      />
    </Pressable>
  );
}

// ── Settings row ──────────────────────────────────────────────────────────

/** Label + explanation on the left, control on the right; stacks when narrow. */
export function SettingRow({
  title,
  description,
  control,
  stacked,
}: {
  title: string;
  description?: string;
  control: ReactNode;
  stacked?: boolean;
}) {
  const { c } = useTheme();
  return (
    <View style={[styles.settingRow, stacked && styles.stacked, { borderTopColor: c.rule }]}>
      <View style={styles.settingText}>
        <Text variant="bodyStrong">{title}</Text>
        {description ? (
          <Text variant="small" tone="muted">
            {description}
          </Text>
        ) : null}
      </View>
      <View style={stacked ? undefined : styles.settingControl}>{control}</View>
    </View>
  );
}

/** Section heading inside a screen. Sentence case, no eyebrow labels. */
export function SectionTitle({ children, aside }: { children: string; aside?: ReactNode }) {
  return (
    <View style={styles.sectionTitle}>
      <Text variant="heading" accessibilityRole="header">
        {children}
      </Text>
      {aside}
    </View>
  );
}

const styles = StyleSheet.create({
  field: {
    flexDirection: "row",
    alignItems: "center",
    borderWidth: 1,
    borderRadius: radius.field,
    paddingHorizontal: space.md,
    minHeight: 44,
    gap: space.sm,
  },
  fieldLg: { minHeight: 64, paddingLeft: space.lg, paddingRight: space.sm },
  input: { flex: 1, paddingVertical: space.sm, minWidth: 0 },
  inputLg: { fontFamily: fonts.medium, fontSize: 20, lineHeight: 26 },
  segmented: {
    flexDirection: "row",
    borderRadius: radius.control,
    padding: 3,
    gap: 3,
    alignSelf: "flex-start",
  },
  segment: {
    paddingHorizontal: space.md,
    paddingVertical: 6,
    borderRadius: radius.chip,
    borderWidth: 1,
    borderColor: "transparent",
  },
  track: { width: 44, height: 26, borderRadius: 13, padding: 3, justifyContent: "center" },
  thumb: { width: 20, height: 20, borderRadius: 10 },
  settingRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: space.lg,
    paddingVertical: space.lg,
    borderTopWidth: StyleSheet.hairlineWidth * 2,
  },
  stacked: { flexDirection: "column", alignItems: "stretch", gap: space.md },
  settingText: { flex: 1, gap: 2 },
  settingControl: { flexShrink: 0 },
  sectionTitle: {
    flexDirection: "row",
    alignItems: "baseline",
    justifyContent: "space-between",
    marginTop: space.xxl,
    marginBottom: space.sm,
    gap: space.md,
  },
});
