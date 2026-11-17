import type { ErrorKind, JobError } from "./types";

const RULES: [ErrorKind, RegExp][] = [
  ["unsupported", /unsupported url|no suitable extractor|is not a valid url/i],
  [
    "unavailable",
    /private video|video unavailable|has been removed|members-only|join this channel|premieres in|drm|this live event will begin|copyright|http error 404|http error 410/i,
  ],
  [
    "blocked",
    /sign in to confirm|not a bot|http error 403|http error 429|too many requests|not available in your country|geo.?restrict|login required|rate.?limit/i,
  ],
  [
    "network",
    /timed out|timeout|connection (reset|refused|aborted)|network is unreachable|temporary failure in name resolution|getaddrinfo|ssl|eof occurred|incompleteread|http error 5\d\d/i,
  ],
];

/** Map yt-dlp / ffmpeg error output to an actionable category. */
export function classifyError(message: string): JobError {
  const line = lastErrorLine(message);
  for (const [kind, re] of RULES) if (re.test(line)) return { kind, message: line };
  return { kind: "unknown", message: line };
}

function lastErrorLine(text: string): string {
  const lines = text.split(/\r?\n/).map((l) => l.trim()).filter(Boolean);
  const err = [...lines].reverse().find((l) => l.startsWith("ERROR:"));
  return (err ?? lines.at(-1) ?? "Unknown error").replace(/^ERROR:\s*/, "");
}

export const isRetryable = (e: JobError) => e.kind === "network" || e.kind === "unknown";

/** Plain-language explanation shown in the UI. */
export function explainError(e: JobError): string {
  switch (e.kind) {
    case "network":
      return "The connection dropped. Try again to continue from where it stopped.";
    case "blocked":
      return "The site refused this request. Updating the engine usually fixes this.";
    case "unavailable":
      return "This video is private, removed, or restricted.";
    case "unsupported":
      return "That link isn't a supported video page.";
    case "canceled":
      return "Canceled.";
    default:
      return e.message;
  }
}
