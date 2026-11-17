import { createContext, useContext, useMemo, type ReactNode } from "react";
import { useColorScheme, useWindowDimensions } from "react-native";

import { useSettings } from "@/state/settings";
import { layout, palettes, type Palette, type Scheme } from "./tokens";

interface Theme {
  scheme: Scheme;
  c: Palette;
}

const ThemeContext = createContext<Theme>({ scheme: "light", c: palettes.light });

export function ThemeProvider({ children }: { children: ReactNode }) {
  const system = useColorScheme();
  const { appearance } = useSettings();
  const scheme: Scheme =
    appearance === "system" ? (system === "dark" ? "dark" : "light") : appearance;
  const value = useMemo(() => ({ scheme, c: palettes[scheme] }), [scheme]);
  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}

export const useTheme = () => useContext(ThemeContext);

/** Responsive flags shared by every screen. */
export function useLayout() {
  const { width } = useWindowDimensions();
  return {
    width,
    wide: width >= layout.wide,
    split: width >= layout.split,
  };
}
