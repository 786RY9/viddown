import {
  Platform,
  Pressable as RNPressable,
  type PressableProps,
  type PressableStateCallbackType,
  type StyleProp,
  type ViewStyle,
} from "react-native";

import { useTheme } from "@/theme/theme";

/** react-native-web adds hovered/focused to the state object. */
export type PressState = PressableStateCallbackType & { hovered?: boolean; focused?: boolean };

interface Props extends Omit<PressableProps, "style"> {
  style?: StyleProp<ViewStyle> | ((s: PressState) => StyleProp<ViewStyle>);
  /** Draw the keyboard focus ring (web/desktop). Default true. */
  focusRing?: boolean;
}

/** Pressable with a visible keyboard focus ring on web/desktop. */
export function Pressable({ style, focusRing = true, ...rest }: Props) {
  const { c } = useTheme();
  return (
    <RNPressable
      {...rest}
      style={(state) => {
        const s = state as PressState;
        const base = typeof style === "function" ? style(s) : style;
        const ring: ViewStyle | null =
          focusRing && Platform.OS === "web" && s.focused
            ? ({ outlineColor: c.focus, outlineStyle: "solid", outlineWidth: 2, outlineOffset: 2 } as ViewStyle)
            : Platform.OS === "web"
              ? ({ outlineStyle: "none" } as unknown as ViewStyle)
              : null;
        return [base, ring];
      }}
    />
  );
}
