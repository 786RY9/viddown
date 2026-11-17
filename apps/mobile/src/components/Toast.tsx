import { useEffect, useState, useSyncExternalStore } from "react";
import { StyleSheet, View } from "react-native";
import Animated, { FadeInDown, FadeOut } from "react-native-reanimated";
import { useSafeAreaInsets } from "react-native-safe-area-context";

import { useLayout, useTheme } from "@/theme/theme";
import { radius, space } from "@/theme/tokens";
import { Pressable } from "./Pressable";
import { Text } from "./Text";

interface ToastMsg {
  id: number;
  text: string;
  action?: { label: string; onPress: () => void };
}

let current: ToastMsg | null = null;
let seq = 0;
const listeners = new Set<() => void>();
const emit = () => listeners.forEach((fn) => fn());

/** Show a short confirmation, e.g. toast("Added to queue", { label: "View", onPress }). */
export function toast(text: string, action?: ToastMsg["action"]) {
  current = { id: ++seq, text, action };
  emit();
}

export function ToastHost() {
  const { c } = useTheme();
  const { wide } = useLayout();
  const insets = useSafeAreaInsets();
  const msg = useSyncExternalStore(
    (fn) => {
      listeners.add(fn);
      return () => {
        listeners.delete(fn);
      };
    },
    () => current,
    () => current,
  );
  const [visible, setVisible] = useState<ToastMsg | null>(null);

  useEffect(() => {
    if (!msg) return;
    setVisible(msg);
    const t = setTimeout(() => setVisible((v) => (v?.id === msg.id ? null : v)), 3500);
    return () => clearTimeout(t);
  }, [msg]);

  if (!visible) return null;
  return (
    <View
      pointerEvents="box-none"
      style={[styles.host, { bottom: (wide ? space.xl : 72) + insets.bottom }]}
    >
      <Animated.View
        key={visible.id}
        entering={FadeInDown.duration(180)}
        exiting={FadeOut.duration(120)}
        accessibilityLiveRegion="polite"
        accessibilityRole="alert"
        style={[styles.toast, { backgroundColor: c.ink }]}
      >
        <Text variant="label" style={{ color: c.bg, flexShrink: 1 }}>
          {visible.text}
        </Text>
        {visible.action ? (
          <Pressable
            accessibilityRole="button"
            onPress={() => {
              visible.action?.onPress();
              setVisible(null);
            }}
            style={styles.action}
          >
            <Text variant="label" style={{ color: c.bg, textDecorationLine: "underline" }}>
              {visible.action.label}
            </Text>
          </Pressable>
        ) : null}
      </Animated.View>
    </View>
  );
}

const styles = StyleSheet.create({
  host: { position: "absolute", left: 0, right: 0, alignItems: "center", paddingHorizontal: space.lg },
  toast: {
    flexDirection: "row",
    alignItems: "center",
    gap: space.lg,
    paddingLeft: space.lg,
    paddingRight: space.sm,
    minHeight: 44,
    borderRadius: radius.control,
    maxWidth: 480,
  },
  action: { paddingHorizontal: space.sm, paddingVertical: space.sm },
});
