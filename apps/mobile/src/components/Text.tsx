import { Text as RNText, type TextProps } from "react-native";

import { useTheme } from "@/theme/theme";
import { type as typeScale, type TypeVariant } from "@/theme/tokens";

interface Props extends TextProps {
  variant?: TypeVariant;
  tone?: "ink" | "muted" | "danger" | "success" | "onAccent";
}

export function Text({ variant = "body", tone = "ink", style, ...rest }: Props) {
  const { c } = useTheme();
  const color = {
    ink: c.ink,
    muted: c.inkMuted,
    danger: c.danger,
    success: c.success,
    onAccent: c.onAccent,
  }[tone];
  return <RNText {...rest} style={[typeScale[variant], { color }, style]} />;
}
