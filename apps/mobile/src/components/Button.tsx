import { ActivityIndicator, StyleSheet, View, type ViewStyle } from "react-native";

import { useTheme } from "@/theme/theme";
import { radius, space } from "@/theme/tokens";
import { Icon, type IconName } from "./Icon";
import { Pressable } from "./Pressable";
import { Text } from "./Text";

type Kind = "primary" | "secondary" | "quiet";

interface Props {
  label: string;
  onPress?: () => void;
  kind?: Kind;
  icon?: IconName;
  disabled?: boolean;
  busy?: boolean;
  size?: "md" | "lg";
  style?: ViewStyle;
  accessibilityHint?: string;
}

export function Button({
  label,
  onPress,
  kind = "secondary",
  icon,
  disabled,
  busy,
  size = "md",
  style,
  accessibilityHint,
}: Props) {
  const { c } = useTheme();
  const fg = kind === "primary" ? c.onAccent : c.ink;
  const inactive = disabled || busy;

  return (
    <Pressable
      accessibilityRole="button"
      accessibilityLabel={label}
      accessibilityHint={accessibilityHint}
      accessibilityState={{ disabled: !!inactive, busy: !!busy }}
      disabled={inactive}
      onPress={onPress}
      style={({ pressed, hovered }) => [
        styles.base,
        size === "lg" && styles.lg,
        kind === "primary" && { backgroundColor: pressed ? c.accentPressed : c.accent },
        kind === "secondary" && {
          backgroundColor: pressed || hovered ? c.sunken : c.surface,
          borderColor: c.rule,
          borderWidth: StyleSheet.hairlineWidth * 2,
        },
        kind === "quiet" && { backgroundColor: pressed || hovered ? c.sunken : "transparent" },
        inactive && styles.disabled,
        style,
      ]}
    >
      <View style={styles.row}>
        {busy ? (
          <ActivityIndicator size="small" color={fg} />
        ) : icon ? (
          <Icon name={icon} size={size === "lg" ? 20 : 18} color={fg} />
        ) : null}
        <Text variant={size === "lg" ? "bodyStrong" : "label"} style={{ color: fg }} numberOfLines={1}>
          {label}
        </Text>
      </View>
    </Pressable>
  );
}

export function IconButton({
  icon,
  label,
  onPress,
  tone,
}: {
  icon: IconName;
  /** Required: announced by screen readers and shown as a tooltip on desktop. */
  label: string;
  onPress: () => void;
  tone?: "danger";
}) {
  const { c } = useTheme();
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityLabel={label}
      // Tooltip on web/desktop.
      {...({ title: label } as object)}
      onPress={onPress}
      hitSlop={6}
      style={({ pressed, hovered }) => [
        styles.icon,
        (pressed || hovered) && { backgroundColor: c.sunken },
      ]}
    >
      <Icon name={icon} size={18} color={tone === "danger" ? c.danger : c.ink} />
    </Pressable>
  );
}

const styles = StyleSheet.create({
  base: {
    minHeight: 40,
    paddingHorizontal: space.lg,
    borderRadius: radius.control,
    justifyContent: "center",
  },
  lg: { minHeight: 52, paddingHorizontal: space.xl },
  row: { flexDirection: "row", alignItems: "center", justifyContent: "center", gap: space.sm },
  disabled: { opacity: 0.45 },
  icon: {
    width: 36,
    height: 36,
    borderRadius: radius.control,
    alignItems: "center",
    justifyContent: "center",
  },
});
