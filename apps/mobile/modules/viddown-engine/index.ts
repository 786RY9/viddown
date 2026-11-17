import type { EventSubscription } from "expo";

import ViddownEngine from "./src/ViddownEngineModule";

export { default } from "./src/ViddownEngineModule";
export type { ViddownEngineNative } from "./src/ViddownEngineModule";
export * from "./src/ViddownEngine.types";

/** First http(s) URL in shared text ("Look at this https://youtu.be/x …"). */
export function extractUrl(text: string | null | undefined): string | null {
  if (!text) return null;
  const m = text.match(/https?:\/\/[^\s<>"'`]+/i);
  if (!m) return null;
  // Trailing punctuation from prose ("…see https://x.y/z).") isn't part of the link.
  return m[0].replace(/[).,;:!?\]}'"»]+$/, "");
}

/** Link shared into the app ("Share → viddown"), consumed once. Null elsewhere. */
export function takeSharedUrl(): string | null {
  return extractUrl(ViddownEngine?.takeSharedText() ?? null);
}

/**
 * Calls `cb` with each link shared while the app is already running. Pair with
 * takeSharedUrl() on mount for the link that launched the app.
 */
export function addSharedUrlListener(cb: (url: string) => void): EventSubscription {
  if (!ViddownEngine) return { remove() {} };
  return ViddownEngine.addListener("onShare", () => {
    const url = takeSharedUrl();
    if (url) cb(url);
  });
}
