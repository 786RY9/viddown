/**
 * Flexible time input -> "HH:MM:SS". Mirrors cli/viddown_core/timeparse.py;
 * both are checked against /fixtures/parse_time.json.
 * Accepts: 5:23 · 1:05:30 · 5m23s · 1h5m30s · 323 (seconds) · 30s
 */
const COLON_RE = /^(\d+):(\d{2})(?::(\d{2}))?$/;
const UNITS_RE = /^(?:(\d+)\s*h)?\s*(?:(\d+)\s*m(?:in)?)?\s*(?:(\d+)\s*s(?:ec)?)?$/;

const pad = (n: number) => String(n).padStart(2, "0");
const hms = (h: number, m: number, s: number) => `${pad(h)}:${pad(m)}:${pad(s)}`;

export function parseTime(raw: string): string | null {
  const input = raw.trim().toLowerCase();

  if (/^\d+$/.test(input)) return formatClock(Number(input));

  const colon = COLON_RE.exec(input);
  if (colon) {
    const [, a, b, c] = colon;
    return c !== undefined
      ? hms(Number(a), Number(b), Number(c))
      : hms(0, Number(a), Number(b));
  }

  const units = UNITS_RE.exec(input);
  if (units && (units[1] || units[2] || units[3])) {
    return hms(Number(units[1] ?? 0), Number(units[2] ?? 0), Number(units[3] ?? 0));
  }
  return null;
}

/** "HH:MM:SS" (as returned by parseTime) -> total seconds. */
export function toSeconds(t: string): number {
  const [h = 0, m = 0, s = 0] = t.split(":").map(Number);
  return h * 3600 + m * 60 + s;
}

/** Total seconds -> zero-padded "HH:MM:SS". */
export function formatClock(totalSeconds: number): string {
  const t = Math.max(0, Math.floor(totalSeconds));
  return hms(Math.floor(t / 3600), Math.floor((t % 3600) / 60), t % 60);
}

/** Human display: 83 -> "1:23", 3930 -> "1:05:30". */
export function formatDuration(totalSeconds: number | null | undefined): string {
  if (totalSeconds == null || !Number.isFinite(totalSeconds)) return "--:--";
  const t = Math.max(0, Math.round(totalSeconds));
  const h = Math.floor(t / 3600);
  const m = Math.floor((t % 3600) / 60);
  const s = t % 60;
  return h > 0 ? `${h}:${pad(m)}:${pad(s)}` : `${m}:${pad(s)}`;
}
