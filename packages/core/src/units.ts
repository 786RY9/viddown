const UNITS = ["B", "KB", "MB", "GB", "TB"] as const;

/** 1_572_864 -> "1.5 MB" (binary multiples, labelled the way people expect). */
export function formatBytes(bytes: number | null | undefined, digits = 1): string {
  if (bytes == null || !Number.isFinite(bytes) || bytes < 0) return "—";
  let v = bytes;
  let i = 0;
  while (v >= 1024 && i < UNITS.length - 1) {
    v /= 1024;
    i++;
  }
  return `${i === 0 ? Math.round(v) : v.toFixed(digits)} ${UNITS[i]}`;
}

export function formatSpeed(bytesPerSec: number | null | undefined): string {
  return bytesPerSec ? `${formatBytes(bytesPerSec)}/s` : "—";
}
