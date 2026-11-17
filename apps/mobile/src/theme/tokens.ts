/**
 * viddown design tokens.
 *
 * Direction: a precise instrument for getting video onto your own device.
 * Borrowed from film stock and editing suites rather than SaaS dashboards:
 * aluminium greys, one film-stock yellow used only as a *fill* (with ink on
 * top), and timecode-style figures set in Archivo Narrow tabular numerals.
 */

export type Scheme = "light" | "dark";

export interface Palette {
  /** App background. */
  bg: string;
  /** Raised surfaces: rail, panels, inputs. */
  surface: string;
  /** Recessed wells: text fields, empty frames on light surfaces. */
  sunken: string;
  ink: string;
  /** Secondary text. Meets 4.5:1 on bg and surface. */
  inkMuted: string;
  /** Hairlines and dividers. */
  rule: string;
  /** Film-stock yellow. Fill only; put `onAccent` text on it. */
  accent: string;
  accentPressed: string;
  onAccent: string;
  /** Empty frames in the frame bar. */
  frame: string;
  danger: string;
  success: string;
  /** Keyboard focus ring. */
  focus: string;
  scrim: string;
}

export const palettes: Record<Scheme, Palette> = {
  light: {
    bg: "#EEF0F2",
    surface: "#FFFFFF",
    sunken: "#E3E6EA",
    ink: "#16181D",
    inkMuted: "#555C69",
    rule: "#D6DAE0",
    accent: "#F5C400",
    accentPressed: "#DDB000",
    onAccent: "#16181D",
    frame: "#D3D7DD",
    danger: "#B3311A",
    success: "#1D7347",
    focus: "#16181D",
    scrim: "rgba(22,24,29,0.32)",
  },
  dark: {
    bg: "#1E2024",
    surface: "#272A2F",
    sunken: "#1A1C1F",
    ink: "#ECEDEF",
    inkMuted: "#9EA4AE",
    rule: "#383C43",
    accent: "#F5C400",
    accentPressed: "#FFD633",
    onAccent: "#16181D",
    frame: "#3B3F46",
    danger: "#FF8466",
    success: "#62C990",
    focus: "#F5C400",
    scrim: "rgba(0,0,0,0.5)",
  },
};

/** 4-pt spacing scale. */
export const space = {
  xxs: 2,
  xs: 4,
  sm: 8,
  md: 12,
  lg: 16,
  xl: 24,
  xxl: 32,
  xxxl: 48,
} as const;

/** Radius follows hierarchy: big containers soften, small controls stay crisp. */
export const radius = {
  frame: 1,
  chip: 6,
  control: 8,
  field: 12,
  panel: 14,
} as const;

export const fonts = {
  regular: "Archivo_400Regular",
  medium: "Archivo_500Medium",
  semibold: "Archivo_600SemiBold",
  bold: "Archivo_700Bold",
  heavy: "Archivo_800ExtraBold",
  figures: "ArchivoNarrow_500Medium",
  figuresBold: "ArchivoNarrow_600SemiBold",
} as const;

/**
 * Classical scale (12 · 14 · 16 · 18 · 24 · 36), Bringhurst-style.
 * `figures*` are for numbers that change: timecodes, sizes, speeds, counts.
 */
export const type = {
  display: { fontFamily: fonts.heavy, fontSize: 36, lineHeight: 40, letterSpacing: -0.8 },
  title: { fontFamily: fonts.bold, fontSize: 24, lineHeight: 30, letterSpacing: -0.3 },
  heading: { fontFamily: fonts.semibold, fontSize: 18, lineHeight: 24, letterSpacing: -0.1 },
  body: { fontFamily: fonts.regular, fontSize: 16, lineHeight: 24 },
  bodyStrong: { fontFamily: fonts.semibold, fontSize: 16, lineHeight: 24 },
  label: { fontFamily: fonts.medium, fontSize: 14, lineHeight: 20 },
  small: { fontFamily: fonts.regular, fontSize: 14, lineHeight: 20 },
  caption: { fontFamily: fonts.regular, fontSize: 12, lineHeight: 16 },
  figures: { fontFamily: fonts.figures, fontSize: 14, lineHeight: 20, fontVariant: ["tabular-nums"] },
  figuresLarge: { fontFamily: fonts.figuresBold, fontSize: 18, lineHeight: 24, fontVariant: ["tabular-nums"] },
} as const;

export type TypeVariant = keyof typeof type;

/** Layout breakpoints (dp / CSS px). */
export const layout = {
  /** Rail navigation instead of bottom bar. */
  wide: 840,
  /** Download screen shows the queue alongside. */
  split: 1180,
  railWidth: 208,
  contentMax: 720,
  sidePanel: 380,
} as const;

export const motion = {
  quick: 120,
  base: 200,
} as const;
