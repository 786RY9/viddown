import { Platform } from "react-native";
import { MockEngine, type DownloadEngine } from "@viddown/core";

import { AndroidEngine, isAndroidEngineAvailable } from "./android";
import { isTauri, TauriEngine } from "./tauri";

/**
 * Picks the engine for this platform:
 *  - desktop (Tauri shell): yt-dlp + ffmpeg sidecars on this machine
 *  - Android (dev/release build with the native module): youtubedl-android
 *  - plain browser / Expo Go: MockEngine, so the UI can be worked on anywhere
 */
let engine: DownloadEngine | null = null;

export function getEngine(): DownloadEngine {
  if (!engine) {
    if (isTauri()) engine = new TauriEngine();
    else if (Platform.OS === "android" && isAndroidEngineAvailable()) engine = new AndroidEngine();
    else engine = new MockEngine();
  }
  return engine;
}

export const isRealEngine = () => !(getEngine() instanceof MockEngine);

/** Desktop-only extras (app updates, links), when running in Tauri. */
export function getDesktopEngine(): TauriEngine | null {
  const e = getEngine();
  return e instanceof TauriEngine ? e : null;
}

export function getAndroidEngine(): AndroidEngine | null {
  const e = getEngine();
  return e instanceof AndroidEngine ? e : null;
}

export interface FolderChoice {
  /** Stored in settings.outputDir: a path (desktop) or a content:// tree URI (Android). */
  value: string;
  /** What to show the user. */
  label: string;
}

/** Native folder picker on desktop and Android; null when cancelled or unsupported. */
export async function pickOutputFolder(current?: string): Promise<FolderChoice | null> {
  const desktop = getDesktopEngine();
  if (desktop) {
    const path = await desktop.pickFolder(current || undefined);
    return path ? { value: path, label: path } : null;
  }
  const android = getAndroidEngine();
  if (android) {
    const f = await android.pickFolder(current || null);
    return f ? { value: f.uri, label: f.name ?? "Chosen folder" } : null;
  }
  return null;
}

export const canPickFolder = () => !!(getDesktopEngine() || getAndroidEngine());

/** Server fallback engine, when enabled and reachable. */
export function getCloudEngine(): DownloadEngine | null {
  return null;
}
