import { useSyncExternalStore } from "react";
import { Platform } from "react-native";

import { loadJSON, saveJSON } from "./persist";

export interface Settings {
  appearance: "system" | "light" | "dark";
  /** Empty = engine default (Downloads/viddown). Path on desktop, content:// URI on Android. */
  outputDir: string;
  /** Display name for outputDir (Android folder names aren't paths). */
  outputLabel: string;
  /** Downloads running at the same time. */
  concurrency: number;
  /** Android: only download on unmetered networks. */
  wifiOnly: boolean;
  /** Use the viddown server when a site blocks this device. */
  cloudFallback: boolean;
  /** Check for yt-dlp updates on launch (at most daily). */
  autoUpdateEngine: boolean;
}

const KEY = "viddown.settings.v1";

export const defaultSettings: Settings = {
  appearance: "system",
  outputDir: "",
  outputLabel: "",
  concurrency: Platform.OS === "android" ? 2 : 3,
  wifiOnly: false,
  cloudFallback: true,
  autoUpdateEngine: true,
};

let current: Settings = defaultSettings;
const listeners = new Set<() => void>();

export const settingsStore = {
  get: () => current,
  subscribe(fn: () => void) {
    listeners.add(fn);
    return () => {
      listeners.delete(fn);
    };
  },
  set(patch: Partial<Settings>) {
    current = { ...current, ...patch };
    listeners.forEach((fn) => fn());
    void saveJSON(KEY, current);
  },
  async hydrate() {
    current = { ...defaultSettings, ...(await loadJSON<Partial<Settings>>(KEY, {})) };
    listeners.forEach((fn) => fn());
  },
};

export function useSettings(): Settings {
  return useSyncExternalStore(settingsStore.subscribe, settingsStore.get, settingsStore.get);
}
