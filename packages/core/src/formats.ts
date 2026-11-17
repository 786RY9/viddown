import type { YtFormat } from "./types";

/**
 * One format per resolution, best first. Mirrors
 * cli/viddown_core/formats.py (checked against /fixtures/filter_formats.json).
 *
 * Within a resolution, prefer direct HTTP/DASH over HLS (m3u8 is much slower
 * to download); between two of the same kind, keep the higher bitrate.
 */
export function filterVideoFormats(formats: readonly YtFormat[]): YtFormat[] {
  const seen = new Map<number, YtFormat>();
  for (const fmt of formats) {
    if ((fmt.vcodec ?? "none") === "none" || !fmt.height) continue;
    const h = fmt.height;
    const isM3u8 = (fmt.protocol ?? "").includes("m3u8");

    const prev = seen.get(h);
    if (!prev) {
      seen.set(h, fmt);
      continue;
    }
    const prevIsM3u8 = (prev.protocol ?? "").includes("m3u8");
    if (prevIsM3u8 && !isM3u8) seen.set(h, fmt);
    else if (prevIsM3u8 === isM3u8 && (fmt.tbr ?? 0) > (prev.tbr ?? 0)) seen.set(h, fmt);
  }
  return [...seen.values()].sort((a, b) => (b.height ?? 0) - (a.height ?? 0));
}

/** Best size estimate in bytes for a video format merged with an audio track. */
export function estimateSize(
  fmt: YtFormat,
  durationSec?: number | null,
  audioKbps = 128,
): number | null {
  const own = fmt.filesize ?? fmt.filesize_approx;
  const audio = durationSec ? (audioKbps * 1000 * durationSec) / 8 : 0;
  if (own) return own + audio;
  if (fmt.tbr && durationSec) return (fmt.tbr * 1000 * durationSec) / 8 + audio;
  return null;
}

/** "1080p60" style label. */
export function qualityLabel(fmt: YtFormat): string {
  const fps = fmt.fps && fmt.fps > 30 ? Math.round(fmt.fps) : "";
  return `${fmt.height ?? "?"}p${fps}`;
}

/** Friendly codec family: "H.264", "VP9", "AV1". */
export function codecLabel(vcodec?: string | null): string {
  const c = (vcodec ?? "").toLowerCase();
  if (c.startsWith("avc") || c.startsWith("h264")) return "H.264";
  if (c.startsWith("hev") || c.startsWith("hvc") || c.startsWith("h265")) return "HEVC";
  if (c.startsWith("vp9") || c.startsWith("vp09")) return "VP9";
  if (c.startsWith("av01") || c.startsWith("av1")) return "AV1";
  return c ? c.split(".")[0]!.toUpperCase() : "—";
}
