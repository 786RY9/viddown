import { Platform } from "react-native";
import Svg, { Path } from "react-native-svg";

import { useTheme } from "@/theme/theme";

/**
 * Hand-drawn 24px stroke icons, one weight (1.75) so they sit with Archivo.
 * Kept deliberately few; text labels do most of the work.
 */
const PATHS = {
  download: "M12 4v11m0 0-4.5-4.5M12 15l4.5-4.5M5 19.5h14",
  queue: "M4 6.5h16M4 12h16M4 17.5h10",
  history: "M12 7.5V12l3 2M3.75 12a8.25 8.25 0 1 0 2.4-5.82M3.75 4.5v3.75H7.5",
  settings: "M4 7h9m4 0h3M4 17h3m4 0h9M15 4.5v5M9 14.5v5",
  person: "M12 12a3.75 3.75 0 1 0 0-7.5 3.75 3.75 0 0 0 0 7.5ZM5 20c.8-3.5 3.6-5.25 7-5.25S18.2 16.5 19 20",
  pause: "M8.5 5.5v13M15.5 5.5v13",
  play: "M8 5.5v13l10.5-6.5L8 5.5Z",
  close: "M6 6l12 12M18 6 6 18",
  retry: "M19.5 12a7.5 7.5 0 1 1-2.2-5.3M19.5 4.5v3.75h-3.75",
  folder: "M3.75 7.5V18a1.5 1.5 0 0 0 1.5 1.5h13.5a1.5 1.5 0 0 0 1.5-1.5V9a1.5 1.5 0 0 0-1.5-1.5h-6.75L10.5 5.25H5.25a1.5 1.5 0 0 0-1.5 1.5Z",
  check: "M5 12.5 9.5 17 19 7.5",
  clipboard: "M9 4.5h6M9 4.5a1.5 1.5 0 0 0-1.5 1.5v.75h9V6A1.5 1.5 0 0 0 15 4.5M9 4.5H7.5A1.5 1.5 0 0 0 6 6v12.75a1.5 1.5 0 0 0 1.5 1.5h9a1.5 1.5 0 0 0 1.5-1.5V6a1.5 1.5 0 0 0-1.5-1.5H15",
  scissors: "M8.25 8.25 19.5 18M8.25 15.75 19.5 6M6 9.75a2.25 2.25 0 1 0 0-4.5 2.25 2.25 0 0 0 0 4.5Zm0 9a2.25 2.25 0 1 0 0-4.5 2.25 2.25 0 0 0 0 4.5Z",
  music: "M9 18V6.5l10.5-2v11M9 18a2.25 2.25 0 1 1-4.5 0A2.25 2.25 0 0 1 9 18Zm10.5-2.5a2.25 2.25 0 1 1-4.5 0 2.25 2.25 0 0 1 4.5 0Z",
  external: "M13.5 4.5h6v6M19.5 4.5 11 13M9 6H5.25a.75.75 0 0 0-.75.75v12c0 .41.34.75.75.75h12a.75.75 0 0 0 .75-.75V15",
  alert: "M12 8.5v4.5m0 3.25h.01M10.3 4.4 3.1 17a2 2 0 0 0 1.7 3h14.4a2 2 0 0 0 1.7-3L13.7 4.4a2 2 0 0 0-3.4 0Z",
} as const;

export type IconName = keyof typeof PATHS;

/** Icons are decorative; labels carry meaning. (Web uses aria-hidden.) */
const HIDDEN =
  Platform.OS === "web"
    ? ({ "aria-hidden": true } as object)
    : { accessibilityElementsHidden: true, importantForAccessibility: "no" as const };

export function Icon({
  name,
  size = 20,
  color,
}: {
  name: IconName;
  size?: number;
  color?: string;
}) {
  const { c } = useTheme();
  return (
    <Svg width={size} height={size} viewBox="0 0 24 24" fill="none" {...HIDDEN}>
      <Path
        d={PATHS[name]}
        stroke={color ?? c.ink}
        strokeWidth={1.75}
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </Svg>
  );
}
