import type { ReactNode } from "react";
import { ScrollView, StyleSheet, View } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";

import { useLayout, useTheme } from "@/theme/theme";
import { layout, space } from "@/theme/tokens";
import { Text } from "./Text";

/** Scrollable screen with a left-aligned title and a readable max width. */
export function Screen({
  title,
  subtitle,
  aside,
  children,
  maxWidth = layout.contentMax,
  scroll = true,
}: {
  title: string;
  subtitle?: ReactNode;
  aside?: ReactNode;
  children: ReactNode;
  maxWidth?: number;
  scroll?: boolean;
}) {
  const { c } = useTheme();
  const { wide } = useLayout();
  const insets = useSafeAreaInsets();
  const pad = wide ? space.xxl : space.lg;

  const body = (
    <View style={[styles.inner, { maxWidth, paddingHorizontal: pad }]}>
      <View style={[styles.header, { paddingTop: (wide ? space.xxl : space.lg) + (wide ? 0 : insets.top) }]}>
        <View style={{ flex: 1, gap: space.xs }}>
          <Text variant="title" accessibilityRole="header">
            {title}
          </Text>
          {typeof subtitle === "string" ? (
            <Text variant="small" tone="muted">
              {subtitle}
            </Text>
          ) : (
            subtitle
          )}
        </View>
        {aside}
      </View>
      {children}
    </View>
  );

  return scroll ? (
    <ScrollView
      style={{ flex: 1, backgroundColor: c.bg }}
      contentContainerStyle={{ paddingBottom: space.xxxl }}
      keyboardShouldPersistTaps="handled"
    >
      {body}
    </ScrollView>
  ) : (
    <View style={{ flex: 1, backgroundColor: c.bg }}>{body}</View>
  );
}

const styles = StyleSheet.create({
  inner: { width: "100%", alignSelf: "flex-start" },
  header: {
    flexDirection: "row",
    alignItems: "flex-end",
    gap: space.lg,
    marginBottom: space.xl,
  },
});
