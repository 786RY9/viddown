import type { JobSpec } from "./types";

/**
 * yt-dlp argument builders. RESUME_FLAGS/baseArgs/videoArgs/audioArgs mirror
 * cli/viddown_core/ytdlp_args.py; jobArgs adds the machine-readable output the
 * apps need instead of scraping human progress text.
 */
export const RESUME_FLAGS = [
  "--continue",
  "--concurrent-fragments", "8",
  "--fragment-retries", "infinite",
  "--retries", "10",
  "--retry-sleep", "5",
] as const;

export function baseArgs(
  url: string,
  outputTemplate: string,
  clip?: { start: string; end: string } | null,
): string[] {
  const args: string[] = [
    ...RESUME_FLAGS,
    "-o", outputTemplate,
    "--no-playlist",
    "--no-quiet",
    "--progress",
    "--newline",
  ];
  if (clip) {
    args.push("--download-sections", `*${clip.start}-${clip.end}`, "--force-keyframes-at-cuts");
  }
  args.push(url);
  return args;
}

export const videoArgs = (formatId: string) => [
  "-f", `${formatId}+bestaudio/best`,
  "--merge-output-format", "mp4",
];

export const audioArgs = () => [
  "-f", "bestaudio/best",
  "-x", "--audio-format", "mp3", "--audio-quality", "0",
];

/** Line prefixes emitted by jobArgs; parsed by progress.ts. */
export const MARK = {
  progress: "[vd:dl] ",
  postprocess: "[vd:pp] ",
  file: "[vd:file] ",
} as const;

export interface JobArgOptions {
  /** Parallel fragment downloads; lower on phones / metered networks. */
  concurrentFragments?: number;
  /** Absolute path to an ffmpeg binary or its directory. */
  ffmpegLocation?: string;
  /** Path separator for the output template ("/" or "\\"). */
  sep?: string;
}

export function outputTemplate(spec: JobSpec, sep = "/"): string {
  const dir = spec.outputDir.replace(/[\\/]+$/, "");
  const section = spec.clip ? " [%(section_start)s-%(section_end)s]" : "";
  const tag = spec.kind === "audio" ? "[audio]" : "[%(height)sp]";
  return `${dir}${sep}%(title).150B ${tag}${section}.%(ext)s`;
}

/** Full argv (excluding the yt-dlp executable) for one download job. */
export function jobArgs(spec: JobSpec, opts: JobArgOptions = {}): string[] {
  const kindArgs = spec.kind === "audio" ? audioArgs() : videoArgs(spec.formatId ?? "bestvideo");
  const args = [
    ...kindArgs,
    ...baseArgs(spec.url, outputTemplate(spec, opts.sep), spec.clip),
  ];
  const url = args.pop()!;

  const frags = String(opts.concurrentFragments ?? 8);
  const i = args.indexOf("--concurrent-fragments");
  if (i >= 0) args[i + 1] = frags;

  args.push(
    "--progress-template", `download:${MARK.progress}%(progress)j`,
    "--progress-template", `postprocess:${MARK.postprocess}%(progress.status)s %(progress.postprocessor)s`,
    "--print", `after_move:${MARK.file}%(filepath)s`,
    "--no-simulate",
    "--windows-filenames",
    "--no-mtime",
  );
  if (opts.ffmpegLocation) args.push("--ffmpeg-location", opts.ffmpegLocation);
  args.push("--", url);
  return args;
}

/** argv for metadata probing (single JSON document on stdout). */
export function probeArgs(url: string): string[] {
  return ["--dump-single-json", "--no-playlist", "--no-warnings", "--", url];
}
