import { StyleSheet, View } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { TabList, TabSlot, TabTrigger, Tabs, type TabTriggerSlotProps } from "expo-router/ui";

import { useQueueSummary } from "@/state/downloads";
import { useLayout, useTheme } from "@/theme/theme";
import { fonts, layout, radius, space } from "@/theme/tokens";
import { Icon, type IconName } from "./Icon";
import { Pressable } from "./Pressable";
import { Text } from "./Text";
import { ShareIntentHandler } from "./ShareIntentHandler";
import { ToastHost } from "./Toast";

/**
 * One navigation shell for every platform: a labelled rail on wide windows
 * (desktop, tablets) and a bottom bar on phones. Same routes, same order.
 */
export function Shell() {
  const { c } = useTheme();
  const { wide } = useLayout();
  const insets = useSafeAreaInsets();
  const { active } = useQueueSummary();
  const badge = active.length || undefined;

  return (
    <>
      <Tabs style={[styles.root, { backgroundColor: c.bg, flexDirection: wide ? "row" : "column-reverse" }]}>
        <TabList
          style={
            wide
              ? [styles.rail, { backgroundColor: c.surface, borderRightColor: c.rule }]
              : [styles.bar, { backgroundColor: c.surface, borderTopColor: c.rule, paddingBottom: insets.bottom }]
          }
        >
          {wide ? <Wordmark /> : null}
          <TabTrigger name="index" href="/" asChild>
            <NavItem icon="download" label="Download" wide={wide} />
          </TabTrigger>
          <TabTrigger name="queue" href="/queue" asChild>
            <NavItem icon="queue" label="Queue" wide={wide} badge={badge} />
          </TabTrigger>
          <TabTrigger name="history" href="/history" asChild>
            <NavItem icon="history" label="History" wide={wide} />
          </TabTrigger>
          <TabTrigger name="settings" href="/settings" asChild>
            <NavItem icon="settings" label="Settings" wide={wide} />
          </TabTrigger>
          <TabTrigger name="about" href="/about" asChild>
            <NavItem icon="person" label="About" wide={wide} hidden={!wide} />
          </TabTrigger>
          {wide ? (
            <View style={styles.railFoot}>
              <Text variant="caption" tone="muted">
                Free and open source.
              </Text>
              <Text variant="caption" tone="muted">
                A Dayframe production.
              </Text>
            </View>
          ) : null}
        </TabList>
        <TabSlot style={{ flex: 1 }} />
      </Tabs>
      <ShareIntentHandler />
      <ToastHost />
    </>
  );
}

function Wordmark() {
  const { c } = useTheme();
  return (
    <View style={styles.wordmark} accessible accessibilityRole="image" accessibilityLabel="viddown">
      <View style={styles.mark}>
        {[1, 1, 1, 0].map((on, i) => (
          <View key={i} style={[styles.markFrame, { backgroundColor: on ? c.accent : c.frame }]} />
        ))}
      </View>
      <Text style={{ fontFamily: fonts.heavy, fontSize: 22, letterSpacing: -0.6, color: c.ink }}>
        viddown
      </Text>
    </View>
  );
}

interface NavItemProps extends TabTriggerSlotProps {
  icon: IconName;
  label: string;
  wide: boolean;
  badge?: number;
  hidden?: boolean;
}

function NavItem({ icon, label, wide, badge, hidden, isFocused, ...props }: NavItemProps) {
  const { c } = useTheme();
  if (hidden) return null;
  const color = isFocused ? c.ink : c.inkMuted;
  return (
    <Pressable
      {...(props as object)}
      accessibilityRole="tab"
      accessibilityState={{ selected: !!isFocused }}
      accessibilityLabel={badge ? `${label}, ${badge} active` : label}
      style={({ pressed, hovered }) => [
        wide ? styles.railItem : styles.barItem,
        wide && (isFocused || hovered || pressed) && { backgroundColor: c.sunken },
      ]}
    >
      {/* Selected marker: a single yellow frame, like the frame bar. */}
      {isFocused ? (
        <View style={[wide ? styles.railMarker : styles.barMarker, { backgroundColor: c.accent }]} />
      ) : null}
      <Icon name={icon} size={wide ? 18 : 22} color={color} />
      <Text
        variant={wide ? "label" : "caption"}
        style={[{ color }, isFocused && { fontFamily: fonts.semibold }, wide && { flex: 1 }]}
      >
        {label}
      </Text>
      {badge ? (
        <View style={[styles.badge, !wide && styles.badgeBar, { backgroundColor: c.ink }]}>
          <Text variant="figures" style={{ color: c.bg, fontSize: 12, lineHeight: 16 }}>
            {badge}
          </Text>
        </View>
      ) : null}
    </Pressable>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1 },
  rail: {
    width: layout.railWidth,
    paddingHorizontal: space.md,
    paddingTop: space.xl,
    paddingBottom: space.lg,
    gap: 2,
    borderRightWidth: StyleSheet.hairlineWidth * 2,
    flexDirection: "column",
  },
  wordmark: { flexDirection: "row", alignItems: "center", gap: space.sm, paddingHorizontal: space.sm, marginBottom: space.xl },
  mark: { flexDirection: "row", gap: 2 },
  markFrame: { width: 4, height: 18, borderRadius: 1 },
  railItem: {
    flexDirection: "row",
    alignItems: "center",
    gap: space.md,
    paddingHorizontal: space.sm,
    height: 40,
    borderRadius: radius.control,
  },
  railMarker: { position: "absolute", left: -space.md, top: 10, bottom: 10, width: 4, borderRadius: 1 },
  railFoot: { marginTop: "auto", paddingHorizontal: space.sm, gap: 2 },
  bar: {
    flexDirection: "row",
    borderTopWidth: StyleSheet.hairlineWidth * 2,
  },
  barItem: {
    flex: 1,
    alignItems: "center",
    justifyContent: "center",
    gap: 2,
    paddingTop: space.sm,
    paddingBottom: space.sm,
    minHeight: 56,
  },
  barMarker: { position: "absolute", top: 0, width: 24, height: 3, borderRadius: 1 },
  badge: {
    minWidth: 20,
    height: 20,
    borderRadius: 10,
    paddingHorizontal: 5,
    alignItems: "center",
    justifyContent: "center",
  },
  badgeBar: { position: "absolute", top: 4, left: "50%", marginLeft: 6 },
});
